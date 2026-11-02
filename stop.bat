@echo off
cd /d "%~dp0"
if exist venv\Scripts\python.exe (venv\Scripts\python engine_ctl.py stop) else (python engine_ctl.py stop)
if "%1"=="" pause
