@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" collect_diagnostics.py %*
exit /b %errorlevel%
