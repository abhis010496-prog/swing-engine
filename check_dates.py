import sqlite3
from datetime import datetime
conn = sqlite3.connect("market.db")
lo, hi, n = conn.execute(
    "SELECT MIN(date), MAX(date), COUNT(*) FROM prices_daily").fetchone()
print(f"Database holds {n:,} rows, from {lo} to {hi}\n")
print("Newest date per stock (oldest first):")
rows = conn.execute(
    "SELECT ticker, MAX(date) d, COUNT(*) FROM prices_daily "
    "GROUP BY ticker ORDER BY d ASC").fetchall()
today = datetime.now().date()
for t, d, c in rows:
    age = (today - datetime.strptime(d, "%Y-%m-%d").date()).days
    flag = "  <-- STALE" if age > 5 else ""
    print(f"  {t.replace('.NS',''):<16} {d}  ({age:>3} days old, {c:,} rows){flag}")