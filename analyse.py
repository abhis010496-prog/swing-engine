"""
IS THERE ANY SKILL HERE — lookahead-free version
================================================
The previous version had a flaw worth understanding, because it is the most
common way backtests lie.

It scored each stock using the AVERAGE score across every day it was picked
in the window, and counted how many times it was picked in total. Both of
those include days AFTER entry. A stock that rose kept qualifying and
accumulated a high count; one that fell dropped off. So "consistency"
was partly a consequence of the outcome, not a predictor of it.

This version fixes that:

  * Every pick is its own observation — (stock, date), not one row per stock.
  * Features use only what was knowable that morning: that day's score, and
    how many times it had been picked in the PRIOR 30 days.
  * Returns are measured over a FIXED horizon so early and late picks are
    comparable. Holding "to now" gives April picks four months and July
    picks two weeks, which is not a fair comparison.
  * Everything is excess over the index across the same window.
  * The period still splits into tuning and holdout halves.

Run with:   python analyse.py
            python analyse.py --horizon 40 --days 120
"""

import argparse
import sqlite3

import numpy as np
import pandas as pd

DB = "market.db"
BENCHMARK = "^NSEI"


def build_events(conn, days, horizon):
    hist = pd.read_sql("SELECT ticker, as_of, score FROM shortlist_history", conn)
    if hist.empty:
        return None
    hist["as_of"] = pd.to_datetime(hist["as_of"])

    all_days = sorted(hist["as_of"].unique())
    window = all_days[-days:]
    w = hist[hist["as_of"].isin(window)].copy()
    tickers = sorted(w["ticker"].unique())

    marks = ",".join("?" * len(tickers))
    px = pd.read_sql(
        f"SELECT ticker, date, open, close FROM prices_daily "
        f"WHERE ticker IN ({marks}) ORDER BY ticker, date",
        conn, params=tickers, parse_dates=["date"])

    sc = pd.read_sql(
        "SELECT ticker, cap_bucket, sector FROM scores", conn).set_index("ticker")

    nif = pd.read_sql(
        "SELECT date, open, close FROM prices_daily WHERE ticker=? ORDER BY date",
        conn, params=(BENCHMARK,), parse_dates=["date"], index_col="date")

    # prior-30-day pick count, strictly before the pick date
    w = w.sort_values(["ticker", "as_of"])
    prior = {}
    for tk, g in w.groupby("ticker"):
        ds = list(g["as_of"])
        for i, d in enumerate(ds):
            cutoff = d - pd.Timedelta(days=30)
            prior[(tk, d)] = sum(1 for x in ds[:i] if x >= cutoff)

    frames = {tk: g.set_index("date").sort_index()
              for tk, g in px.groupby("ticker")}

    events = []
    for r in w.itertuples():
        g = frames.get(r.ticker)
        if g is None:
            continue
        after = g[g.index > r.as_of]
        if len(after) < horizon + 1:
            continue                      # not enough forward data — skip

        entry_date = after.index[0]
        entry = float(after["open"].iloc[0])
        if entry <= 0:
            continue
        exit_date = after.index[horizon]
        exit_px = float(after["close"].iloc[horizon])

        na = nif[nif.index >= entry_date]
        nb = na[na.index <= exit_date]
        if len(na) < 1 or len(nb) < 1:
            continue
        idx_ret = (float(nb["close"].iloc[-1]) / float(na["open"].iloc[0]) - 1) * 100

        stock_ret = (exit_px / entry - 1) * 100
        s = sc.loc[r.ticker] if r.ticker in sc.index else None

        # as-of features only
        hist_px = g[g.index <= r.as_of]["close"]
        if len(hist_px) < 60:
            continue
        ma50 = float(hist_px.tail(50).mean())
        last = float(hist_px.iloc[-1])
        ext = (last / ma50 - 1) * 100 if ma50 else np.nan
        vol = float(hist_px.pct_change().tail(20).std() * 100)

        events.append(dict(
            ticker=r.ticker, date=r.as_of, score=float(r.score),
            prior=prior.get((r.ticker, r.as_of), 0),
            ret=stock_ret, idx=idx_ret, ex=stock_ret - idx_ret,
            cap=(s["cap_bucket"] if s is not None else None),
            sector=(s["sector"] if s is not None else None),
            ext=ext, vol=vol))
    return pd.DataFrame(events)


def stat(a):
    a = np.asarray([x for x in a if x is not None and not np.isnan(x)])
    if len(a) == 0:
        return None
    return dict(n=len(a), mean=a.mean(), med=np.median(a),
                beat=(a > 0).mean() * 100)


def show(title, groups, min_n=25):
    print(f"\n  {title}")
    print("  " + "-" * 56)
    print(f"  {'group':<24}{'n':>6}{'excess %':>11}{'beat idx %':>12}")
    for name, vals in groups:
        s = stat(vals)
        if not s or s["n"] < min_n:
            print(f"  {str(name):<24}{(s['n'] if s else 0):>6}"
                  f"     too few to judge")
            continue
        print(f"  {str(name):<24}{s['n']:>6}{s['mean']:>11.2f}{s['beat']:>12.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--horizon", type=int, default=20,
                    help="trading days held after entry")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    ev = build_events(conn, args.days, args.horizon)
    if ev is None or ev.empty:
        print("Not enough data with a full forward window. Try a shorter")
        print("--horizon, or run backfill_history.py over more days.")
        return

    dates = sorted(ev["date"].unique())
    cut = dates[len(dates) // 2]
    tune = ev[ev["date"] < cut]
    hold = ev[ev["date"] >= cut]

    print("=" * 60)
    print(f"  IS THERE ANY SKILL — {len(ev)} pick-days, {args.horizon}-day hold")
    print("=" * 60)
    print("  Every feature uses only what was known on the pick date.")
    print("  Every return is excess over the index across the same window.")
    print(f"\n  Tuning : {len(tune)} picks before {pd.Timestamp(cut).date()}")
    print(f"  Holdout: {len(hold)} picks from {pd.Timestamp(cut).date()}")

    for label, d in [("TUNING", tune), ("HOLDOUT", hold)]:
        s, r, i = stat(d["ex"]), stat(d["ret"]), stat(d["idx"])
        if not s:
            continue
        print(f"\n  {label}")
        print(f"    stock {r['mean']:+7.2f}%   index {i['mean']:+7.2f}%   "
              f"excess {s['mean']:+6.2f} pts")
        print(f"    beat the index {s['beat']:.0f}% of the time")

    print("\n" + "=" * 60)
    print("  SLICES — tuning half only")
    print("=" * 60)

    t = tune
    try:
        q = pd.qcut(t["score"], 4, labels=["lowest", "low", "high", "highest"],
                    duplicates="drop")
        show("Does that day's SCORE predict excess return?",
             [(l, t[q == l]["ex"].tolist()) for l in q.cat.categories])
    except Exception:
        print("\n  Score: not enough spread")

    show("Does being picked BEFORE (prior 30 days) help?",
         [("first time", t[t["prior"] == 0]["ex"].tolist()),
          ("picked 1-4 times before", t[(t["prior"] >= 1) & (t["prior"] <= 4)]["ex"].tolist()),
          ("picked 5-14 before", t[(t["prior"] >= 5) & (t["prior"] <= 14)]["ex"].tolist()),
          ("picked 15+ before", t[t["prior"] >= 15]["ex"].tolist())])

    show("Does COMPANY SIZE help?",
         [(c, t[t["cap"] == c]["ex"].tolist())
          for c in ["large", "mid", "small", "micro"]])

    show("Does VOLATILITY help, beta removed?",
         [("calm (<2%)", t[t["vol"] < 2]["ex"].tolist()),
          ("medium (2-3.5%)", t[(t["vol"] >= 2) & (t["vol"] < 3.5)]["ex"].tolist()),
          ("wild (3.5%+)", t[t["vol"] >= 3.5]["ex"].tolist())])

    show("Does EXTENSION above the 50-day average help?",
         [("near average (<8%)", t[t["ext"] < 8]["ex"].tolist()),
          ("stretched (8-18%)", t[(t["ext"] >= 8) & (t["ext"] < 18)]["ex"].tolist()),
          ("extended (18%+)", t[t["ext"] >= 18]["ex"].tolist())])

    # ---- concentration, ranked by that day's score only ----
    print("\n  Would taking only the best-scoring few each day help?")
    print("  " + "-" * 56)
    print(f"  {'each day take':<24}{'n':>6}{'excess %':>11}{'beat idx %':>12}")
    conc = {}
    for n in [3, 5, 10, 20, None]:
        if n is None:
            sub = t
        else:
            sub = (t.sort_values(["date", "score"], ascending=[True, False])
                     .groupby("date").head(n))
        s = stat(sub["ex"])
        lbl = "everything" if n is None else f"top {n}"
        conc[lbl] = (s, n)
        print(f"  {lbl:<24}{s['n']:>6}{s['mean']:>11.2f}{s['beat']:>12.0f}")

    # ================= HOLDOUT =================
    print("\n" + "=" * 60)
    print("  HOLDOUT — never examined above")
    print("=" * 60)
    base = stat(hold["ex"])
    print(f"  Everything            : {base['mean']:+.2f} pts, "
          f"beat {base['beat']:.0f}%")

    best = max((k for k in conc if conc[k][0]), key=lambda k: conc[k][0]["mean"])
    n = conc[best][1]
    sub_h = (hold if n is None else
             hold.sort_values(["date", "score"], ascending=[True, False])
                 .groupby("date").head(n))
    hs = stat(sub_h["ex"])
    print(f"  Best from tuning ({best}):")
    print(f"    tuning {conc[best][0]['mean']:+.2f}  ->  holdout "
          f"{hs['mean']:+.2f} pts, beat {hs['beat']:.0f}%")

    # top vs bottom score, on the holdout
    try:
        qh = pd.qcut(hold["score"], 4, labels=["lowest", "low", "high", "highest"],
                     duplicates="drop")
        top = stat(hold[qh == "highest"]["ex"])
        bot = stat(hold[qh == "lowest"]["ex"])
        if top and bot:
            print(f"\n  Score buckets on the holdout:")
            print(f"    highest quarter {top['mean']:+.2f} pts (n={top['n']})")
            print(f"    lowest quarter  {bot['mean']:+.2f} pts (n={bot['n']})")
            print(f"    spread          {top['mean']-bot['mean']:+.2f} pts")
    except Exception:
        pass

    print("\n" + "-" * 60)
    if base["mean"] > 1:
        print(f"  The picks beat the index by {base['mean']:+.2f} pts on unseen data.")
    elif base["mean"] > -0.5:
        print(f"  The picks matched the index ({base['mean']:+.2f} pts) on unseen data.")
    else:
        print(f"  The picks trailed by {abs(base['mean']):.2f} pts on unseen data.")

    if hs["mean"] > base["mean"] + 0.5 and hs["mean"] > 0.5:
        print(f"  Concentrating into '{best}' held up out of sample.")
    else:
        print("  Concentration did not hold up out of sample.")

    print("\n" + "=" * 60)
    print("  Costs are not deducted. Subtract 0.3-0.6% per round trip.")
    print("  One holdout is one sample — repeat this monthly before")
    print("  changing anything real.")
    print("=" * 60)
    conn.close()


if __name__ == "__main__":
    main()