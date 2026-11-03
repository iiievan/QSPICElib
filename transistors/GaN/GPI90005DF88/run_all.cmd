@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "PYTHONPATH="
if not exist ".venv\Scripts\python.exe" goto missing
".venv\Scripts\python.exe" run_suite.py --open-report %*
set "SUITE_EXIT=%ERRORLEVEL%"
echo.
echo Suite exit code: %SUITE_EXIT%
goto finish
:missing
echo Project .venv was not found. Run setup.cmd first.
set "SUITE_EXIT=2"
:finish
pause
exit /b %SUITE_EXIT%
