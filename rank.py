"""
NIGHTLY RANKING
===============
Scores every stock once and stores the result, so the dashboard doesn't have
to calculate 1,874 stocks each time you open it.

Run this after build_universe.py / run_daily.bat. Takes 1-3 minutes.

Run with:   python rank.py
"""

import json
import sqlite3
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

DB = "market.db"
BENCHMARK = "^NSEI"
MIN_ROWS = 220

EVENTS = {
    "order": "won a new order", "contract": "won a new order",
    "bags": "won a new order", "wins": "won a new order",
    "results": "reported results", "profit": "reported results",
    "revenue": "reported results", "capex": "announced expansion",
    "expansion": "announced expansion", "capacity": "announced expansion",
    "acquisition": "is buying another company", "acquires": "is buying another company",
    "merger": "is merging", "stake": "had a stake change",
    "upgrade": "was upgraded by analysts", "downgrade": "was downgraded by analysts",
    "buyback": "announced a buyback", "dividend": "announced a dividend",
    "bonus": "announced bonus shares",
}


def event_of(h):
    low = h.lower()
    for w, p in EVENTS.items():
        if w in low:
            return p
    return None


SCHEMA = """
CREATE TABLE IF NOT EXISTS scores (
    ticker      TEXT PRIMARY KEY,
    as_of       TEXT,
    name        TEXT,
    sector      TEXT,
    cap_bucket  TEXT,
    mcap        REAL,
    price       REAL,
    day_chg     REAL,
    ma50        REAL,
    ma200       REAL,
    atr         REAL,
    atr_pct     REAL,
    from_high   REAL,
    high52      REAL,
    rs          REAL,
    vol_x       REAL,
    turnover    REAL,
    trend       INTEGER,
    score       INTEGER,
    good_json   TEXT,
    bad_json    TEXT,
    events_json TEXT,
    deliv REAL,
    is_be INTEGER,
    fscore INTEGER,
    has_fund INTEGER,
    vetoed INTEGER,
    veto_json TEXT,
    qualifies INTEGER,
    days_on_list INTEGER,
    first_seen TEXT,
    is_new INTEGER
);
CREATE INDEX IF NOT EXISTS ix_score ON scores(score DESC);
"""

HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS shortlist_history (
    ticker TEXT NOT NULL,
    as_of  TEXT NOT NULL,
    score  INTEGER,
    PRIMARY KEY (ticker, as_of)
);
CREATE INDEX IF NOT EXISTS ix_hist_date ON shortlist_history(as_of);
"""


def base_qualifies(m):
    """The stable gate — deliberately excludes anything that swings daily."""
    return (m["trend"] and (m["rs"] or 0) > 0
            and m["from_high"] > -15 and m["turnover"] > 8)


def tenure(conn, ticker, as_of):
    """
    How many consecutive runs has this stock qualified for?

    A stock that has held up for three weeks is a different proposition from
    one that appeared this morning. For a two-month hold, that distinction
    matters more than a couple of points of score.
    """
    rows = [r[0] for r in conn.execute(
        "SELECT DISTINCT as_of FROM shortlist_history "
        "WHERE ticker=? ORDER BY as_of DESC LIMIT 90", (ticker,))]
    if not rows:
        return 0, None
    all_days = [r[0] for r in conn.execute(
        "SELECT DISTINCT as_of FROM shortlist_history ORDER BY as_of DESC LIMIT 90")]
    streak = 0
    for d in all_days:
        if d in rows:
            streak += 1
        else:
            break
    return streak, rows[-1]


def analyse(g, bench, heads, fu=None):
    close = g["close"]
    volume = g["volume"]
    if len(close) < MIN_ROWS:
        return None

    last = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1])
    high52 = float(close.tail(252).max())
    from_high = (last / high52 - 1) * 100
    vol20 = float(volume.rolling(20).mean().iloc[-1])
    # Five-day average volume, not today's. One busy afternoon should not
    # push a stock onto a list you intend to hold for two months.
    vol5 = float(volume.tail(5).mean())
    vol_x = vol5 / vol20 if vol20 else 0
    turnover = vol20 * last / 1e7

    hl = g["high"] - g["low"]
    hc = (g["high"] - close.shift()).abs()
    lc = (g["low"] - close.shift()).abs()
    atr = float(pd.concat([hl, hc, lc], axis=1).max(axis=1)
                .ewm(alpha=1/14, adjust=False).mean().iloc[-1])

    rs = None
    if bench is not None and len(close) > 63:
        c = bench.reindex(close.index).ffill()
        if c.notna().sum() > 63:
            rs = ((last / float(close.iloc[-63]) - 1) * 100
                  - (float(c.iloc[-1]) / float(c.iloc[-63]) - 1) * 100)

    good, bad = [], []
    if last > ma50 > ma200:
        good.append("Climbing steadily — both trend lines point up")
    elif last > ma50:
        bad.append("Rising lately, but the long-term trend is still weak")
    else:
        bad.append("Price is falling, not rising")

    if rs is not None:
        if rs > 20:   good.append(f"Far outpacing the market, by about {rs:.0f}%")
        elif rs > 10: good.append(f"Beating the market by {rs:.0f}% over three months")
        elif rs > 0:  good.append("Slightly ahead of the market")
        else:         bad.append(f"Lagging the market by {abs(rs):.0f}%")

    if from_high > -3:    good.append("At or near its highest price in a year")
    elif from_high > -8:  good.append(f"Near its yearly high, {abs(from_high):.0f}% below")
    elif from_high > -15: bad.append(f"Sitting {abs(from_high):.0f}% below its yearly high")
    else:                 bad.append(f"Well off its peak, down {abs(from_high):.0f}%")

    if vol_x > 1.6:   good.append("Trading volume has stepped up over the past "
                                  "week, which supports the move")
    elif vol_x > 1.2: good.append("Slightly busier than usual")
    elif vol_x < 0.7: bad.append("Interest is fading — volume below its own average")

    if turnover > 50:  good.append("Very easy to buy and sell at any size")
    elif turnover < 5: bad.append("Thinly traded — hard to sell in a hurry")

    # ---- delivery percentage: real buying versus intraday churn ----
    deliv = None
    is_be = False
    if "delivery_pct" in g.columns:
        d = g["delivery_pct"].dropna()
        if len(d) >= 5:
            deliv = float(d.tail(20).mean())
    if "series" in g.columns:
        recent = g["series"].dropna()
        if len(recent) and str(recent.iloc[-1]).strip() == "BE":
            is_be = True

    if is_be:
        # BE means trade-for-trade. Delivery is 100% by rule, so it tells us
        # nothing — but the series itself often signals surveillance.
        bad.append("In the BE series — usually a sign of exchange surveillance")
        deliv = None
    elif deliv is not None:
        if deliv > 60:
            good.append(f"{deliv:.0f}% of volume is genuine delivery — "
                        f"real buyers, not day traders")
        elif deliv < 25:
            bad.append(f"Only {deliv:.0f}% of volume is delivery — mostly "
                       f"intraday churn, so the move is fragile")

    # ================= THE BUSINESS BEHIND THE PRICE =================
    # Everything above is chart-reading. This is whether the company is
    # any good. Capped at +/-20 so it informs the ranking without
    # overwhelming the momentum signal that drives a two-month trade.
    fscore = 0
    if fu:
        def g_(k):
            v = fu.get(k)
            return None if v is None or (isinstance(v, float) and v != v) else v

        eg = g_("qtr_earn_growth")
        if eg is None:
            eg = g_("earnings_growth")
        if eg is not None:
            if eg > 25:
                good.append(f"Profits growing fast — up {eg:.0f}% on last year")
                fscore += 5
            elif eg > 10:
                good.append(f"Profits growing {eg:.0f}% on last year")
                fscore += 3
            elif eg < 0:
                bad.append(f"Profits are shrinking, down {abs(eg):.0f}%")
                fscore -= 4

        rg = g_("revenue_growth")
        if rg is not None:
            if rg > 20:
                good.append(f"Sales growing strongly, up {rg:.0f}%")
                fscore += 4
            elif rg > 10:
                fscore += 2
            elif rg < 0:
                bad.append(f"Sales are falling, down {abs(rg):.0f}%")
                fscore -= 3

        pm = g_("profit_margin")
        if pm is not None and pm < 0:
            bad.append(f"The company is loss-making ({pm:.1f}% margin)")
            fscore -= 6

        om = g_("operating_margin")
        if om is not None:
            if om > 18:
                good.append(f"Healthy margins — keeps {om:.0f} paise of every rupee")
                fscore += 3
            elif 0 < om < 5:
                bad.append(f"Wafer-thin margins of {om:.1f}% — little room for error")
                fscore -= 2

        roe = g_("roe")
        if roe is not None:
            if roe > 18:
                good.append(f"Uses shareholder money well ({roe:.0f}% return on equity)")
                fscore += 3
            elif roe < 5:
                bad.append(f"Poor return on shareholder money ({roe:.0f}%)")
                fscore -= 2

        de = g_("debt_to_equity")
        if de is not None:
            if de > 2:
                bad.append(f"Heavily indebted — owes {de:.1f}x its own equity")
                fscore -= 6
            elif de > 1:
                bad.append(f"Carries meaningful debt ({de:.1f}x equity)")
                fscore -= 3
            elif de < 0.3:
                good.append("Very little debt — a cushion if things go wrong")
                fscore += 2

        pe = g_("pe")
        if pe is not None and pe > 0:
            if pe > 80:
                bad.append(f"Very expensive at {pe:.0f} times earnings — a lot "
                           f"of growth is already priced in")
                fscore -= 3
            elif pe < 25 and (eg or 0) > 15:
                good.append(f"Growing well yet only {pe:.0f} times earnings")
                fscore += 3
    else:
        bad.append("No business numbers available — chart signals only")

    fscore = max(-15, min(20, fscore))

    # ================= HARD VETOES =================
    # These are not scored, they are disqualifying. The point is not to
    # predict winners but to remove the handful of names capable of losing
    # half your money in a week. A great chart on a loss-making, heavily
    # indebted company is still a great chart on a loss-making, heavily
    # indebted company.
    vetoes = []
    if is_be:
        vetoes.append("BE series — under exchange surveillance")
    if fu:
        pm_ = fu.get("profit_margin")
        if pm_ is not None and not (isinstance(pm_, float) and pm_ != pm_) and pm_ < 0:
            vetoes.append(f"loss-making ({pm_:.1f}% margin)")
        de_ = fu.get("debt_to_equity")
        if de_ is not None and not (isinstance(de_, float) and de_ != de_) and de_ > 2:
            vetoes.append(f"debt {de_:.1f}x equity")
        rg_ = fu.get("revenue_growth")
        if rg_ is not None and not (isinstance(rg_, float) and rg_ != rg_) and rg_ < -15:
            vetoes.append(f"sales collapsing ({rg_:.0f}%)")
    if turnover < 8:
        vetoes.append(f"too illiquid (Rs{turnover:.1f}cr/day)")

    # ================= THE TIGHTER GATE =================
    # Deliberately stricter than before. The old gate passed 330 stocks a
    # day, which is not a screen, it is a list. This is about making the
    # daily list usable — it is not a claim that stricter means better
    # returns, which the holdout did not support.
    qualifies = (
        not vetoes
        and last > ma50 > ma200                 # clean uptrend
        and (rs or 0) > 5                       # clearly ahead, not marginally
        and from_high > -10                     # near its highs
        and turnover > 15                       # comfortably tradeable
        and 1.8 <= atr / last * 100 <= 7        # moves enough, not wildly
        and last / ma50 < 1.20                  # not already extended
    )

    events = sorted({e for e in (event_of(h) for h in heads) if e})
    if events:
        good.append("Recent news: " + ", ".join(events))
    elif heads:
        bad.append("In the news, but nothing clearly business-changing")
    else:
        bad.append("No recent news — nothing obvious driving it")

    s = 0
    if last > ma50: s += 7
    if ma50 > ma200: s += 8
    if rs is not None:
        s += 15 if rs > 20 else 11 if rs > 10 else 7 if rs > 0 else 0
    s += 12 if from_high > -3 else 9 if from_high > -8 else 5 if from_high > -15 else 0
    s += 5 if vol_x > 1.6 else 3 if vol_x > 1.2 else 1 if vol_x > 0.8 else 0
    s += 5 if turnover > 50 else 3 if turnover > 10 else 1 if turnover > 3 else 0
    s += min(15, 5 * len(events))
    s += fscore
    if is_be:
        s -= 12                       # surveillance series: hard penalty
    elif deliv is not None:
        s += 6 if deliv > 60 else 3 if deliv > 45 else -4 if deliv < 25 else 0

    return dict(price=last, day_chg=(last/prev - 1) * 100, ma50=ma50, ma200=ma200,
                atr=atr, atr_pct=atr / last * 100 if last else 0,
                from_high=from_high, high52=high52, rs=rs, vol_x=vol_x,
                turnover=turnover, trend=int(last > ma50 > ma200), score=max(0, s),
                good=good, bad=bad, events=events,
                deliv=deliv, is_be=int(is_be), fscore=fscore,
                has_fund=int(bool(fu)), vetoes=vetoes,
                qualifies=int(qualifies))


def main():
    t0 = time.time()
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)

    print("Loading price history ...")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(prices_daily)")}
    extra = ""
    if "delivery_pct" in cols:
        extra += ", delivery_pct"
    if "series" in cols:
        extra += ", series"
    prices = pd.read_sql(
        f"SELECT ticker, date, high, low, close, volume{extra} FROM prices_daily "
        "ORDER BY ticker, date", conn, parse_dates=["date"])
    print(f"  {len(prices):,} rows")

    inst = pd.read_sql(
        "SELECT ticker, name, sector, cap_bucket, market_cap_cr "
        "FROM instruments WHERE active = 1", conn).set_index("ticker")

    try:
        cut = (datetime.now() - timedelta(days=21)).strftime("%Y-%m-%d")
        news = pd.read_sql("SELECT ticker, headline FROM news WHERE published >= ?",
                           conn, params=(cut,))
        news_map = news.groupby("ticker")["headline"].apply(list).to_dict()
    except Exception:
        news_map = {}

    try:
        fund = pd.read_sql("SELECT * FROM fundamentals", conn).set_index("ticker")
        fund_map = fund.to_dict("index")
        print(f"  fundamentals for {len(fund_map):,} companies")
    except Exception:
        fund_map = {}
        print("  no fundamentals table yet — run fetch_fundamentals.py")

    bench = None
    b = prices[prices["ticker"] == BENCHMARK]
    if not b.empty:
        bench = b.set_index("date")["close"]
        print("  benchmark loaded")

    print("Scoring ...")
    as_of = prices["date"].max().strftime("%Y-%m-%d")
    out = []
    skipped = 0

    # One pass through the data instead of a scan per stock — this is the
    # difference between 3 minutes and 40.
    for ticker, g in prices.groupby("ticker", sort=False):
        if ticker == BENCHMARK or ticker not in inst.index:
            continue
        g = g.set_index("date")
        m = analyse(g, bench, news_map.get(ticker, []), fund_map.get(ticker))
        if m is None:
            skipped += 1
            continue
        row = inst.loc[ticker]
        out.append((
            ticker, as_of, row["name"], row["sector"], row["cap_bucket"],
            float(row["market_cap_cr"]) if pd.notna(row["market_cap_cr"]) else None,
            m["price"], m["day_chg"], m["ma50"], m["ma200"], m["atr"], m["atr_pct"],
            m["from_high"], m["high52"], m["rs"], m["vol_x"], m["turnover"],
            m["trend"], m["score"],
            json.dumps(m["good"]), json.dumps(m["bad"]), json.dumps(m["events"]),
            m["deliv"], m["is_be"], m["fscore"], m["has_fund"],
            int(bool(m["vetoes"])), json.dumps(m["vetoes"]), m["qualifies"],
        ))

    conn.executescript(HISTORY_SCHEMA)

    # Record who qualifies today, before rebuilding the scores table.
    # Columns: 12=from_high, 14=rs, 16=turnover, 17=trend, 18=score
    # index 27 is the new stricter 'qualifies' flag from analyse()
    qualifying = [(o[0], as_of, o[18]) for o in out if o[28]]
    print(f"  {len(qualifying)} stocks qualify today")
    conn.executemany(
        "INSERT OR REPLACE INTO shortlist_history VALUES (?,?,?)", qualifying)
    conn.commit()

    # Work out how long each has been holding its place.
    out2 = []
    for o in out:
        streak, first = tenure(conn, o[0], as_of)
        o = list(o)
        # Persistence bonus: a trend intact for weeks is more trustworthy
        # than one that appeared this morning. Deliberately modest.
        if streak >= 20:
            o[18] = min(100, o[18] + 6)
        elif streak >= 10:
            o[18] = min(100, o[18] + 4)
        elif streak >= 5:
            o[18] = min(100, o[18] + 2)
        out2.append(tuple(o) + (streak, first, int(streak <= 1)))
    out = out2

    # Keep three months of history, no more.
    conn.execute("DELETE FROM shortlist_history WHERE as_of < "
                 "(SELECT MIN(as_of) FROM (SELECT DISTINCT as_of FROM "
                 "shortlist_history ORDER BY as_of DESC LIMIT 90))")

    conn.execute("DROP TABLE IF EXISTS scores")
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO scores VALUES (" + ",".join("?" * 32) + ")", out)
    conn.commit()

    print(f"\n  scored {len(out):,}, skipped {skipped:,} (too little history)")
    print(f"  took {time.time()-t0:.0f} seconds\n")

    # ---- how many survive each gate, so we can sanity-check thresholds ----
    q = lambda w: conn.execute(f"SELECT COUNT(*) FROM scores WHERE {w}").fetchone()[0]
    print("=" * 54)
    print("  HOW MANY SURVIVE EACH FILTER")
    print("=" * 54)
    print(f"  Scored                          {len(out):>6,}")
    print(f"  In an uptrend                   {q('trend=1'):>6,}")
    print(f"  ... and beating the index       {q('trend=1 AND rs>0'):>6,}")
    print(f"  ... and within 15% of high      "
          f"{q('trend=1 AND rs>0 AND from_high>-15'):>6,}")
    print(f"  ... and turnover over Rs8cr     "
          f"{q('trend=1 AND rs>0 AND from_high>-15 AND turnover>8'):>6,}")
    print(f"  ... and moves at least 1.8%/day "
          f"{q('trend=1 AND rs>0 AND from_high>-15 AND turnover>8 AND atr_pct>1.8'):>6,}")
    print(f"  ... and not over-extended       "
          f"{q('trend=1 AND rs>0 AND from_high>-15 AND turnover>8 AND atr_pct>1.8 AND price/ma50 < 1.25'):>6,}")
    print("=" * 54)
    print("\n  By size (after all filters):")
    for bucket in ["large", "mid", "small", "micro"]:
        n = q(f"trend=1 AND rs>0 AND from_high>-15 AND turnover>8 AND "
              f"atr_pct>1.8 AND price/ma50 < 1.25 AND cap_bucket='{bucket}'")
        print(f"    {bucket:<8} {n:>5}")

    # ---- churn report: how stable is the list actually? ----
    days = [r[0] for r in conn.execute(
        "SELECT DISTINCT as_of FROM shortlist_history ORDER BY as_of DESC LIMIT 2")]
    if len(days) == 2:
        today_set = {r[0] for r in conn.execute(
            "SELECT ticker FROM shortlist_history WHERE as_of=?", (days[0],))}
        yday_set = {r[0] for r in conn.execute(
            "SELECT ticker FROM shortlist_history WHERE as_of=?", (days[1],))}
        added, dropped = today_set - yday_set, yday_set - today_set
        kept = today_set & yday_set
        print("\n" + "=" * 54)
        print("  HOW MUCH THE LIST CHANGED SINCE LAST RUN")
        print("=" * 54)
        print(f"  Held their place : {len(kept)}")
        print(f"  New today        : {len(added)}")
        print(f"  Dropped off      : {len(dropped)}")
        if dropped:
            print("    gone: " + ", ".join(
                sorted(d.replace('.NS', '') for d in dropped)[:8]))
        stable = len(kept) / max(1, len(yday_set)) * 100
        print(f"\n  {stable:.0f}% of yesterday's list is still there.")
        print("  For a two-month hold you want this high. Below about 70%")
        print("  the screen is reacting to noise rather than trend.")
        print("=" * 54)

    conn.close()
    print("\nDone. The dashboard will now load instantly.")


if __name__ == "__main__":
    main()