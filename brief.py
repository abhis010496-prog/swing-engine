"""
STEP 4b — The morning briefing
==============================
Combines the technical score with the news flow. This is the thing you open
every morning.

Run with:   python brief.py
            python brief.py small
"""

import sqlite3
import sys
from datetime import datetime, timedelta

import pandas as pd

DB = "market.db"
BENCHMARK = "^NSEI"

CATALYST_WORDS = {
    "order": "Order win", "contract": "Order win", "wins": "Order win",
    "bags": "Order win", "results": "Results", "profit": "Results",
    "revenue": "Results", "capex": "Expansion", "expansion": "Expansion",
    "capacity": "Expansion", "acquisition": "M&A", "acquires": "M&A",
    "merger": "M&A", "stake": "M&A", "upgrade": "Rating",
    "downgrade": "Rating", "rating": "Rating", "buyback": "Capital action",
    "dividend": "Capital action", "bonus": "Capital action",
    "block deal": "Block deal", "bulk deal": "Block deal",
}


def tag(headline):
    low = headline.lower()
    for word, label in CATALYST_WORDS.items():
        if word in low:
            return label
    return None


def technical_score(df, bench):
    close, volume = df["close"], df["volume"]
    if len(close) < 220:
        return None

    last = float(close.iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1])
    from_high = (last / float(close.tail(252).max()) - 1) * 100
    vol20 = float(volume.rolling(20).mean().iloc[-1])
    vol_ratio = float(volume.iloc[-1]) / vol20 if vol20 else 0
    turnover = (vol20 * last) / 1e7

    rs = None
    if bench is not None and len(close) > 63:
        common = bench.reindex(close.index).ffill()
        if common.notna().sum() > 63:
            rs = ((last / float(close.iloc[-63]) - 1) * 100
                  - (float(common.iloc[-1]) / float(common.iloc[-63]) - 1) * 100)

    s = 0
    if last > ma50: s += 7
    if ma50 > ma200: s += 8
    if last > ma20: s += 5
    if rs is not None:
        s += 15 if rs > 20 else 11 if rs > 10 else 7 if rs > 0 else 0
    s += 12 if from_high > -3 else 9 if from_high > -8 else 5 if from_high > -15 else 0
    s += 8 if vol_ratio > 2 else 5 if vol_ratio > 1.3 else 2 if vol_ratio > 0.8 else 0
    s += 5 if turnover > 50 else 3 if turnover > 10 else 1 if turnover > 3 else 0

    return {
        "price": last, "from_high": from_high, "rs": rs,
        "vol_x": vol_ratio, "turnover": turnover,
        "trend": last > ma50 > ma200, "tech": s,
    }


def main():
    wanted = [a.lower() for a in sys.argv[1:]] or ["large", "mid", "small", "micro"]
    conn = sqlite3.connect(DB)

    has_news = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='news'"
    ).fetchone()[0] > 0

    bench = None
    b = pd.read_sql("SELECT date, close FROM prices_daily WHERE ticker=? ORDER BY date",
                    conn, params=(BENCHMARK,), parse_dates=["date"], index_col="date")
    if not b.empty:
        bench = b["close"]

    inst = pd.read_sql(
        "SELECT ticker, name, sector, cap_bucket FROM instruments WHERE active=1", conn)
    inst = inst[inst["cap_bucket"].isin(wanted)]

    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    rows = []
    for _, r in inst.iterrows():
        px = pd.read_sql(
            "SELECT date, close, volume FROM prices_daily WHERE ticker=? ORDER BY date",
            conn, params=(r["ticker"],), parse_dates=["date"], index_col="date")
        if px.empty:
            continue
        m = technical_score(px, bench)
        if m is None:
            continue

        headlines, catalysts = [], []
        if has_news:
            hl = conn.execute(
                "SELECT published, headline, source FROM news "
                "WHERE ticker=? AND published >= ? ORDER BY published DESC LIMIT 6",
                (r["ticker"], week_ago)).fetchall()
            for pub, head, src in hl:
                t = tag(head)
                headlines.append((pub, head, src, t))
                if t:
                    catalysts.append(t)

        # Catalyst score, out of 20
        cat = 0
        if catalysts:
            cat = min(20, 6 + 4 * len(set(catalysts)) + 2 * (len(catalysts) - 1))

        rows.append({
            "ticker": r["ticker"].replace(".NS", ""),
            "name": r["name"], "sector": r["sector"], "cap": r["cap_bucket"],
            "total": m["tech"] + cat, "tech": m["tech"], "cat": cat,
            "price": m["price"], "rs": m["rs"], "from_high": m["from_high"],
            "vol_x": m["vol_x"], "turnover": m["turnover"], "trend": m["trend"],
            "news": headlines, "news_7d": len(headlines),
        })

    if not rows:
        print("Nothing to report. Check the database has price history.")
        return

    rows.sort(key=lambda x: -x["total"])
    as_of = conn.execute("SELECT MAX(date) FROM prices_daily").fetchone()[0]

    print("=" * 78)
    print(f"  MORNING BRIEF — data as of {as_of}")
    print(f"  {len(rows)} stocks   |   buckets: {', '.join(wanted)}")
    if not has_news:
        print("  (no news table yet — run ingest_news.py to add the catalyst layer)")
    print("=" * 78)

    qualified = [
        r for r in rows
        if r["trend"] and r["rs"] is not None and r["rs"] > 0
        and r["from_high"] > -15 and r["turnover"] > 3
    ]
    shortlist = qualified[:8]

    if not shortlist:
        print("\n  Nothing qualifies today. That is information, not a bug.")
    else:
        for n, r in enumerate(shortlist, 1):
            print(f"\n{n}. {r['ticker']}  —  {r['name']}")
            print(f"   {r['sector']} · {r['cap']} cap")
            print(f"   Score {r['total']}/80   (technical {r['tech']}/60, "
                  f"catalyst {r['cat']}/20)")
            print(f"   ₹{r['price']:,.1f}   RS {r['rs']:+.1f}%   "
                  f"{r['from_high']:+.1f}% from high   "
                  f"vol {r['vol_x']:.2f}x   ₹{r['turnover']:.0f}cr/day")

            if r["news"]:
                print(f"   News ({r['news_7d']} in 7 days):")
                for pub, head, src, t in r["news"][:4]:
                    label = f"[{t}] " if t else ""
                    text = head if len(head) <= 66 else head[:63] + "..."
                    print(f"     · {label}{text}")
                    print(f"       {pub}  {src}")
            elif has_news:
                print("   News: nothing in the last 7 days")

    # ---- things worth a second look ----
    print("\n" + "=" * 78)
    print("  WATCH — heavy news flow but not yet qualifying technically")
    print("=" * 78)
    watchers = [r for r in rows if r not in qualified and r["news_7d"] >= 4
                and r["cat"] >= 10][:5]
    if watchers:
        for r in watchers:
            reason = ("below trend" if not r["trend"]
                      else "lagging index" if (r["rs"] or 0) <= 0
                      else "far from high" if r["from_high"] <= -15
                      else "just outside top 8")
            print(f"  {r['ticker']:<14} catalyst {r['cat']}/20, "
                  f"{r['news_7d']} headlines — held back by: {reason}")
    else:
        print("  Nothing here today.")

    print("\n" + "-" * 78)
    print("  A brief is a reading list. Verify every catalyst against the")
    print("  exchange filing before you act on it.")
    print("-" * 78)

    conn.close()


if __name__ == "__main__":
    main()