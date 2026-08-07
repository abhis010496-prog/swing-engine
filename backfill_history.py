"""
BACKFILL SHORTLIST HISTORY
==========================
Replays the daily qualification test over the past N trading days using the
price history you already have, so the monthly view works immediately
instead of taking a month to fill up.

Strictly no lookahead: for each past date, only data up to that date is used.

Run ONCE:   python backfill_history.py
            python backfill_history.py --days 120

Existing rows are never overwritten — real recorded history always wins.
"""

import argparse
import sqlite3
import time

import numpy as np
import pandas as pd

DB = "market.db"
BENCHMARK = "^NSEI"
MIN_ROWS = 260

SCHEMA = """
CREATE TABLE IF NOT EXISTS shortlist_history (
    ticker TEXT NOT NULL,
    as_of  TEXT NOT NULL,
    score  INTEGER,
    PRIMARY KEY (ticker, as_of)
);
CREATE INDEX IF NOT EXISTS ix_hist_date ON shortlist_history(as_of);
"""


def qualifying_series(g, bench_ret63):
    """
    Return a DataFrame indexed by date with a boolean 'ok' column and the
    score, computed the same way rank.py does — but for every date at once.
    """
    close, volume = g["close"], g["volume"]
    if len(close) < MIN_ROWS:
        return None

    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()
    high52 = close.rolling(252).max()
    from_high = (close / high52 - 1) * 100

    vol20 = volume.rolling(20).mean()
    vol5 = volume.rolling(5).mean()
    vol_x = (vol5 / vol20).replace([np.inf, -np.inf], np.nan)
    turnover = vol20 * close / 1e7

    hl = g["high"] - g["low"]
    hc = (g["high"] - close.shift()).abs()
    lc = (g["low"] - close.shift()).abs()
    atr = pd.concat([hl, hc, lc], axis=1).max(axis=1).ewm(alpha=1/14,
                                                          adjust=False).mean()
    atr_pct = atr / close * 100

    ret63 = (close / close.shift(63) - 1) * 100
    rs = ret63 - bench_ret63.reindex(close.index).ffill()

    trend = (close > ma50) & (ma50 > ma200)
    ok = (trend & (rs > 0) & (from_high > -15) & (turnover > 8))

    score = pd.Series(0, index=close.index, dtype=float)
    score += np.where(close > ma50, 7, 0)
    score += np.where(ma50 > ma200, 8, 0)
    score += np.where(rs > 20, 15, np.where(rs > 10, 11, np.where(rs > 0, 7, 0)))
    score += np.where(from_high > -3, 12,
                      np.where(from_high > -8, 9, np.where(from_high > -15, 5, 0)))
    score += np.where(vol_x > 1.6, 5, np.where(vol_x > 1.2, 3,
                      np.where(vol_x > 0.8, 1, 0)))
    score += np.where(turnover > 50, 5, np.where(turnover > 10, 3,
                      np.where(turnover > 3, 1, 0)))

    return pd.DataFrame({"ok": ok.fillna(False), "score": score,
                         "atr_pct": atr_pct, "from_high": from_high,
                         "rs": rs, "turnover": turnover})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90,
                    help="how many trading days to reconstruct")
    args = ap.parse_args()

    t0 = time.time()
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)

    have = conn.execute("SELECT COUNT(DISTINCT as_of) FROM shortlist_history"
                        ).fetchone()[0]
    print(f"History currently covers {have} day(s).")
    print("Loading price history ...")

    prices = pd.read_sql(
        "SELECT ticker, date, high, low, close, volume FROM prices_daily "
        "ORDER BY ticker, date", conn, parse_dates=["date"])
    print(f"  {len(prices):,} rows")

    b = prices[prices["ticker"] == BENCHMARK].set_index("date")["close"]
    if b.empty:
        print("No benchmark data. Cannot compute relative strength.")
        return
    bench_ret63 = (b / b.shift(63) - 1) * 100

    all_dates = sorted(prices["date"].unique())
    target_dates = set(pd.to_datetime(all_dates[-args.days:]))
    print(f"Reconstructing {len(target_dates)} trading days "
          f"({pd.Timestamp(min(target_dates)).date()} to "
          f"{pd.Timestamp(max(target_dates)).date()})\n")

    rows, done, skipped = [], 0, 0
    for ticker, g in prices.groupby("ticker", sort=False):
        if ticker == BENCHMARK:
            continue
        g = g.set_index("date")
        res = qualifying_series(g, bench_ret63)
        if res is None:
            skipped += 1
            continue

        res = res[res.index.isin(target_dates)]
        hits = res[res["ok"]]
        for d, r in hits.iterrows():
            rows.append((ticker, d.strftime("%Y-%m-%d"), int(r["score"])))
        done += 1
        if done % 300 == 0:
            print(f"  {done} stocks processed, {len(rows):,} qualifying days found")

    # Never overwrite genuinely recorded history
    conn.executemany(
        "INSERT OR IGNORE INTO shortlist_history VALUES (?,?,?)", rows)
    conn.commit()

    days_now = conn.execute("SELECT COUNT(DISTINCT as_of) FROM shortlist_history"
                            ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM shortlist_history").fetchone()[0]

    print("\n" + "=" * 58)
    print("  BACKFILL COMPLETE")
    print("=" * 58)
    print(f"  Stocks processed     : {done:,}  ({skipped:,} too short)")
    print(f"  Qualifying days added: {len(rows):,}")
    print(f"  History now covers   : {days_now} trading days")
    print(f"  Total records        : {total:,}")
    print(f"  Took                 : {time.time()-t0:.0f} seconds")

    print("\n  Most consistent stocks over the period:")
    for tk, n in conn.execute(
            "SELECT ticker, COUNT(*) c FROM shortlist_history "
            "GROUP BY ticker ORDER BY c DESC LIMIT 10"):
        print(f"    {tk.replace('.NS',''):<16} qualified {n} of {days_now} days")
    print("=" * 58)
    print("\nNext: python monthly.py")

    conn.close()


if __name__ == "__main__":
    main()