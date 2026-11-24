@echo off
REM One-time PlayLimit Server setup. Double-click and accept UAC.
setlocal
echo Installing PlayLimit Server (firewall + startup shortcut)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-NoExit','-File','%~dp0server_install.ps1')"
echo.
echo A new Administrator window is running the setup.
pause
