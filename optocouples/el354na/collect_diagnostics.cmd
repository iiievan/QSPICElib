@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "PYTHONPATH="
if exist ".venv\Scripts\python.exe" goto venv
where py >nul 2>nul
if errorlevel 1 goto python
py -3 collect_diagnostics.py %*
goto done
:venv
".venv\Scripts\python.exe" collect_diagnostics.py %*
goto done
:python
python collect_diagnostics.py %*
:done
set "SUITE_EXIT=%ERRORLEVEL%"
pause
exit /b %SUITE_EXIT%
