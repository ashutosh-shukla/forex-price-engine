@echo off
cd /d "%~dp0"
if exist venv\Scripts\python.exe (venv\Scripts\python engine_ctl.py check-db) else (python engine_ctl.py check-db)
pause
