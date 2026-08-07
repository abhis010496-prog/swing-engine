@echo off
REM ============================================================
REM  Daily update — run by Windows Task Scheduler at 4:30pm
REM  Updates prices first, then news, and logs what happened.
REM ============================================================

cd /d C:\Users\abhis\swing-engine

echo. >> daily_log.txt
echo ============================================ >> daily_log.txt
echo RUN STARTED: %date% %time% >> daily_log.txt
echo ============================================ >> daily_log.txt

echo Updating prices...
python build_db.py >> daily_log.txt 2>&1

echo Updating news...
python ingest_news.py >> daily_log.txt 2>&1

echo RUN FINISHED: %date% %time% >> daily_log.txt

echo.
echo Done. Open daily_log.txt to see what happened.