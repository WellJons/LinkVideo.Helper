@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if exist .venv\Scripts\python.exe (set "PY=.venv\Scripts\python.exe") else (set "PY=python")
set "PYTHONPATH=%CD%;%PYTHONPATH%"

rem Keep every release entry point synchronized from APP_VERSION first.
%PY% scripts\sync_release_version.py
if errorlevel 1 exit /b 1
%PY% scripts\generate_version_info.py
if errorlevel 1 exit /b 1

rem A clean machine must be able to run the exact same preflight as CI.
%PY% -m pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1

rem One authoritative runner discovers and executes every core_tests*.py file.
rem This prevents future releases from silently missing a new regression test and
rem keeps the repository root on PYTHONPATH for every standalone test script.
%PY% scripts\release_preflight.py
if errorlevel 1 exit /b 1

%PY% -m PyInstaller --noconfirm --clean LinkVideo.Helper.spec
if errorlevel 1 exit /b 1

rem The 32-bit RADIUS sidecar is built separately and must remain outside
rem the 64-bit PyInstaller runtime so it can load an existing Ula OCI x86.
if defined ULA_BRIDGE_X86_PATH (
    if not exist "%ULA_BRIDGE_X86_PATH%" exit /b 1
    copy /Y "%ULA_BRIDGE_X86_PATH%" "dist\LinkVideo.Helper\LinkVideo.UlaBridge32.exe" >nul
    if errorlevel 1 exit /b 1
)

echo.
echo Build ready: dist\LinkVideo.Helper\LinkVideo.Helper.exe
