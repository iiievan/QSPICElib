@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.cmd first.
  exit /b 2
)
".venv\Scripts\python.exe" run_suite.py %*
set "RESULT=%errorlevel%"
echo Exit 0: criteria met or prepare-only. Exit 1: inspect report. Exit 2: startup error.
exit /b %RESULT%
