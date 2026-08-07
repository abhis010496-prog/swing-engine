@echo off
REM ============================================================
REM  Daily update
REM
REM  1. Prices  -> Yahoo Finance, previous day's closing candle
REM  2. Scores  -> recomputed from your own database
REM  3. News    -> Google News RSS, top 150 scoring stocks only
REM  4. Scores  -> again, so catalysts count toward the score
REM  5. Monthly -> refresh the monthly shortlist
REM
REM  Takes about 8-12 minutes. Safe to close and rerun.
REM ============================================================

cd /d C:\Users\abhis\swing-engine

echo. >> daily_log.txt
echo ============================================ >> daily_log.txt
echo RUN STARTED: %date% %time% >> daily_log.txt

echo [1/7] Updating prices for all listed companies...
python build_universe.py >> daily_log.txt 2>&1

echo [2/7] Topping up fundamentals for anything new...
python fetch_fundamentals.py --top 300 >> daily_log.txt 2>&1

echo [3/7] Scoring...
python rank.py >> daily_log.txt 2>&1

echo [4/7] Fetching news for the top-scoring stocks...
python ingest_news.py --top 150 >> daily_log.txt 2>&1

echo [5/7] Rescoring with catalysts included...
python rank.py >> daily_log.txt 2>&1

echo [6/7] Updating the monthly shortlist...
python monthly.py >> daily_log.txt 2>&1

echo [7/7] Measuring how past picks performed...
python tracker.py >> daily_log.txt 2>&1

echo RUN FINISHED: %date% %time% >> daily_log.txt

echo.
echo Done. Open daily_log.txt if anything looks wrong.
echo Now run:  python -m streamlit run dashboard.py