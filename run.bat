@echo off
REM Run in this window with live console output (Ctrl+C to stop)
cd /d "%~dp0"
if exist venv\Scripts\python.exe (venv\Scripts\python engine_ctl.py run) else (python engine_ctl.py run)
pause
