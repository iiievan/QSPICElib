@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "PYTHONPATH="
if exist ".venv\Scripts\python.exe" goto install
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 goto missing
py -3 -m venv ".venv"
if errorlevel 1 goto failed
goto install
:use_python
where python >nul 2>nul
if errorlevel 1 goto missing
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 goto missing
python -m venv ".venv"
if errorlevel 1 goto failed
:install
if not exist "proxy_settings.cmd" copy /y "proxy_settings.example.cmd" "proxy_settings.cmd" >nul
set "SUITE_PIP_PROXY="
if exist "proxy_settings.cmd" call "proxy_settings.cmd"
if defined SUITE_PIP_PROXY set "PIP_PROXY=%SUITE_PIP_PROXY%"
set "PIP_USER=false"
set "PIP_REQUIRE_VIRTUALENV=true"
".venv\Scripts\python.exe" -m pip install --require-virtualenv -r "requirements.txt"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -c "import sys,matplotlib; print('Python:',sys.executable); print('matplotlib:',matplotlib.__version__); sys.exit(0 if sys.prefix != sys.base_prefix else 1)"
if errorlevel 1 goto failed
echo.
echo Setup complete. Packages are installed in .venv.
echo Run run_all.cmd to simulate the six QSCH benches.
set "SUITE_EXIT=0"
goto finish
:missing
echo Python 3.10 or newer was not found. Install Python, then retry.
set "SUITE_EXIT=2"
goto finish
:failed
echo Setup failed. For a proxy, edit proxy_settings.cmd, then retry setup.cmd.
set "SUITE_EXIT=2"
:finish
pause
exit /b %SUITE_EXIT%
