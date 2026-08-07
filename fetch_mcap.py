"""
MARKET CAP + SIZE CLASSIFICATION
================================
Fetches market capitalisation for every stock and sorts them into size
buckets. Run this ONCE after build_universe.py finishes.

Takes 15-30 minutes for ~2,000 stocks. Safe to interrupt — rerun to continue.

Run with:   python fetch_mcap.py
            python fetch_mcap.py --refresh    (redo everything)
"""

import argparse
import sqlite3
import sys
import time
from datetime import date

DB = "market.db"
BATCH_PAUSE = 0.25

# Thresholds in ₹ crore. These follow SEBI's broad convention:
# top 100 by mcap = large, next 150 = mid, rest = small.
LARGE = 50_000
MID = 15_000
SMALL = 1_000


def classify(cr):
    if cr is None or cr <= 0:
        return None
    if cr >= LARGE:
        return "large"
    if cr >= MID:
        return "mid"
    if cr >= SMALL:
        return "small"
    return "micro"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true",
                    help="refetch even stocks that already have a value")
    args = ap.parse_args()

    try:
        import yfinance as yf
    except ImportError:
        print("Run: pip install yfinance")
        sys.exit(1)

    conn = sqlite3.connect(DB)

    cols = {r[1] for r in conn.execute("PRAGMA table_info(instruments)")}
    if "market_cap_cr" not in cols:
        conn.execute("ALTER TABLE instruments ADD COLUMN market_cap_cr REAL")
    if "mcap_updated" not in cols:
        conn.execute("ALTER TABLE instruments ADD COLUMN mcap_updated TEXT")
    conn.commit()

    # Only bother with stocks that actually have price history
    where = "" if args.refresh else "AND (i.market_cap_cr IS NULL)"
    todo = [r[0] for r in conn.execute(f"""
        SELECT i.ticker FROM instruments i
        WHERE i.active = 1 {where}
          AND EXISTS (SELECT 1 FROM prices_daily p WHERE p.ticker = i.ticker)
        ORDER BY i.ticker
    """)]

    if not todo:
        print("Nothing to fetch — every stock already has a market cap.")
    else:
        print(f"Fetching market cap for {todo and len(todo)} stocks.")
        print(f"Roughly {len(todo) * 0.6 / 60:.0f} minutes. "
              f"Ctrl+C is safe — rerun to continue.\n")

        ok = bad = 0
        today = date.today().isoformat()
        start = time.time()

        try:
            for i, t in enumerate(todo, 1):
                cr = None
                try:
                    fi = yf.Ticker(t).fast_info
                    mc = None
                    # Attribute access handles the snake_case alias; .get() does
                    # not, because the underlying key is 'marketCap'.
                    try:
                        mc = fi.market_cap
                    except Exception:
                        mc = None
                    if not mc:
                        try:
                            mc = fi["marketCap"]
                        except Exception:
                            mc = None
                    if not mc:
                        # Last resort: shares outstanding x latest price
                        try:
                            sh, lp = fi.shares, fi.last_price
                            if sh and lp:
                                mc = float(sh) * float(lp)
                        except Exception:
                            mc = None
                    if mc:
                        cr = float(mc) / 1e7        # rupees -> crore
                except Exception:
                    cr = None

                if cr and cr > 0:
                    conn.execute(
                        "UPDATE instruments SET market_cap_cr=?, cap_bucket=?, "
                        "mcap_updated=? WHERE ticker=?",
                        (round(cr, 1), classify(cr), today, t))
                    ok += 1
                else:
                    bad += 1

                if i % 25 == 0:
                    conn.commit()
                    elapsed = time.time() - start
                    eta = elapsed / i * (len(todo) - i) / 60
                    print(f"  {i:>5}/{len(todo)}   found {ok}, missing {bad}"
                          f"   ~{eta:.0f} min left")

                time.sleep(BATCH_PAUSE)
        except KeyboardInterrupt:
            conn.commit()
            print("\n  Stopped. Progress saved — rerun to continue.\n")

        conn.commit()
        print(f"\n  Found {ok}, missing {bad}")

    # ---- report ----
    print("\n" + "=" * 56)
    print("  SIZE BREAKDOWN")
    print("=" * 56)
    rows = conn.execute("""
        SELECT cap_bucket, COUNT(*), ROUND(MIN(market_cap_cr)),
               ROUND(MAX(market_cap_cr))
        FROM instruments WHERE market_cap_cr IS NOT NULL AND active=1
        GROUP BY cap_bucket
        ORDER BY MAX(market_cap_cr) DESC
    """).fetchall()
    for bucket, n, lo, hi in rows:
        print(f"  {bucket or 'unknown':<8} {n:>5} stocks   "
              f"₹{lo:,.0f}cr to ₹{hi:,.0f}cr")

    missing = conn.execute(
        "SELECT COUNT(*) FROM instruments WHERE active=1 AND market_cap_cr IS NULL"
    ).fetchone()[0]
    print(f"\n  {missing} stocks still have no market cap.")
    print("  They'll be treated as unknown size and excluded from the")
    print("  size-based modes. Mostly illiquid or recently listed names.")
    print("=" * 56)

    conn.close()


if __name__ == "__main__":
    main()