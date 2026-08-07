from datetime import date, timedelta
import pandas as pd
import yfinance as yf

STOCKS = ["RELIANCE.NS", "TCS.NS", "DIXON.NS", "ASIANPAINT.NS"]
INDEX = "^NSEI"

end = date.today()
start = end - timedelta(days=400)

print("Downloading...")
raw = yf.download(STOCKS + [INDEX], start=start, end=end,
                  auto_adjust=True, progress=False, group_by="ticker")

idx = raw[INDEX]["Close"].dropna()
rows = {}

for t in STOCKS:
    c = raw[t]["Close"].dropna()
    v = raw[t]["Volume"].dropna()
    if len(c) < 200:
        continue
    last = float(c.iloc[-1])
    ma50 = float(c.rolling(50).mean().iloc[-1])
    ma200 = float(c.rolling(200).mean().iloc[-1])
    stock_ret = (last / float(c.iloc[-63]) - 1) * 100
    index_ret = (float(idx.iloc[-1]) / float(idx.iloc[-63]) - 1) * 100
    rows[t.replace(".NS", "")] = {
        "Price": round(last, 2),
        "vs 50DMA %": round((last / ma50 - 1) * 100, 1),
        "vs 200DMA %": round((last / ma200 - 1) * 100, 1),
        "From 52w high %": round((last / float(c.tail(252).max()) - 1) * 100, 1),
        "RS vs Nifty %": round(stock_ret - index_ret, 1),
        "Vol vs 20d": round(float(v.iloc[-1]) / float(v.rolling(20).mean().iloc[-1]), 2),
        "Trend": "UP" if last > ma50 > ma200 else "--",
    }

print()
print(pd.DataFrame(rows).T.to_string())
print()
print("Leaders (uptrend + beating Nifty):")
for k, r in rows.items():
    if r["Trend"] == "UP" and r["RS vs Nifty %"] > 0:
        print("  -", k)