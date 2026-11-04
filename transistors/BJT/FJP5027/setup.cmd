@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (set "PY=python") else (set "PY=py -3")
if exist proxy_settings.cmd call proxy_settings.cmd
%PY% -m venv .venv
if errorlevel 1 exit /b 2
".venv\Scripts\python.exe" -m pip install -r requirements.txt
exit /b %errorlevel%
