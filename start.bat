@echo off
cd /d "%~dp0"
if exist venv\Scripts\python.exe (venv\Scripts\python engine_ctl.py start) else (python engine_ctl.py start)
if "%1"=="" pause
