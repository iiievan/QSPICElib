@echo off
call "%~dp0run_all.cmd" --prepare %*
exit /b %errorlevel%
