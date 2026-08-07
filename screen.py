"""
STEP 3 — The screener
=====================
Reads market.db, computes the swing-trading measures, scores every stock,
and prints a ranked shortlist.

No downloading (except the Nifty benchmark on first run). It runs off the
database you already built, so it's fast.

Run with:   python screen.py
            python screen.py small        (only small caps)
            python screen.py mid large    (mid and large only)
"""

import sqlite3
import sys
from datetime import date, timedelta

import pandas as pd

DB = "market.db"
BENCHMARK = "^NSEI"


# ---------------------------------------------------------------------
# Make sure we have the index to compare against
# ---------------------------------------------------------------------

def ensure_benchmark(conn):
    n = conn.execute(
        "SELECT COUNT(*) FROM prices_daily WHERE ticker = ?", (BENCHMARK,)
    ).fetchone()[0]
    if n > 200:
        return True

    print("Fetching the Nifty benchmark (one time only) ...")
    try:
        import yfinance as yf
        df = yf.download(
            BENCHMARK,
            start=date.today() - timedelta(days=365 * 5),
            end=date.today() + timedelta(days=1),
            auto_adjust=True, progress=False, threads=False,
        )
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Close"])
        rec = [(BENCHMARK, ix.strftime("%Y-%m-%d"), float(r["Open"]), float(r["High"]),
                float(r["Low"]), float(r["Close"]),
                int(r["Volume"]) if not pd.isna(r["Volume"]) else 0)
               for ix, r in df.iterrows()]
        conn.executemany(
            "INSERT OR REPLACE INTO prices_daily VALUES (?,?,?,?,?,?,?)", rec)
        conn.commit()
        print(f"  stored {len(rec)} rows\n")
        return True
    except Exception as e:
        print(f"  couldn't fetch benchmark: {e}")
        print("  Relative strength will be skipped.\n")
        return False


# ---------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------

def analyse(df, bench):
    """df has date-indexed close/volume. Returns a dict of measures + score."""
    close = df["close"]
    volume = df["volume"]

    if len(close) < 220:
        return None

    last = float(close.iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1])

    high52 = float(close.tail(252).max())
    from_high = (last / high52 - 1) * 100

    vol20 = float(volume.rolling(20).mean().iloc[-1])
    vol_ratio = float(volume.iloc[-1]) / vol20 if vol20 > 0 else 0
    turnover_cr = (vol20 * last) / 1e7          # average daily value in ₹ crore

    # Relative strength over three months
    rs = None
    if bench is not None and len(close) > 63:
        stock_ret = (last / float(close.iloc[-63]) - 1) * 100
        common = bench.reindex(close.index).ffill()
        if len(common.dropna()) > 63:
            bench_ret = (float(common.iloc[-1]) / float(common.iloc[-63]) - 1) * 100
            rs = stock_ret - bench_ret

    # ---- score, out of 60 (the other 40 arrives with news + fundamentals) ----
    score = 0

    # Trend structure — 20 points
    if last > ma50:   score += 7
    if ma50 > ma200:  score += 8
    if last > ma20:   score += 5

    # Relative strength — 15 points
    if rs is not None:
        if rs > 20:   score += 15
        elif rs > 10: score += 11
        elif rs > 0:  score += 7

    # Proximity to 52-week high — 12 points
    if from_high > -3:    score += 12
    elif from_high > -8:  score += 9
    elif from_high > -15: score += 5

    # Volume confirmation — 8 points
    if vol_ratio > 2.0:   score += 8
    elif vol_ratio > 1.3: score += 5
    elif vol_ratio > 0.8: score += 2

    # Liquidity — 5 points (and a hard floor below)
    if turnover_cr > 50:  score += 5
    elif turnover_cr > 10: score += 3
    elif turnover_cr > 3:  score += 1

    return {
        "price": round(last, 1),
        "50dma": round((last / ma50 - 1) * 100, 1),
        "200dma": round((last / ma200 - 1) * 100, 1),
        "from_high": round(from_high, 1),
        "rs": round(rs, 1) if rs is not None else None,
        "vol_x": round(vol_ratio, 2),
        "turnover": round(turnover_cr, 1),
        "trend": last > ma50 > ma200,
        "score": score,
    }


# ---------------------------------------------------------------------

def main():
    wanted = [a.lower() for a in sys.argv[1:]] or ["large", "mid", "small", "micro"]

    conn = sqlite3.connect(DB)
    has_bench = ensure_benchmark(conn)

    bench = None
    if has_bench:
        b = pd.read_sql(
            "SELECT date, close FROM prices_daily WHERE ticker = ? ORDER BY date",
            conn, params=(BENCHMARK,), parse_dates=["date"], index_col="date")
        bench = b["close"]

    instruments = pd.read_sql(
        "SELECT ticker, name, sector, cap_bucket FROM instruments WHERE active = 1",
        conn)
    instruments = instruments[instruments["cap_bucket"].isin(wanted)]

    if instruments.empty:
        print(f"No instruments in buckets: {', '.join(wanted)}")
        return

    rows = []
    for _, inst in instruments.iterrows():
        df = pd.read_sql(
            "SELECT date, close, volume FROM prices_daily WHERE ticker = ? ORDER BY date",
            conn, params=(inst["ticker"],), parse_dates=["date"], index_col="date")
        if df.empty:
            continue
        m = analyse(df, bench)
        if m is None:
            continue
        rows.append({
            "Stock": inst["ticker"].replace(".NS", ""),
            "Cap": inst["cap_bucket"],
            "Score": m["score"],
            "Price": m["price"],
            "50DMA%": m["50dma"],
            "200DMA%": m["200dma"],
            "FromHigh%": m["from_high"],
            "RS%": m["rs"],
            "VolX": m["vol_x"],
            "Turn(cr)": m["turnover"],
            "Trend": "UP" if m["trend"] else "--",
            "Sector": inst["sector"],
        })

    if not rows:
        print("Nothing to screen. Is there enough price history in the database?")
        return

    table = pd.DataFrame(rows).sort_values("Score", ascending=False)

    as_of = conn.execute("SELECT MAX(date) FROM prices_daily").fetchone()[0]
    print("=" * 104)
    print(f"  SWING SCREEN — as of {as_of}   |   buckets: {', '.join(wanted)}"
          f"   |   {len(table)} stocks scored")
    print("=" * 104)
    print(table.drop(columns=["Sector"]).to_string(index=False))
    print("=" * 104)

    # ---- The shortlist: the filters that actually matter ----
    short = table[
        (table["Trend"] == "UP")
        & (table["RS%"] > 0)
        & (table["FromHigh%"] > -15)
        & (table["Turn(cr)"] > 3)
    ].head(10)

    print("\nSHORTLIST — uptrend, beating Nifty, near highs, liquid enough")
    print("-" * 104)
    if short.empty:
        print("  Nothing qualifies today.")
        print("  That is a real signal about the market, not a bug.")
    else:
        for _, r in short.iterrows():
            print(f"  {r['Score']:>3}  {r['Stock']:<14} {r['Cap']:<6} "
                  f"₹{r['Price']:>9,.1f}  RS {r['RS%']:>+6.1f}%  "
                  f"{r['FromHigh%']:>+6.1f}% from high   {r['Sector']}")
    print("-" * 104)
    print("\nScore is out of 60. Catalyst and fundamental factors come in later steps.")
    print("A shortlist is a place to start reading, never a reason to buy.")

    conn.close()


if __name__ == "__main__":
    main()