@echo off
chcp 65001 >nul
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0cleanup.ps1" %*
set "FINKA_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %FINKA_EXIT%
