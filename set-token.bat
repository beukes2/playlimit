@echo off
REM PlayLimit - save the log-shipping token so logs upload from this machine.
setlocal
echo ============================================================
echo  PlayLimit - log shipping token setup
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0set-token.ps1"
echo.
pause
