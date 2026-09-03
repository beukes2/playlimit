@echo off
:: Simple batch wrapper for install.ps1 - double-click this as Admin
echo AlbionLimiter - Installing...
powershell -ExecutionPolicy Bypass -File "%~dp0install.ps1"
pause
