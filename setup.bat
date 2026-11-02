@echo off
REM One-time setup: virtual environment + packages + .env with generated secrets
cd /d "%~dp0"
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv || goto :fail
)
echo Installing requirements...
venv\Scripts\python -m pip install --upgrade pip >nul
venv\Scripts\python -m pip install -r requirements.txt || goto :fail
venv\Scripts\python engine_ctl.py init
echo.
echo Setup done. Next: copy your wallet files into the wallet folder, fill the ORACLE_ADB_* and OANDA_* values in .env,
echo then run:  check_db.bat   then   initdb.bat   then   start.bat
pause
exit /b 0
:fail
echo.
echo Setup failed. Is Python 3.10 or newer installed and on PATH?  (run: python --version)
pause
exit /b 1
