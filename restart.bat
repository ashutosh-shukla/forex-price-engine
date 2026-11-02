@echo off
cd /d "%~dp0"
if exist venv\Scripts\python.exe (venv\Scripts\python engine_ctl.py restart) else (python engine_ctl.py restart)
if "%1"=="" pause
