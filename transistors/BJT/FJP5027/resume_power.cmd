@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call setup.cmd
if not exist ".venv\Scripts\python.exe" exit /b 2
if exist "proxy_settings.cmd" call "proxy_settings.cmd"
".venv\Scripts\python.exe" resume_power.py %*
exit /b %errorlevel%
