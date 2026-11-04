@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.cmd first.
  exit /b 2
)
".venv\Scripts\python.exe" run_extra.py %*
exit /b %errorlevel%
