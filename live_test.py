"""
Which live-price method actually works on your machine?
Run:  python live_test.py
"""

import time

import pandas as pd
import yfinance as yf

TEST = ["RELIANCE.NS", "TCS.NS", "DIXON.NS", "SHAKTIPUMP.NS", "CDSL.NS"]

print("yfinance", getattr(yf, "__version__", "unknown"))
print("=" * 58)

# ---- method 1: batch download, 5-minute bars ----
print("\n1. Batch download (period=2d, interval=5m)")
t0 = time.time()
try:
    d = yf.download(TEST, period="2d", interval="5m", progress=False,
                    threads=False, auto_adjust=False)
    print(f"   returned {len(d)} rows in {time.time()-t0:.1f}s")
    print(f"   MultiIndex columns: {isinstance(d.columns, pd.MultiIndex)}")
    if isinstance(d.columns, pd.MultiIndex):
        print(f"   level 0 sample: {list(d.columns.get_level_values(0))[:4]}")
        print(f"   level 1 sample: {list(d.columns.get_level_values(1))[:4]}")
    got = 0
    for t in TEST:
        try:
            c = (d["Close"][t] if isinstance(d.columns, pd.MultiIndex)
                 else d["Close"]).dropna()
            if len(c):
                got += 1
                print(f"     {t:<16} {float(c.iloc[-1]):>10.2f}  "
                      f"at {c.index[-1]}")
        except Exception as e:
            print(f"     {t:<16} FAILED {type(e).__name__}")
    print(f"   -> {got}/{len(TEST)} usable")
except Exception as e:
    print(f"   FAILED: {type(e).__name__}: {e}")

# ---- method 2: fast_info.last_price, one at a time ----
print("\n2. fast_info.last_price (one call per stock)")
t0 = time.time()
got = 0
for t in TEST:
    try:
        fi = yf.Ticker(t).fast_info
        p = fi.last_price
        pc = None
        try:
            pc = fi.previous_close
        except Exception:
            pass
        if p:
            got += 1
            print(f"     {t:<16} {float(p):>10.2f}   prev close "
                  f"{float(pc) if pc else float('nan'):.2f}")
    except Exception as e:
        print(f"     {t:<16} FAILED {type(e).__name__}")
print(f"   -> {got}/{len(TEST)} usable in {time.time()-t0:.1f}s")

# ---- method 3: batch download, 1-day bars (today's partial candle) ----
print("\n3. Batch download (period=1d, interval=1d)")
t0 = time.time()
try:
    d = yf.download(TEST, period="1d", interval="1d", progress=False,
                    threads=False, auto_adjust=False)
    got = 0
    for t in TEST:
        try:
            c = (d["Close"][t] if isinstance(d.columns, pd.MultiIndex)
                 else d["Close"]).dropna()
            if len(c):
                got += 1
                print(f"     {t:<16} {float(c.iloc[-1]):>10.2f}")
        except Exception:
            pass
    print(f"   -> {got}/{len(TEST)} usable in {time.time()-t0:.1f}s")
except Exception as e:
    print(f"   FAILED: {type(e).__name__}: {e}")

# ---- how slow is fast_info at scale? ----
print("\n4. Timing fast_info for 20 stocks (to judge scale)")
import sqlite3
try:
    conn = sqlite3.connect("market.db")
    many = [r[0] for r in conn.execute(
        "SELECT ticker FROM scores ORDER BY score DESC LIMIT 20")]
    conn.close()
    t0 = time.time()
    ok = 0
    for t in many:
        try:
            if yf.Ticker(t).fast_info.last_price:
                ok += 1
        except Exception:
            pass
    el = time.time() - t0
    print(f"   {ok}/20 in {el:.1f}s  ->  about {el/20*60:.0f}s for 60 stocks")
except Exception as e:
    print(f"   skipped: {e}")

print("\n" + "=" * 58)
print("Send me all of the above.")