"""
NEWS INGESTION
==============
Pulls recent headlines from Google News RSS.

With ~2,000 stocks it makes no sense to fetch news for all of them — most
will never appear in a shortlist. By default this fetches news only for the
top-scoring stocks, which is where catalysts actually matter.

Run with:   python ingest_news.py            (top 150 by score)
            python ingest_news.py --top 300  (more, slower)
            python ingest_news.py --all      (everything — takes ~45 min)
"""

import argparse
import sqlite3
import sys
import time
import urllib.parse
from datetime import datetime, timedelta

try:
    import feedparser
except ImportError:
    print("Missing package. Run:  pip install feedparser")
    sys.exit(1)

DB = "market.db"
DAYS_TO_KEEP = 45
PAUSE = 1.1

SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, published TEXT,
    source TEXT, headline TEXT NOT NULL, url TEXT, fetched_at TEXT,
    UNIQUE (ticker, headline));
CREATE INDEX IF NOT EXISTS ix_news_ticker ON news(ticker);
CREATE INDEX IF NOT EXISTS ix_news_pub ON news(published);
"""


def feed_url(company):
    q = urllib.parse.quote(f'"{company}" (stock OR shares OR NSE OR BSE)')
    return f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"


def entry_date(e):
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(e, attr, None)
        if t:
            try:
                return datetime(*t[:6]).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def clean(title, source):
    if source and title.endswith(f" - {source}"):
        return title[: -(len(source) + 3)].strip()
    return title.strip()


def pick_targets(conn, top, everything):
    """Which companies are worth fetching news for."""
    if everything:
        return conn.execute(
            "SELECT ticker, name FROM instruments WHERE active=1 ORDER BY ticker"
        ).fetchall()

    # Prefer the scores table — those are the stocks that could actually
    # show up in a shortlist.
    try:
        rows = conn.execute(
            "SELECT ticker, name FROM scores WHERE trend = 1 "
            "ORDER BY score DESC LIMIT ?", (top,)).fetchall()
        if rows:
            return rows
    except Exception:
        pass

    # No scores yet — fall back to the largest companies we know about.
    return conn.execute(
        "SELECT ticker, name FROM instruments WHERE active=1 "
        "AND market_cap_cr IS NOT NULL ORDER BY market_cap_cr DESC LIMIT ?",
        (top,)).fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=150)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)

    targets = pick_targets(conn, args.top, args.all)
    if not targets:
        print("Nothing to fetch. Run build_universe.py and rank.py first.")
        return

    print(f"Fetching news for {len(targets)} companies "
          f"(~{len(targets)*PAUSE/60:.0f} minutes).")
    print("These are the highest-scoring stocks — the only ones that could")
    print("appear in a shortlist. Ctrl+C is safe.\n")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    added = seen = failed = 0

    try:
        for i, (ticker, name) in enumerate(targets, 1):
            try:
                entries = feedparser.parse(feed_url(name)).entries[:12]
            except Exception:
                failed += 1
                continue

            for e in entries:
                src = ""
                if hasattr(e, "source") and hasattr(e.source, "title"):
                    src = e.source.title
                try:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO news (ticker,published,source,"
                        "headline,url,fetched_at) VALUES (?,?,?,?,?,?)",
                        (ticker, entry_date(e), src, clean(e.title, src),
                         getattr(e, "link", ""), now))
                    if cur.rowcount:
                        added += 1
                    else:
                        seen += 1
                except Exception:
                    pass

            if i % 10 == 0:
                conn.commit()
                print(f"  {i:>4}/{len(targets)}   {added} new headlines")
            time.sleep(PAUSE)
    except KeyboardInterrupt:
        print("\n  Stopped — everything fetched so far is saved.\n")

    conn.commit()

    cutoff = (datetime.now() - timedelta(days=DAYS_TO_KEEP)).strftime("%Y-%m-%d")
    purged = conn.execute("DELETE FROM news WHERE published < ?", (cutoff,)).rowcount
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]

    print("\n" + "=" * 48)
    print(f"  New headlines   : {added}")
    print(f"  Already had     : {seen}")
    print(f"  Failed          : {failed}")
    print(f"  Purged (>{DAYS_TO_KEEP}d)  : {purged}")
    print(f"  Total stored    : {total}")
    print("=" * 48)
    print("\nNext: python rank.py  (so catalyst scores pick these up)")

    conn.close()


if __name__ == "__main__":
    main()