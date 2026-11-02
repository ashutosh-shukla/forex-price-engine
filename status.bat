@echo off
cd /d "%~dp0"
if exist venv\Scripts\python.exe (venv\Scripts\python engine_ctl.py status) else (python engine_ctl.py status)
if "%1"=="" pause
