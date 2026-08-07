"""
MAKE A DEPLOY DATABASE
======================
Your market.db is ~197MB. GitHub caps files at 100MB, and Render's free tier
gives about 512MB of memory — loading 1.36 million price rows into pandas
there would run out and crash.

This builds a trimmed copy that keeps everything the dashboard needs to
display, and drops what it does not.

    kept     the stocks that currently qualify, are in your pool, are on
             your watchlist, or are held in paper trading — plus the index
    kept     about 400 trading days of prices for those names
    kept     scores, monthly/weekly lists, pool, watchlist, paper trading,
             news, fundamentals, pick history
    dropped  price history for the ~1,900 stocks nothing refers to
    dropped  anything older than the window

Run with:   python make_deploy_db.py
            python make_deploy_db.py --days 250 --max-stocks 300

Then commit deploy.db (it should be a few MB) and push.
The dashboard uses deploy.db automatically when market.db is absent.
"""

import argparse
import os
import shutil
import sqlite3

SRC = "market.db"
DST = "deploy.db"


def table_exists(conn, name):
    return conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone()[0] > 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=400,
                    help="trading days of price history to keep")
    ap.add_argument("--max-stocks", type=int, default=400)
    args = ap.parse_args()

    if not os.path.exists(SRC):
        print(f"{SRC} not found. Run this in your swing-engine folder.")
        return

    if os.path.exists(DST):
        os.remove(DST)
    print(f"Copying {SRC} -> {DST} ...")
    shutil.copy(SRC, DST)

    conn = sqlite3.connect(DST)

    # ---- which tickers must survive ----
    keep = {"^NSEI", "^CRSLDX"}
    sources = [
        ("scores", "SELECT ticker FROM scores WHERE qualifies=1"),
        ("scores", "SELECT ticker FROM scores ORDER BY score DESC LIMIT 250"),
        ("monthly_pool", "SELECT symbol FROM monthly_pool"),
        ("watchlist", "SELECT ticker FROM watchlist"),
        ("paper_positions", "SELECT ticker FROM paper_positions"),
        ("monthly_shortlist", "SELECT ticker FROM monthly_shortlist"),
        ("daily_shortlist", "SELECT symbol FROM daily_shortlist"),
    ]
    for tbl, q in sources:
        if table_exists(conn, tbl):
            try:
                keep.update(r[0] for r in conn.execute(q))
            except Exception:
                pass

    keep = {t for t in keep if t}
    if len(keep) > args.max_stocks + 2:
        ranked = [r[0] for r in conn.execute(
            "SELECT ticker FROM scores ORDER BY score DESC")]
        priority = [t for t in ranked if t in keep][:args.max_stocks]
        keep = set(priority) | {"^NSEI", "^CRSLDX"}

    print(f"  keeping {len(keep)} tickers")

    cutoff = conn.execute(
        "SELECT MIN(date) FROM (SELECT DISTINCT date FROM prices_daily "
        "ORDER BY date DESC LIMIT ?)", (args.days,)).fetchone()[0]
    print(f"  keeping prices from {cutoff} onward")

    marks = ",".join("?" * len(keep))
    before = conn.execute("SELECT COUNT(*) FROM prices_daily").fetchone()[0]
    conn.execute(
        f"DELETE FROM prices_daily WHERE ticker NOT IN ({marks}) OR date < ?",
        list(keep) + [cutoff])
    after = conn.execute("SELECT COUNT(*) FROM prices_daily").fetchone()[0]
    print(f"  price rows: {before:,} -> {after:,}")

    # trim the wide reference tables to match
    if table_exists(conn, "instruments"):
        conn.execute(
            f"DELETE FROM instruments WHERE ticker NOT IN ({marks})", list(keep))
    for tbl, col in [("scores", "ticker"), ("news", "ticker"),
                     ("fundamentals", "ticker"), ("shortlist_history", "ticker")]:
        if table_exists(conn, tbl):
            try:
                conn.execute(
                    f"DELETE FROM {tbl} WHERE {col} NOT IN ({marks})", list(keep))
            except Exception:
                pass

    # shadow_signals is a research log, not needed to display anything
    if table_exists(conn, "shadow_signals"):
        conn.execute("DELETE FROM shadow_signals")

    conn.commit()
    print("  compacting ...")
    conn.execute("VACUUM")
    conn.close()

    mb = os.path.getsize(DST) / 1e6
    print("\n" + "=" * 52)
    print(f"  {DST} is {mb:.1f} MB")
    if mb > 90:
        print("  Still too big for GitHub. Rerun with:")
        print("     python make_deploy_db.py --days 250 --max-stocks 200")
    else:
        print("  Small enough to commit.")
        print("\n  Next:")
        print("     git add deploy.db")
        print('     git commit -m "add deploy database"')
        print("     git push")
    print("=" * 52)


if __name__ == "__main__":
    main()