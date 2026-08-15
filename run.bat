@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")
%PY% -c "import PySide6" >nul 2>&1
if errorlevel 1 (
  echo Installing dependencies...
  %PY% -m pip install -r requirements.txt
  if errorlevel 1 goto :fail
)
echo Starting LinkVideo.Helper 3.0.7 from source...
%PY% -m linkvideo_vpn_helper.app
if errorlevel 1 goto :fail
exit /b 0
:fail
echo.
echo LinkVideo.Helper stopped with an error.
pause
exit /b 1
