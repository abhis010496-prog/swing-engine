import sqlite3, sys, time
from datetime import date, datetime, timedelta
import pandas as pd, yfinance as yf

DB = "market.db"
YEARS = 5

U = [
 ("RELIANCE.NS","Reliance Industries","Energy","large"),
 ("TCS.NS","Tata Consultancy","IT Services","large"),
 ("HDFCBANK.NS","HDFC Bank","Banking","large"),
 ("ICICIBANK.NS","ICICI Bank","Banking","large"),
 ("INFY.NS","Infosys","IT Services","large"),
 ("BHARTIARTL.NS","Bharti Airtel","Telecom","large"),
 ("ITC.NS","ITC","FMCG","large"),
 ("SBIN.NS","State Bank of India","Banking","large"),
 ("LT.NS","Larsen & Toubro","Capital Goods","large"),
 ("ASIANPAINT.NS","Asian Paints","Paints","large"),
 ("MARUTI.NS","Maruti Suzuki","Automobiles","large"),
 ("TITAN.NS","Titan Company","Cons Durables","large"),
 ("SUNPHARMA.NS","Sun Pharma","Pharma","large"),
 ("TMPV.NS","Tata Motors Passenger Vehicles","Automobiles","large"),
 ("TMLCV.NS","Tata Motors Commercial Vehicles","Automobiles","large"),
 ("NTPC.NS","NTPC","Power","large"),
 ("ULTRACEMCO.NS","UltraTech Cement","Cement","large"),
 ("TATASTEEL.NS","Tata Steel","Metals","large"),
 ("AXISBANK.NS","Axis Bank","Banking","large"),
 ("BAJFINANCE.NS","Bajaj Finance","NBFC","large"),
 ("HINDUNILVR.NS","Hindustan Unilever","FMCG","large"),
 ("DIXON.NS","Dixon Technologies","Electronics","mid"),
 ("PERSISTENT.NS","Persistent Systems","IT Services","mid"),
 ("POLYCAB.NS","Polycab India","Electricals","mid"),
 ("CUMMINSIND.NS","Cummins India","Capital Goods","mid"),
 ("ASTRAL.NS","Astral","Building Prod","mid"),
 ("CDSL.NS","Central Depository","Capital Mkts","mid"),
 ("PIIND.NS","PI Industries","Agrochemicals","mid"),
 ("BHEL.NS","BHEL","Capital Goods","mid"),
 ("SUZLON.NS","Suzlon Energy","Renewables","mid"),
 ("IRCTC.NS","IRCTC","Travel","mid"),
 ("VOLTAS.NS","Voltas","Cons Durables","mid"),
 ("COFORGE.NS","Coforge","IT Services","mid"),
 ("SHAKTIPUMP.NS","Shakti Pumps","Ind Pumps","small"),
 ("KIRLOSBROS.NS","Kirloskar Brothers","Pumps","small"),
 ("RATEGAIN.NS","RateGain Travel","Software","small"),
 ("GRANULES.NS","Granules India","Pharma","small"),
 ("CAPLIPOINT.NS","Caplin Point","Pharma","small"),
 ("GARFIBRES.NS","Garware Tech Fibres","Textiles","small"),
 ("TIINDIA.NS","Tube Investments","Auto Comp","small"),
 ("ELECON.NS","Elecon Engineering","Capital Goods","small"),
 ("RALLIS.NS","Rallis India","Agrochemicals","small"),
 ("HGINFRA.NS","HG Infra Engineering","Construction","small"),
 ("JBCHEPHARM.NS","JB Chemicals","Pharma","small"),
 ("TECHNOE.NS","Techno Electric","Power Infra","small"),
]

conn = sqlite3.connect(DB)
conn.executescript("""
CREATE TABLE IF NOT EXISTS instruments(
  ticker TEXT PRIMARY KEY, name TEXT, sector TEXT,
  cap_bucket TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS prices_daily(
  ticker TEXT, date TEXT, open REAL, high REAL, low REAL,
  close REAL, volume INTEGER, PRIMARY KEY(ticker,date));
CREATE INDEX IF NOT EXISTS ix_d ON prices_daily(date);
""")
conn.executemany(
  "INSERT OR REPLACE INTO instruments(ticker,name,sector,cap_bucket) VALUES(?,?,?,?)", U)
conn.commit()
print(f"Loaded {len(U)} instruments.\n")

tickers = [r[0] for r in conn.execute(
    "SELECT ticker FROM instruments WHERE active=1 ORDER BY ticker")]
today = date.today()
ok = bad = rows = 0
failed = []

for i in range(0, len(tickers), 10):
    batch = tickers[i:i+10]
    stored = [conn.execute("SELECT MAX(date) FROM prices_daily WHERE ticker=?",
                           (t,)).fetchone()[0] for t in batch]
    if all(stored):
        start = datetime.strptime(min(stored), "%Y-%m-%d").date() - timedelta(days=5)
    else:
        start = today - timedelta(days=365*YEARS)

    print(f"  {i//10+1}/{(len(tickers)+9)//10}: " +
          ", ".join(t.replace('.NS','') for t in batch))
    try:
        raw = yf.download(batch, start=start, end=today+timedelta(days=1),
                          auto_adjust=True, progress=False,
                          group_by="ticker", threads=False)
    except Exception as e:
        print("    batch failed:", e); failed += batch; bad += len(batch); continue

    for t in batch:
        try:
            df = raw[t].dropna(subset=["Close"]) if isinstance(
                raw.columns, pd.MultiIndex) else raw.dropna(subset=["Close"])
            rec = [(t, ix.strftime("%Y-%m-%d"), float(r["Open"]), float(r["High"]),
                    float(r["Low"]), float(r["Close"]),
                    int(r["Volume"]) if not pd.isna(r["Volume"]) else 0)
                   for ix, r in df.iterrows()]
            if not rec:
                failed.append(t); bad += 1; continue
            conn.executemany("INSERT OR REPLACE INTO prices_daily VALUES(?,?,?,?,?,?,?)", rec)
            conn.commit(); rows += len(rec); ok += 1
        except Exception:
            failed.append(t); bad += 1
    time.sleep(1)

tot = conn.execute("SELECT COUNT(*) FROM prices_daily").fetchone()[0]
lo, hi = conn.execute("SELECT MIN(date),MAX(date) FROM prices_daily").fetchone()
print("\n" + "="*46)
print(f"  Stocks OK        : {ok}")
print(f"  Stocks failed    : {bad}")
print(f"  Rows written     : {rows:,}")
print(f"  Total in database: {tot:,}")
print(f"  Date range       : {lo} to {hi}")
if failed:
    print("  Failed: " + ", ".join(f.replace('.NS','') for f in failed))
print("="*46)
conn.close()