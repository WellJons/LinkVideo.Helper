@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title LinkVideo.Helper 3.0.5 Build

echo ============================================================
echo   LinkVideo.Helper 3.0.5 - FULL RELEASE BUILD
echo ============================================================
echo.

echo [CHECK] Project files...
if not exist "build_setup.bat" goto :bad_project
if not exist "linkvideo_vpn_helper\version.py" goto :bad_project
if not exist "LinkVideo.Helper.spec" goto :bad_project

echo [CHECK] Python...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" --version
) else (
    python --version
)
if errorlevel 1 goto :no_python

echo.
echo Starting build...
echo.
call build_setup.bat
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto :build_failed

echo.
echo ============================================================
echo BUILD 3.0.5 COMPLETE
echo READY: release_upload\LinkVideo_VPN_Helper_Setup.exe
echo READY: release_upload\version.json
echo ============================================================
echo.
pause
exit /b 0

:bad_project
echo.
echo ERROR: Project files were not found.
echo Extract the whole ZIP and run this BAT from the project root.
echo.
pause
exit /b 2

:no_python
echo.
echo ERROR: Python was not found.
echo.
pause
exit /b 3

:build_failed
echo.
echo ============================================================
echo BUILD FAILED. Error code: %RC%
echo Nothing should be uploaded to Google Drive.
echo ============================================================
echo.
pause
exit /b %RC%
