"""
FUNDAMENTALS
============
Fetches the business numbers behind each stock — earnings growth, margins,
debt, returns and valuation — and stores them.

Run this ONCE after build_universe.py. It takes 45-75 minutes for ~1,900
stocks because each company needs its own request. Ctrl+C is safe; rerun
and it continues from where it stopped.

Run with:   python fetch_fundamentals.py
            python fetch_fundamentals.py --top 400   (biggest companies only)
            python fetch_fundamentals.py --refresh   (redo everything)

Refresh it about once a quarter, after results season.
"""

import argparse
import sqlite3
import sys
import time
from datetime import date

DB = "market.db"
PAUSE = 0.35

SCHEMA = """
CREATE TABLE IF NOT EXISTS fundamentals (
    ticker            TEXT PRIMARY KEY,
    sector            TEXT,
    industry          TEXT,
    pe                REAL,   -- trailing price to earnings
    pb                REAL,   -- price to book
    roe               REAL,   -- return on equity, %
    debt_to_equity    REAL,   -- x (1.0 = debt equals equity)
    profit_margin     REAL,   -- %
    operating_margin  REAL,   -- %
    revenue_growth    REAL,   -- % year on year
    earnings_growth   REAL,   -- % year on year
    qtr_earn_growth   REAL,   -- % most recent quarter, year on year
    current_ratio     REAL,
    free_cashflow_cr  REAL,
    book_value        REAL,
    updated           TEXT
);
"""


def pct(v):
    """yfinance gives fractions for most ratios. Convert to percent."""
    try:
        f = float(v)
        return round(f * 100, 2)
    except (TypeError, ValueError):
        return None


def num(v, scale=1.0):
    try:
        return round(float(v) * scale, 3)
    except (TypeError, ValueError):
        return None


def fetch_one(yf, ticker):
    info = yf.Ticker(ticker).info
    if not info or len(info) < 5:
        return None

    # yfinance reports debtToEquity as a percentage (21.5 means 0.215x)
    dte = info.get("debtToEquity")
    dte = round(float(dte) / 100, 3) if dte not in (None, "") else None

    return dict(
        sector=info.get("sector"),
        industry=info.get("industry"),
        pe=num(info.get("trailingPE")),
        pb=num(info.get("priceToBook")),
        roe=pct(info.get("returnOnEquity")),
        debt_to_equity=dte,
        profit_margin=pct(info.get("profitMargins")),
        operating_margin=pct(info.get("operatingMargins")),
        revenue_growth=pct(info.get("revenueGrowth")),
        earnings_growth=pct(info.get("earningsGrowth")),
        qtr_earn_growth=pct(info.get("earningsQuarterlyGrowth")),
        current_ratio=num(info.get("currentRatio")),
        free_cashflow_cr=num(info.get("freeCashflow"), 1 / 1e7),
        book_value=num(info.get("bookValue")),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=0,
                    help="only the N largest companies (0 = all)")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    try:
        import yfinance as yf
    except ImportError:
        print("Run: pip install yfinance")
        sys.exit(1)

    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)

    where = "" if args.refresh else \
        "AND i.ticker NOT IN (SELECT ticker FROM fundamentals)"
    limit = f"LIMIT {args.top}" if args.top else ""

    todo = [r[0] for r in conn.execute(f"""
        SELECT i.ticker FROM instruments i
        WHERE i.active = 1 {where}
          AND EXISTS (SELECT 1 FROM prices_daily p WHERE p.ticker = i.ticker)
        ORDER BY COALESCE(i.market_cap_cr, 0) DESC {limit}
    """)]

    if not todo:
        print("Nothing to fetch — every stock already has fundamentals.")
    else:
        print(f"Fetching fundamentals for {len(todo)} companies.")
        print(f"Roughly {len(todo)*1.6/60:.0f} minutes. Ctrl+C is safe.\n")

        ok = bad = 0
        today = date.today().isoformat()
        start = time.time()

        try:
            for i, t in enumerate(todo, 1):
                try:
                    d = fetch_one(yf, t)
                except Exception:
                    d = None

                if d:
                    conn.execute(
                        "INSERT OR REPLACE INTO fundamentals VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (t, d["sector"], d["industry"], d["pe"], d["pb"],
                         d["roe"], d["debt_to_equity"], d["profit_margin"],
                         d["operating_margin"], d["revenue_growth"],
                         d["earnings_growth"], d["qtr_earn_growth"],
                         d["current_ratio"], d["free_cashflow_cr"],
                         d["book_value"], today))
                    # backfill the sector on the instrument too
                    if d["sector"]:
                        conn.execute(
                            "UPDATE instruments SET sector=? WHERE ticker=? "
                            "AND (sector IS NULL OR sector='' OR sector='—')",
                            (d["sector"], t))
                    ok += 1
                else:
                    bad += 1

                if i % 25 == 0:
                    conn.commit()
                    eta = (time.time() - start) / i * (len(todo) - i) / 60
                    print(f"  {i:>5}/{len(todo)}   found {ok}, missing {bad}"
                          f"   ~{eta:.0f} min left")
                time.sleep(PAUSE)
        except KeyboardInterrupt:
            conn.commit()
            print("\n  Stopped. Progress saved — rerun to continue.\n")

        conn.commit()
        print(f"\n  Found {ok}, missing {bad}")

    # ---------------- what we ended up with ----------------
    q = lambda w: conn.execute(
        f"SELECT COUNT(*) FROM fundamentals WHERE {w}").fetchone()[0]
    total = q("1=1")

    print("\n" + "=" * 58)
    print("  FUNDAMENTALS COVERAGE")
    print("=" * 58)
    if total:
        for label, col in [("Earnings growth", "earnings_growth"),
                           ("Revenue growth", "revenue_growth"),
                           ("Operating margin", "operating_margin"),
                           ("Debt to equity", "debt_to_equity"),
                           ("Return on equity", "roe"),
                           ("P/E ratio", "pe"),
                           ("Sector", "sector")]:
            n = q(f"{col} IS NOT NULL")
            print(f"  {label:<20} {n:>5} of {total}  ({n/total*100:>4.0f}%)")

        print("\n  Warning signs already visible:")
        print(f"    Loss-making (negative margin)   {q('profit_margin < 0'):>5}")
        print(f"    Heavily indebted (D/E over 2x)  {q('debt_to_equity > 2'):>5}")
        print(f"    Shrinking revenue               {q('revenue_growth < 0'):>5}")
    else:
        print("  Nothing stored yet.")
    print("=" * 58)
    print("\n  NOT available from this source: promoter pledging, promoter")
    print("  holding changes, auditor resignations. Those need BSE filings.")
    print("\nNext: python rank.py")

    conn.close()


if __name__ == "__main__":
    main()