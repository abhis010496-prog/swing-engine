"""
MONTHLY SHORTLIST
=================
Finds stocks that kept qualifying through the month, rather than the ones
that happen to qualify today.

A stock on the daily list has passed six tests once. A stock on the monthly
list has passed them 18 times out of 22. That is a different, and for a
two-month hold a more useful, statement.

Also writes a plain-English explanation for each — technical reasons and
sentiment reasons — so you know why it is there.

Run with:   python monthly.py
            python monthly.py --window 30
"""

import argparse
import json
import sqlite3
import time
from datetime import datetime

import numpy as np
import pandas as pd

DB = "market.db"
BENCHMARK = "^NSEI"

SCHEMA = """
DROP TABLE IF EXISTS monthly_shortlist;
CREATE TABLE monthly_shortlist (
    ticker          TEXT,
    period          TEXT,
    as_of           TEXT,
    name            TEXT,
    sector          TEXT,
    cap_bucket      TEXT,
    mcap            REAL,
    price           REAL,
    hits_30         INTEGER,   -- days qualified in the window
    days_30         INTEGER,   -- trading days in the window
    consistency     REAL,      -- hits / days, %
    hits_7          INTEGER,
    days_7          INTEGER,
    streak          INTEGER,   -- current unbroken run
    ret_1m          REAL,      -- % move over the window
    ret_3m          REAL,
    sector_ret_1m   REAL,      -- median move of its sector
    excess_vs_sector REAL,
    rs              REAL,
    from_high       REAL,
    atr_pct         REAL,
    turnover        REAL,
    vol_trend       REAL,      -- recent volume vs the month's average
    new_high        INTEGER,   -- made a new 52-week high in the window
    avg_score       REAL,
    stage           TEXT,      -- Early / Established / Extended / Fading
    tech_json       TEXT,
    senti_json      TEXT,
    outlook         TEXT,
    ma20            REAL,
    ma50            REAL,
    extension       REAL,   -- % above the 50-day average
    entry_verdict   TEXT,   -- Buy now / Wait for pullback / Do not chase
    entry_ideal     REAL,   -- the price you'd rather pay
    entry_ceiling   REAL,   -- above this the maths stops working
    stop_price      REAL,
    target_price    REAL,
    rr              REAL,   -- reward to risk
    entry_note      TEXT,
    fav_score       INTEGER,   -- 0-100, how favourable an entry looks now
    cont_score      INTEGER,   -- 0-100, how likely the move continues
    cont_band       TEXT,      -- High / Moderate / Low
    fc_low          REAL,      -- one-month range, low
    fc_high         REAL,
    fc_base         REAL,
    forecast        TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_ms_key
    ON monthly_shortlist(ticker, period);
CREATE INDEX IF NOT EXISTS ix_ms_cons ON monthly_shortlist(consistency DESC);
"""


def stage_of(consistency, ret_1m, from_high, extension, streak):
    """Where in the move are we? Drives whether an entry here is sensible."""
    if consistency < 40:
        return "Fading"
    if extension > 22 or (ret_1m > 40 and from_high > -3):
        return "Extended"
    if consistency >= 70 and streak >= 8:
        return "Established"
    return "Early"


def build_period(conn, window_days, min_hits_arg,
                 period_label, quiet=False):
    t0 = time.time()

    try:
        hist = pd.read_sql("SELECT * FROM shortlist_history", conn)
    except Exception:
        print("No shortlist_history. Run backfill_history.py first.")
        return
    if hist.empty:
        print("History is empty. Run backfill_history.py first.")
        return

    all_days = sorted(hist["as_of"].unique())
    window = all_days[-window_days:]
    week = all_days[-7:]
    as_of = all_days[-1]
    min_hits = min_hits_arg or max(2, int(len(window) * 0.5))

    print(f"Window: {len(window)} trading days ({window[0]} to {window[-1]})")
    print(f"A stock needs {min_hits}+ qualifying days to make the monthly list.\n")

    w = hist[hist["as_of"].isin(window)]
    counts = w.groupby("ticker").agg(hits=("as_of", "count"),
                                     avg_score=("score", "mean"))
    wk = hist[hist["as_of"].isin(week)].groupby("ticker").size()

    keep = counts[counts["hits"] >= min_hits]
    print(f"{len(keep)} stocks qualified at least {min_hits} times.\n")
    if keep.empty:
        print("Nothing qualifies. Try a lower --min-hits or a longer backfill.")
        return

    tickers = list(keep.index)

    # ---- prices for the window ----
    marks = ",".join("?" * len(tickers))
    px = pd.read_sql(
        f"SELECT ticker, date, close, volume FROM prices_daily "
        f"WHERE ticker IN ({marks}) AND date >= ? ORDER BY ticker, date",
        conn, params=tickers + [window[0]], parse_dates=["date"])

    inst = pd.read_sql(
        "SELECT ticker, name, sector, cap_bucket, market_cap_cr FROM instruments",
        conn).set_index("ticker")
    scores = pd.read_sql("SELECT * FROM scores", conn).set_index("ticker")

    try:
        fund = pd.read_sql("SELECT * FROM fundamentals", conn).set_index("ticker")
        fmap = fund.to_dict("index")
    except Exception:
        fmap = {}

    news_map = {}
    try:
        nw = pd.read_sql(
            "SELECT ticker, headline, published, source FROM news "
            "WHERE published >= ? ORDER BY published DESC",
            conn, params=(window[0],))
        for t, g in nw.groupby("ticker"):
            news_map[t] = g.head(6).to_dict("records")
    except Exception:
        pass

    # ---- sector momentum: is the whole sector moving, or just this stock? ----
    win_start = pd.Timestamp(window[0])
    sector_returns = {}
    all_px = pd.read_sql(
        "SELECT ticker, date, close FROM prices_daily WHERE date >= ? "
        "ORDER BY ticker, date", conn, params=(window[0],), parse_dates=["date"])
    rets = {}
    for t, g in all_px.groupby("ticker"):
        if len(g) > 5:
            rets[t] = (float(g["close"].iloc[-1]) / float(g["close"].iloc[0]) - 1) * 100
    rdf = pd.DataFrame({"ticker": list(rets.keys()), "ret": list(rets.values())})
    rdf["sector"] = rdf["ticker"].map(inst["sector"])
    sector_returns = rdf.groupby("sector")["ret"].median().to_dict()

    rows = []
    for tk in tickers:
        g = px[px["ticker"] == tk].set_index("date")
        if len(g) < max(3, min(10, len(window) // 2)):
            continue
        close = g["close"]
        price = float(close.iloc[-1])
        ret_1m = (price / float(close.iloc[0]) - 1) * 100

        s = scores.loc[tk] if tk in scores.index else None
        i = inst.loc[tk] if tk in inst.index else None
        if s is None or i is None:
            continue

        sector = i["sector"] or "Unknown"
        sec_ret = sector_returns.get(sector)
        excess = (ret_1m - sec_ret) if sec_ret is not None else None

        # current unbroken run
        streak = 0
        mine = set(hist[hist["ticker"] == tk]["as_of"])
        for d in reversed(all_days):
            if d in mine:
                streak += 1
            else:
                break

        vol_recent = float(g["volume"].tail(5).mean())
        vol_month = float(g["volume"].mean())
        vol_trend = (vol_recent / vol_month - 1) * 100 if vol_month else 0

        full = pd.read_sql(
            "SELECT date, close FROM prices_daily WHERE ticker=? ORDER BY date",
            conn, params=(tk,), parse_dates=["date"], index_col="date")["close"]
        ret_3m = ((price / float(full.iloc[-63]) - 1) * 100
                  if len(full) > 63 else None)
        high52_before = float(full.iloc[:-len(g)].tail(252).max()) \
            if len(full) > len(g) + 10 else None
        new_high = int(high52_before is not None and price > high52_before)

        extension = (price / float(s["ma50"]) - 1) * 100 if s["ma50"] else 0
        hits = int(keep.loc[tk, "hits"])
        consistency = hits / len(window) * 100
        stage = stage_of(consistency, ret_1m, float(s["from_high"]),
                         extension, streak)

        # ---------------- technical reasons ----------------
        tech = []
        tech.append(f"Qualified on {hits} of the last {len(window)} trading days "
                    f"({consistency:.0f}% of the time) — this is not a one-day signal")
        if streak >= 10:
            tech.append(f"Currently on an unbroken run of {streak} days")
        if s["rs"] is not None:
            tech.append(f"Outperforming the Nifty by {float(s['rs']):.0f}% over "
                        f"three months")
        tech.append(f"Up {ret_1m:.1f}% across the month"
                    + (f", against {sec_ret:.1f}% for its sector"
                       if sec_ret is not None else ""))
        if new_high:
            tech.append("Broke to a new 52-week high during the month")
        elif float(s["from_high"]) > -5:
            tech.append(f"Holding within {abs(float(s['from_high'])):.0f}% of its "
                        f"yearly high")
        if vol_trend > 20:
            tech.append(f"Volume in the last week is {vol_trend:.0f}% above the "
                        f"month's average — participation is increasing")
        elif vol_trend < -25:
            tech.append(f"Volume has faded {abs(vol_trend):.0f}% below the month's "
                        f"average — interest is cooling")
        if extension > 22:
            tech.append(f"Now {extension:.0f}% above its 50-day average — most of "
                        f"the move has already happened")
        tech.append(f"Moves about {float(s['atr_pct']):.1f}% on a normal day, so a "
                    f"10-15% target is realistic within two months")

        # ---------------- sentiment / fundamental reasons ----------------
        senti = []
        if excess is not None:
            if excess > 10:
                senti.append(f"Beating its own sector by {excess:.0f}% — this is a "
                             f"company-specific story, not just a sector wave. "
                             f"Look for what changed at the company")
            elif excess > 4:
                senti.append(f"Ahead of its sector by {excess:.0f}% — partly an "
                             f"industry move, partly something of its own")
            elif excess < -3:
                senti.append(f"Lagging its own sector by {abs(excess):.0f}% — the "
                             f"sector is carrying it rather than the company leading")
            else:
                senti.append(f"Moving broadly with its sector ({sector}), which "
                             f"suggests a policy or industry-wide driver rather "
                             f"than company news")
        if sec_ret is not None and sec_ret > 8:
            senti.append(f"{sector} as a whole is up {sec_ret:.0f}% this month — "
                         f"sector momentum is behind it")

        heads = news_map.get(tk, [])
        if heads:
            senti.append(f"{len(heads)} news items in the window. Most recent: "
                         f"\"{heads[0]['headline'][:90]}\"")
        else:
            senti.append("No news picked up in the window — the move is not being "
                         "driven by anything reported. Treat with more caution")

        d = s.get("deliv")
        if d is not None and not pd.isna(d):
            if float(d) > 55:
                senti.append(f"{float(d):.0f}% of volume is genuine delivery — "
                             f"buyers are holding, not day-trading. As close to an "
                             f"institutional footprint as free data allows")
            elif float(d) < 25:
                senti.append(f"Only {float(d):.0f}% delivery — mostly intraday "
                             f"churn rather than real accumulation")

        f = fmap.get(tk)
        if f:
            eg = f.get("qtr_earn_growth") or f.get("earnings_growth")
            if eg is not None and not pd.isna(eg):
                senti.append(f"Earnings {'up' if eg > 0 else 'down'} "
                             f"{abs(eg):.0f}% year on year")
            de = f.get("debt_to_equity")
            if de is not None and not pd.isna(de) and de > 1.5:
                senti.append(f"Carries heavy debt ({de:.1f}x equity) — a risk if "
                             f"the rally stalls")
            pe = f.get("pe")
            if pe is not None and not pd.isna(pe) and pe > 70:
                senti.append(f"Trading at {pe:.0f} times earnings — expensive, so "
                             f"disappointment would be punished")
        else:
            senti.append("No business fundamentals loaded — run "
                         "fetch_fundamentals.py to add earnings and debt")

        # ---------------- outlook ----------------
        if stage == "Established":
            outlook = (f"A sustained move, not a spike. It has qualified "
                       f"{consistency:.0f}% of the month and is still running. "
                       f"Trends like this more often continue than reverse, but "
                       f"you are no longer early — size accordingly and keep a "
                       f"stop.")
        elif stage == "Early":
            outlook = (f"Building, not yet proven. {hits} qualifying days is "
                       f"enough to take seriously but not enough to be confident. "
                       f"If it holds another week the case gets stronger.")
        elif stage == "Extended":
            outlook = (f"Up {ret_1m:.0f}% and {extension:.0f}% above its 50-day "
                       f"average. The easy part is behind it. Buying here means "
                       f"a distant stop and a poor risk-to-reward. Better to wait "
                       f"for it to settle back toward the average.")
        else:
            outlook = (f"Qualified only {consistency:.0f}% of the month and is "
                       f"losing its grip. Something has changed. Not a candidate "
                       f"until it re-establishes itself.")

        # ================= WHERE TO ENTER =================
        # Reference levels, not predictions. The reasoning: your stop sits a
        # fixed distance below wherever you buy, so the higher you pay above
        # the trend, the worse your risk-to-reward gets. Buying into a
        # pullback is the same trade at a better price.
        ma20 = float(close.tail(20).mean())
        ma50_v = float(s["ma50"]) if s["ma50"] else price
        atr_v = float(s["atr"])

        target = price * 1.13                      # the 13% we're aiming at
        stop = price - 2.0 * atr_v
        rr = (target - price) / (price - stop) if price > stop else 0

        # Above this price a 2:1 payoff is no longer available
        ceiling = ma20 * 1.10

        if extension < 4:
            verdict = "Buy now"
            ideal = price
            note = (f"Trading close to its 50-day average, so you are not "
                    f"chasing. Entering here puts your stop only "
                    f"{(1-stop/price)*100:.1f}% away.")
        elif extension < 10:
            verdict = "Buy now or wait"
            ideal = ma20
            note = (f"{extension:.0f}% above its 50-day average — workable, but "
                    f"a pull back to around ₹{ma20:,.0f} would give you the same "
                    f"trade with a tighter stop. Consider buying half now and "
                    f"half on a dip.")
        elif extension < 20:
            verdict = "Wait for pullback"
            ideal = ma20
            note = (f"{extension:.0f}% above its 50-day average. Strong trends "
                    f"pull back to their 20-day line regularly — around "
                    f"₹{ma20:,.0f} here. Waiting for that costs you nothing but "
                    f"patience, and buying now means a stop "
                    f"{(1-stop/price)*100:.1f}% below.")
        else:
            verdict = "Do not chase"
            ideal = ma50_v
            note = (f"{extension:.0f}% above its 50-day average — the move you "
                    f"are hoping to catch has largely happened. Your stop would "
                    f"sit a long way down. If you want this, wait for it to "
                    f"settle back toward ₹{ma50_v:,.0f}, or accept you have "
                    f"missed it. There will be another.")

        if rr < 1.8 and verdict == "Buy now":
            verdict = "Buy now or wait"
            note += (f" Note the payoff is only {rr:.1f}:1, which is thinner "
                     f"than ideal.")

        # ============ HOW FAVOURABLE IS AN ENTRY RIGHT NOW ============
        fav = 0
        fav += min(30, consistency * 0.35)             # proven, not a one-off
        fav += min(15, streak * 1.2)                   # currently intact
        fav += 20 if rr >= 2.5 else 14 if rr >= 2 else 6 if rr >= 1.6 else 0
        fav += (18 if extension < 4 else 13 if extension < 10
                else 5 if extension < 20 else 0)       # not chasing
        fav += 8 if vol_trend > 15 else 4 if vol_trend > -10 else 0
        fav += 9 if float(s["turnover"]) > 40 else 5 if float(s["turnover"]) > 15 else 0
        fav = int(max(0, min(100, fav)))

        # ============ WILL IT KEEP GOING NEXT MONTH ============
        # A weighted read of the same evidence, not a probability. Trend
        # persistence and relative strength push it up; over-extension and
        # fading volume pull it down.
        cont = 50
        cont += min(18, (consistency - 50) * 0.36)
        cont += min(12, streak * 0.9)
        rs_v = float(s["rs"]) if s["rs"] is not None else 0
        cont += 14 if rs_v > 25 else 9 if rs_v > 12 else 4 if rs_v > 0 else -8
        if sec_ret is not None:
            cont += 7 if sec_ret > 8 else 3 if sec_ret > 2 else -4 if sec_ret < -3 else 0
        cont += 6 if vol_trend > 15 else 0 if vol_trend > -15 else -7
        cont -= 18 if extension > 22 else 9 if extension > 14 else 0
        if new_high:
            cont += 5
        if heads:
            cont += 4
        cont = int(max(5, min(95, cont)))
        band = "High" if cont >= 68 else "Moderate" if cont >= 48 else "Low"

        # ---- what a month of normal movement actually looks like ----
        # Daily volatility scales with the square root of time. 21 trading
        # days in a month, so a one-standard-deviation move is roughly
        # daily_atr% x sqrt(21). This is a range, not a target.
        month_vol = float(s["atr_pct"]) * (21 ** 0.5)
        drift = (ret_1m / len(window)) * 21 * 0.4      # damped recent trend
        drift = max(-12, min(15, drift))
        fc_base = price * (1 + drift / 100)
        fc_low = price * (1 + (drift - month_vol) / 100)
        fc_high = price * (1 + (drift + month_vol) / 100)

        drivers, brakes = [], []
        if consistency >= 70:
            drivers.append(f"held up {consistency:.0f}% of the month")
        if rs_v > 12:
            drivers.append(f"leading the market by {rs_v:.0f}%")
        if sec_ret is not None and sec_ret > 8:
            drivers.append(f"its sector is up {sec_ret:.0f}%")
        if vol_trend > 15:
            drivers.append("participation is rising")
        if new_high:
            drivers.append("just made a new yearly high")
        if extension > 14:
            brakes.append(f"already {extension:.0f}% above its average")
        if vol_trend < -20:
            brakes.append("volume is fading")
        if sec_ret is not None and sec_ret < -3:
            brakes.append("its sector is falling")
        if not heads:
            brakes.append("no news to sustain the story")
        if rs_v <= 0:
            brakes.append("no longer beating the index")

        forecast = (
            f"On a normal month for a stock this volatile, the range is roughly "
            f"₹{fc_low:,.0f} to ₹{fc_high:,.0f}. "
            f"Continuation looks {band.lower()}"
            + (f" — " + ", ".join(drivers[:3]) if drivers else "")
            + (f". Working against it: " + ", ".join(brakes[:3]) if brakes else "")
            + ". This is what the numbers imply, not a prediction — a single "
              "piece of bad news can override all of it."
        )

        rows.append((
            tk, period_label, as_of, i["name"], sector, i["cap_bucket"],
            float(i["market_cap_cr"]) if pd.notna(i["market_cap_cr"]) else None,
            price, hits, len(window), round(consistency, 1),
            int(wk.get(tk, 0)), len(week), streak,
            round(ret_1m, 2), round(ret_3m, 2) if ret_3m is not None else None,
            round(sec_ret, 2) if sec_ret is not None else None,
            round(excess, 2) if excess is not None else None,
            float(s["rs"]) if s["rs"] is not None else None,
            float(s["from_high"]), float(s["atr_pct"]), float(s["turnover"]),
            round(vol_trend, 1), new_high,
            round(float(keep.loc[tk, "avg_score"]), 1), stage,
            json.dumps(tech), json.dumps(senti), outlook,
            round(ma20, 2), round(ma50_v, 2), round(extension, 1),
            verdict, round(ideal, 2), round(ceiling, 2),
            round(stop, 2), round(target, 2), round(rr, 2), note,
            fav, cont, band, round(fc_low, 2), round(fc_high, 2),
            round(fc_base, 2), forecast,
        ))

    conn.executemany(
        "INSERT INTO monthly_shortlist VALUES (" + ",".join("?" * 46) + ")", rows)
    conn.commit()

    print("=" * 62)
    print(f"  {period_label.upper()} SHORTLIST — {len(rows)} stocks")
    print("=" * 62)
    by_stage = {}
    for r in rows:
        by_stage.setdefault(r[24], []).append(r)
    for stage in ["Established", "Early", "Extended", "Fading"]:
        if stage in by_stage:
            print(f"\n  {stage.upper()} ({len(by_stage[stage])})")
            for r in sorted(by_stage[stage], key=lambda x: -x[9])[:6]:
                print(f"    {r[0].replace('.NS',''):<14} "
                      f"{r[7]:>2}/{r[8]} days ({r[9]:>4.0f}%)   "
                      f"{r[13]:+6.1f}% this month")
    print("\n" + "=" * 62)
    print(f"  Took {time.time()-t0:.0f} seconds")
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-hits", type=int, default=0)
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)
    for days, label in [(7, "weekly"), (30, "monthly")]:
        print(f"\n########## {label.upper()} ({days} trading days) ##########")
        build_period(conn, days, args.min_hits, label)
    conn.close()


if __name__ == "__main__":
    main()