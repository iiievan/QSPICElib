@echo off
rem Optional first argument: saved results folder; otherwise the latest run.
call "%~dp0run_all.cmd" --reprocess %*
exit /b %ERRORLEVEL%
