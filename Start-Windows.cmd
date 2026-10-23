@echo off
chcp 65001 >nul
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" %*
set "FINKA_EXIT=%ERRORLEVEL%"
echo.
echo Finished. Press any key to close this window.
pause >nul
exit /b %FINKA_EXIT%
