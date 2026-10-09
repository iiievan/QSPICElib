@echo off
rem Optional first argument: saved run folder; default is latest.
call "%~dp0run_all.cmd" --rebuild-report %*
exit /b %ERRORLEVEL%
