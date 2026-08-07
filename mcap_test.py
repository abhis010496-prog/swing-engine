"""
Diagnostic — which way of reading market cap works on your yfinance version?
Run:  python mcap_test.py
"""

import yfinance as yf

print("yfinance version:", getattr(yf, "__version__", "unknown"))
print()

TICKER = "RELIANCE.NS"
t = yf.Ticker(TICKER)

print(f"Testing {TICKER}\n" + "-" * 50)

# --- 1. fast_info as an object attribute
try:
    fi = t.fast_info
    print("fast_info type:", type(fi).__name__)
    try:
        print("  fast_info.market_cap      =", fi.market_cap)
    except Exception as e:
        print("  fast_info.market_cap      ->", type(e).__name__, e)
    for key in ["market_cap", "marketCap"]:
        try:
            print(f"  fast_info['{key}']".ljust(28), "=", fi[key])
        except Exception as e:
            print(f"  fast_info['{key}']".ljust(28), "->", type(e).__name__)
    try:
        print("  available keys:", list(fi.keys())[:20])
    except Exception as e:
        print("  keys() ->", type(e).__name__)
    for key in ["shares", "last_price", "lastPrice"]:
        try:
            print(f"  fast_info.{key}".ljust(28), "=", getattr(fi, key))
        except Exception:
            pass
except Exception as e:
    print("fast_info failed entirely:", type(e).__name__, e)

print()

# --- 2. get_info / info
try:
    info = t.info
    print("info dict keys containing 'cap':",
          [k for k in info if "cap" in k.lower()][:10])
    print("  info['marketCap'] =", info.get("marketCap"))
    print("  info['sharesOutstanding'] =", info.get("sharesOutstanding"))
    print("  info['sector'] =", info.get("sector"))
except Exception as e:
    print("info failed:", type(e).__name__, e)

print()

# --- 3. shares outstanding endpoint
try:
    so = t.get_shares_full(start="2025-01-01")
    print("get_shares_full last value:", None if so is None or so.empty else so.iloc[-1])
except Exception as e:
    print("get_shares_full failed:", type(e).__name__)

print("\n" + "-" * 50)
print("Send me everything above.")