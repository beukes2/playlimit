@echo off
REM PlayLimit diagnostics - double-click this. Read-only, changes nothing.
setlocal
echo ============================================================
echo  PlayLimit DIAGNOSTICS
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0diag.ps1"
echo.
echo ============================================================
echo  Done. Please copy ALL the text above and send it back.
echo ============================================================
pause
