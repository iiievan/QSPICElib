@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call setup.cmd
if not exist ".venv\Scripts\python.exe" exit /b 2
if exist "proxy_settings.cmd" call "proxy_settings.cmd"
".venv\Scripts\python.exe" -c "import scipy" >nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements_calibration.txt
  if errorlevel 1 exit /b 2
)
".venv\Scripts\python.exe" calibrate_refine.py %*
exit /b %errorlevel%
