"""
FULL NSE UNIVERSE
=================
Replaces the 44-stock demo list with every equity listed on the NSE.

This is a big job. The first run downloads about 3 years of history for
~2,000 stocks and takes 45-90 minutes depending on your connection.

IT IS SAFE TO INTERRUPT. Press Ctrl+C any time and run it again — it picks up
where it stopped. Nothing is lost.

Run with:   python build_universe.py
            python build_universe.py --list-only     (just refresh the stock list)
            python build_universe.py --years 5       (more history, slower)
"""

import argparse
import io
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta

import pandas as pd

DB = "market.db"
CSV_LOCAL = "EQUITY_L.csv"
BATCH = 20
PAUSE = 0.8
MIN_ROWS = 220          # need this much history for a 200-day average

NSE_URLS = [
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
]

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/122.0 Safari/537.36"),
    "Accept": "text/csv,application/csv,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


# ------------------------------------------------------------------
# Schema
# ------------------------------------------------------------------

def ensure_schema(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS instruments (
        ticker      TEXT PRIMARY KEY,
        name        TEXT,
        sector      TEXT,
        cap_bucket  TEXT,
        active      INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS prices_daily (
        ticker TEXT, date TEXT, open REAL, high REAL, low REAL,
        close REAL, volume INTEGER, PRIMARY KEY (ticker, date)
    );
    CREATE INDEX IF NOT EXISTS ix_d ON prices_daily(date);
    CREATE INDEX IF NOT EXISTS ix_t ON prices_daily(ticker);

    CREATE TABLE IF NOT EXISTS ingest_failures (
        ticker   TEXT PRIMARY KEY,
        fails    INTEGER DEFAULT 0,
        last_try TEXT,
        note     TEXT
    );
    """)
    # add columns if upgrading from the earlier schema
    cols = {r[1] for r in conn.execute("PRAGMA table_info(instruments)")}
    for col, decl in [("isin", "TEXT"), ("market_cap_cr", "REAL"),
                      ("series", "TEXT")]:
        if col not in cols:
            conn.execute(f"ALTER TABLE instruments ADD COLUMN {col} {decl}")
    conn.commit()


# ------------------------------------------------------------------
# Getting the list of every listed stock
# ------------------------------------------------------------------

def fetch_nse_list():
    """Try NSE directly. Returns a DataFrame or None."""
    try:
        import requests
    except ImportError:
        return None

    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        session.get("https://www.nseindia.com", timeout=12)
    except Exception:
        pass

    for url in NSE_URLS:
        try:
            print(f"  trying {url.split('/')[2]} ...")
            r = session.get(url, timeout=25)
            if r.status_code == 200 and len(r.content) > 5000:
                df = pd.read_csv(io.BytesIO(r.content))
                print(f"  got {len(df)} rows")
                return df
        except Exception as e:
            print(f"    failed: {type(e).__name__}")
    return None


def load_local_csv():
    if not os.path.exists(CSV_LOCAL):
        return None
    try:
        df = pd.read_csv(CSV_LOCAL)
        print(f"  read {len(df)} rows from {CSV_LOCAL}")
        return df
    except Exception as e:
        print(f"  couldn't read {CSV_LOCAL}: {e}")
        return None


def manual_instructions():
    print("\n" + "!" * 64)
    print("  COULDN'T DOWNLOAD THE STOCK LIST AUTOMATICALLY")
    print("!" * 64)
    print("""
  NSE blocks automated downloads. Do it manually once — takes a minute:

  1. Open this in your browser:
       https://www.nseindia.com/market-data/securities-available-for-trading

  2. Download the file called "Securities available for Equity segment"
     (it downloads as EQUITY_L.csv)

  3. Move it into your project folder:
       C:\\Users\\abhis\\swing-engine

  4. Run this script again.

  The list only changes when companies list or delist, so you'll rarely
  need to repeat this.
""")


def refresh_universe(conn):
    print("Getting the list of listed companies ...")
    df = fetch_nse_list()
    if df is None:
        df = load_local_csv()
    if df is None:
        manual_instructions()
        return 0

    df.columns = [c.strip().upper() for c in df.columns]
    # NSE's file sometimes has columns whose names differ only by whitespace,
    # which collide once trimmed. Keep the first of any duplicate.
    df = df.loc[:, ~df.columns.duplicated()]

    def col(*names):
        for n in names:
            if n in df.columns:
                return n
        return None

    c_sym = col("SYMBOL")
    c_name = col("NAME OF COMPANY", "COMPANY NAME", "NAME")
    c_series = col("SERIES")
    c_isin = col("ISIN NUMBER", "ISIN")

    if not c_sym:
        print("Unexpected file format — no SYMBOL column found.")
        return 0

    if c_series:
        df = df[df[c_series].astype(str).str.strip() == "EQ"]

    records = []
    for _, r in df.iterrows():
        sym = str(r[c_sym]).strip()
        if not sym or sym.lower() == "nan":
            continue
        records.append((
            f"{sym}.NS",
            str(r[c_name]).strip() if c_name else sym,
            str(r[c_isin]).strip() if c_isin else None,
            "EQ",
        ))

    conn.executemany(
        "INSERT INTO instruments (ticker, name, isin, series) VALUES (?,?,?,?) "
        "ON CONFLICT(ticker) DO UPDATE SET name=excluded.name, "
        "isin=excluded.isin, series=excluded.series",
        records)
    conn.commit()
    print(f"  {len(records)} equity symbols in the universe\n")
    return len(records)


# ------------------------------------------------------------------
# Downloading prices
# ------------------------------------------------------------------

def note_failure(conn, ticker, why):
    conn.execute(
        "INSERT INTO ingest_failures (ticker, fails, last_try, note) "
        "VALUES (?,1,?,?) ON CONFLICT(ticker) DO UPDATE SET "
        "fails = fails + 1, last_try = excluded.last_try, note = excluded.note",
        (ticker, date.today().isoformat(), why))
    n = conn.execute("SELECT fails FROM ingest_failures WHERE ticker=?",
                     (ticker,)).fetchone()[0]
    if n >= 3:
        conn.execute("UPDATE instruments SET active=0 WHERE ticker=?", (ticker,))


def store(conn, ticker, df):
    df = df.dropna(subset=["Close"])
    if df.empty:
        return 0
    rec = []
    for ix, r in df.iterrows():
        try:
            rec.append((ticker, ix.strftime("%Y-%m-%d"),
                        float(r["Open"]), float(r["High"]), float(r["Low"]),
                        float(r["Close"]),
                        int(r["Volume"]) if not pd.isna(r["Volume"]) else 0))
        except Exception:
            continue
    if not rec:
        return 0
    conn.executemany(
        "INSERT OR REPLACE INTO prices_daily VALUES (?,?,?,?,?,?,?)", rec)
    return len(rec)


def ingest(conn, years):
    import yfinance as yf

    today = date.today()

    todo = [r[0] for r in conn.execute("""
        SELECT i.ticker FROM instruments i
        LEFT JOIN (SELECT ticker, MAX(date) md, COUNT(*) n
                   FROM prices_daily GROUP BY ticker) p
          ON p.ticker = i.ticker
        WHERE i.active = 1
          AND (p.md IS NULL OR p.md < date('now','-3 day') OR p.n < ?)
        ORDER BY i.ticker
    """, (MIN_ROWS,))]

    done_already = conn.execute(
        "SELECT COUNT(DISTINCT ticker) FROM prices_daily").fetchone()[0]

    if not todo:
        print("Everything is already up to date.")
        return

    print(f"{done_already} stocks already have data.")
    print(f"{len(todo)} still to fetch. Roughly "
          f"{len(todo)/BATCH*(PAUSE+1.4)/60:.0f} minutes.\n")
    print("Safe to stop with Ctrl+C — rerun and it continues.\n")

    ok = bad = rows = 0
    start_time = time.time()

    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]

        # If everything in the batch already has data, only ask for recent days
        stored = [conn.execute("SELECT MAX(date) FROM prices_daily WHERE ticker=?",
                               (t,)).fetchone()[0] for t in batch]
        if all(stored):
            begin = datetime.strptime(min(stored), "%Y-%m-%d").date() - timedelta(days=5)
        else:
            begin = today - timedelta(days=365 * years)

        n = i // BATCH + 1
        total_batches = (len(todo) + BATCH - 1) // BATCH
        elapsed = time.time() - start_time
        eta = (elapsed / n * (total_batches - n) / 60) if n > 1 else 0
        print(f"  [{n:>3}/{total_batches}] {batch[0].replace('.NS','')} ... "
              f"({ok} done, {bad} failed"
              + (f", ~{eta:.0f} min left)" if eta else ")"))

        try:
            raw = yf.download(batch, start=begin, end=today + timedelta(days=1),
                              auto_adjust=True, progress=False,
                              group_by="ticker", threads=False)
        except Exception as e:
            for t in batch:
                note_failure(conn, t, f"batch error: {type(e).__name__}")
            bad += len(batch)
            conn.commit()
            continue

        for t in batch:
            try:
                if raw is None or len(raw) == 0:
                    note_failure(conn, t, "empty response")
                    bad += 1
                    continue
                if isinstance(raw.columns, pd.MultiIndex):
                    if t not in raw.columns.get_level_values(0):
                        note_failure(conn, t, "not in response")
                        bad += 1
                        continue
                    df = raw[t]
                else:
                    df = raw
                added = store(conn, t, df)
                if added:
                    rows += added
                    ok += 1
                    conn.execute("DELETE FROM ingest_failures WHERE ticker=?", (t,))
                else:
                    note_failure(conn, t, "no usable rows")
                    bad += 1
            except Exception as e:
                note_failure(conn, t, type(e).__name__)
                bad += 1

        conn.commit()
        time.sleep(PAUSE)

    print(f"\n  fetched {ok}, failed {bad}, {rows:,} new rows")


# ------------------------------------------------------------------

def report(conn):
    n_inst = conn.execute("SELECT COUNT(*) FROM instruments WHERE active=1").fetchone()[0]
    n_dead = conn.execute("SELECT COUNT(*) FROM instruments WHERE active=0").fetchone()[0]
    n_px = conn.execute("SELECT COUNT(*) FROM prices_daily").fetchone()[0]
    n_tk = conn.execute("SELECT COUNT(DISTINCT ticker) FROM prices_daily").fetchone()[0]
    lo, hi = conn.execute("SELECT MIN(date), MAX(date) FROM prices_daily").fetchone()
    usable = conn.execute(
        "SELECT COUNT(*) FROM (SELECT ticker FROM prices_daily "
        "GROUP BY ticker HAVING COUNT(*) >= ?)", (MIN_ROWS,)).fetchone()[0]

    size = os.path.getsize(DB) / 1e6 if os.path.exists(DB) else 0

    print("\n" + "=" * 56)
    print("  UNIVERSE STATUS")
    print("=" * 56)
    print(f"  Companies tracked      : {n_inst:,}")
    print(f"  Marked inactive        : {n_dead:,}")
    print(f"  With price data        : {n_tk:,}")
    print(f"  With enough history    : {usable:,}  (screenable)")
    print(f"  Total price rows       : {n_px:,}")
    print(f"  Date range             : {lo} to {hi}")
    print(f"  Database size          : {size:.0f} MB")
    print("=" * 56)

    stuck = conn.execute(
        "SELECT COUNT(*) FROM ingest_failures WHERE fails >= 3").fetchone()[0]
    if stuck:
        print(f"\n  {stuck} symbols failed 3+ times and were switched off.")
        print("  Usually delisted or renamed. See the ingest_failures table.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=3,
                    help="years of history (default 3)")
    ap.add_argument("--list-only", action="store_true",
                    help="refresh the company list, skip price download")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_schema(conn)

    refresh_universe(conn)

    if not args.list_only:
        try:
            ingest(conn, args.years)
        except KeyboardInterrupt:
            conn.commit()
            print("\n\n  Stopped. Progress is saved — rerun to continue.\n")

    report(conn)
    conn.close()


if __name__ == "__main__":
    main()