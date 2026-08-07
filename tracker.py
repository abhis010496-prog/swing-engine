"""
PICK PERFORMANCE TRACKER
========================
Answers the only question that really matters:

    "If I had bought every stock the system picked, on the day it picked
     them, what would have actually happened?"

Uses shortlist_history (who was picked, when) joined against prices_daily
(what happened next). Entry is the NEXT day's open, not the same day's
close, because you cannot buy a price that has already printed.

Run with:   python tracker.py
            python tracker.py --days 7        this week only
            python tracker.py --days 30 --invest 25000

Does not touch paper trading. That measures your decisions; this measures
the system's.
"""

import argparse
import sqlite3
import time

import pandas as pd

DB = "market.db"
BENCHMARK = "^NSEI"

SCHEMA = """
DROP TABLE IF EXISTS pick_performance;
CREATE TABLE pick_performance (
    ticker        TEXT,
    window_days   INTEGER,
    name          TEXT,
    sector        TEXT,
    cap_bucket    TEXT,
    first_picked  TEXT,
    entry_date    TEXT,
    entry_price   REAL,
    latest_date   TEXT,
    latest_price  REAL,
    ret_pct       REAL,
    max_gain_pct  REAL,
    max_dd_pct    REAL,
    days_held     INTEGER,
    times_picked  INTEGER,
    invested      REAL,
    pnl           REAL,
    nifty_ret_pct REAL,
    excess_pct    REAL,
    PRIMARY KEY (ticker, window_days)
);

DROP TABLE IF EXISTS tracker_summary;
CREATE TABLE tracker_summary (
    window_days   INTEGER PRIMARY KEY,
    built_at      TEXT,
    from_date     TEXT,
    to_date       TEXT,
    picks         INTEGER,
    winners       INTEGER,
    losers        INTEGER,
    win_rate      REAL,
    avg_ret       REAL,
    median_ret    REAL,
    total_invested REAL,
    total_pnl     REAL,
    portfolio_ret REAL,
    nifty_ret     REAL,
    excess        REAL,
    best_ticker   TEXT,
    best_ret      REAL,
    worst_ticker  TEXT,
    worst_ret     REAL,
    avg_max_gain  REAL,
    avg_max_dd    REAL,
    verdict       TEXT
);
"""


def build(conn, window_days, invest):
    hist = pd.read_sql("SELECT ticker, as_of FROM shortlist_history", conn)
    if hist.empty:
        return None

    all_days = sorted(hist["as_of"].unique())
    window = all_days[-window_days:]
    w = hist[hist["as_of"].isin(window)]
    if w.empty:
        return None

    first = w.groupby("ticker")["as_of"].min()
    counts = w.groupby("ticker").size()
    tickers = list(first.index)

    marks = ",".join("?" * len(tickers))
    px = pd.read_sql(
        f"SELECT ticker, date, open, high, low, close FROM prices_daily "
        f"WHERE ticker IN ({marks}) AND date >= ? ORDER BY ticker, date",
        conn, params=tickers + [window[0]], parse_dates=["date"])

    inst = pd.read_sql(
        "SELECT ticker, name, sector, cap_bucket FROM instruments", conn
    ).set_index("ticker")

    nif = pd.read_sql(
        "SELECT date, open, close FROM prices_daily WHERE ticker=? AND date >= ? "
        "ORDER BY date", conn, params=(BENCHMARK, window[0]),
        parse_dates=["date"], index_col="date")

    rows = []
    for tk in tickers:
        g = px[px["ticker"] == tk].set_index("date").sort_index()
        if g.empty:
            continue

        picked = pd.Timestamp(first[tk])
        after = g[g.index > picked]
        if after.empty:
            # picked on the most recent day — no chance to trade it yet
            continue

        entry_row = after.iloc[0]
        entry_price = float(entry_row["open"]) or float(entry_row["close"])
        if entry_price <= 0:
            continue
        entry_date = after.index[0]

        held = g[g.index >= entry_date]
        latest_price = float(held["close"].iloc[-1])
        latest_date = held.index[-1]

        ret = (latest_price / entry_price - 1) * 100
        max_gain = (float(held["high"].max()) / entry_price - 1) * 100
        max_dd = (float(held["low"].min()) / entry_price - 1) * 100

        qty = invest / entry_price
        pnl = qty * (latest_price - entry_price)

        nif_ret = None
        if not nif.empty:
            na = nif[nif.index >= entry_date]
            if len(na) > 1:
                nif_ret = (float(na["close"].iloc[-1])
                           / float(na["open"].iloc[0]) - 1) * 100

        i = inst.loc[tk] if tk in inst.index else None
        rows.append((
            tk, window_days,
            i["name"] if i is not None else tk.replace(".NS", ""),
            (i["sector"] if i is not None else None) or "Unknown",
            i["cap_bucket"] if i is not None else None,
            str(first[tk]), entry_date.strftime("%Y-%m-%d"), round(entry_price, 2),
            latest_date.strftime("%Y-%m-%d"), round(latest_price, 2),
            round(ret, 2), round(max_gain, 2), round(max_dd, 2),
            int((latest_date - entry_date).days), int(counts[tk]),
            round(invest, 2), round(pnl, 2),
            round(nif_ret, 2) if nif_ret is not None else None,
            round(ret - nif_ret, 2) if nif_ret is not None else None,
        ))

    if not rows:
        return None

    conn.executemany(
        "INSERT OR REPLACE INTO pick_performance VALUES ("
        + ",".join("?" * 19) + ")", rows)

    # ---------------- portfolio level ----------------
    df = pd.DataFrame(rows, columns=[
        "ticker", "window_days", "name", "sector", "cap", "first_picked",
        "entry_date", "entry_price", "latest_date", "latest_price", "ret",
        "max_gain", "max_dd", "days", "times", "invested", "pnl",
        "nifty_ret", "excess"])

    winners = df[df["pnl"] > 0]
    losers = df[df["pnl"] <= 0]
    total_inv = float(df["invested"].sum())
    total_pnl = float(df["pnl"].sum())
    port_ret = total_pnl / total_inv * 100 if total_inv else 0
    nifty_avg = (float(df["nifty_ret"].mean())
                 if df["nifty_ret"].notna().any() else None)
    excess = port_ret - nifty_avg if nifty_avg is not None else None

    best = df.loc[df["ret"].idxmax()]
    worst = df.loc[df["ret"].idxmin()]

    if excess is None:
        verdict = "No benchmark available for comparison."
    elif excess > 2:
        verdict = (f"The picks beat the index by {excess:.1f} points over this "
                   f"window. Encouraging, but one window is not evidence — "
                   f"check it again over several.")
    elif excess > -1:
        verdict = (f"The picks roughly matched the index ({excess:+.1f} points). "
                   f"The screen is not adding much beyond market direction here.")
    else:
        verdict = (f"The picks lagged the index by {abs(excess):.1f} points. "
                   f"If that repeats across windows, the honest conclusion is "
                   f"that the scoring is not earning its complexity.")

    conn.execute(
        "INSERT OR REPLACE INTO tracker_summary VALUES ("
        + ",".join("?" * 22) + ")",
        (window_days, time.strftime("%Y-%m-%d %H:%M"), window[0], window[-1],
         int(len(df)), int(len(winners)), int(len(losers)),
         round(len(winners) / len(df) * 100, 1),
         round(float(df["ret"].mean()), 2), round(float(df["ret"].median()), 2),
         round(total_inv, 2), round(total_pnl, 2), round(port_ret, 2),
         round(nifty_avg, 2) if nifty_avg is not None else None,
         round(excess, 2) if excess is not None else None,
         str(best["ticker"]), round(float(best["ret"]), 2),
         str(worst["ticker"]), round(float(worst["ret"]), 2),
         round(float(df["max_gain"].mean()), 2),
         round(float(df["max_dd"].mean()), 2),
         verdict))
    conn.commit()
    return df


def report(df, window_days, conn):
    s = conn.execute("SELECT * FROM tracker_summary WHERE window_days=?",
                     (window_days,)).fetchone()
    cols = [d[0] for d in conn.execute(
        "SELECT * FROM tracker_summary LIMIT 1").description]
    s = dict(zip(cols, s))

    print("\n" + "=" * 62)
    print(f"  IF YOU HAD BOUGHT EVERY PICK — last {window_days} days")
    print(f"  {s['from_date']} to {s['to_date']}")
    print("=" * 62)
    print(f"  Stocks picked        : {s['picks']}")
    print(f"  Winners / losers     : {s['winners']} / {s['losers']}")
    print(f"  Win rate             : {s['win_rate']:.0f}%")
    print(f"  Average return       : {s['avg_ret']:+.2f}%")
    print(f"  Median return        : {s['median_ret']:+.2f}%")
    print(f"  Invested             : Rs{s['total_invested']:,.0f}")
    print(f"  Profit / loss        : Rs{s['total_pnl']:,.0f}")
    print(f"  Portfolio return     : {s['portfolio_ret']:+.2f}%")
    if s["nifty_ret"] is not None:
        print(f"  Nifty over same time : {s['nifty_ret']:+.2f}%")
        print(f"  Excess               : {s['excess']:+.2f} points")
    print(f"\n  Best  : {s['best_ticker'].replace('.NS','')} {s['best_ret']:+.1f}%")
    print(f"  Worst : {s['worst_ticker'].replace('.NS','')} {s['worst_ret']:+.1f}%")
    print(f"\n  Average best moment  : {s['avg_max_gain']:+.1f}%")
    print(f"  Average worst moment : {s['avg_max_dd']:+.1f}%")
    print(f"\n  {s['verdict']}")
    print("=" * 62)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, nargs="*", default=[7, 30],
                    help="windows to evaluate")
    ap.add_argument("--invest", type=float, default=10000,
                    help="notional amount per stock")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    try:
        conn.execute("SELECT 1 FROM shortlist_history LIMIT 1")
    except Exception:
        print("No pick history. Run backfill_history.py and rank.py first.")
        return

    conn.executescript(SCHEMA)
    for w in args.days:
        df = build(conn, w, args.invest)
        if df is None:
            print(f"\n  Not enough data for a {w}-day window.")
            continue
        report(df, w, conn)

    print("\n  Entry is the next day's OPEN after a pick, never the same day's")
    print("  close — you cannot buy a price that has already printed. No")
    print("  brokerage or slippage is deducted, so real results would be")
    print("  slightly worse.")
    conn.close()


if __name__ == "__main__":
    main()