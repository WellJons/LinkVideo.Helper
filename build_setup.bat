@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo   LinkVideo.Helper FULL RELEASE BUILD
echo ============================================================
echo.

if exist .venv\Scripts\python.exe (set "PY=.venv\Scripts\python.exe") else (set "PY=python")

for /f "usebackq delims=" %%V in (`%PY% -c "from linkvideo_vpn_helper.version import APP_VERSION; print(APP_VERSION)"`) do set "APPVER=%%V"
if not defined APPVER (
  echo ERROR: Cannot read APP_VERSION.
  exit /b 1
)
echo Version: %APPVER%

rem 1. Hard clean - prevents accidental reuse of an old EXE or old installer.
echo [1/7] Cleaning old build artifacts...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist installer_output rmdir /s /q installer_output
if exist build_version_info.txt del /q build_version_info.txt
if not exist release_upload mkdir release_upload
if exist release_upload\LinkVideo_VPN_Helper_Setup.exe del /q release_upload\LinkVideo_VPN_Helper_Setup.exe

rem 2. Sync versions + tests + fresh PyInstaller build.
echo [2/7] Building application from scratch...
call build_onedir.bat
if errorlevel 1 exit /b 1

rem 3. Verify the actual application EXE version metadata.
echo [3/7] Verifying application EXE version...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$raw=[string](Get-Item '.\dist\LinkVideo.Helper\LinkVideo.Helper.exe').VersionInfo.ProductVersion; $m=[regex]::Match($raw,'\d+(?:\.\d+){0,3}'); $v=$m.Value; Write-Host ('App ProductVersion: ' + $v); $p='^'+[regex]::Escape('%APPVER%')+'(?:\.0+)?$'; if((-not $m.Success) -or ($v -notmatch $p)){Write-Host ('Expected: %APPVER%; raw: ['+$raw+']; parsed: ['+$v+']'); exit 9}"
if errorlevel 1 (
  echo ERROR: Application EXE version does not match %APPVER%.
  exit /b 1
)

rem 4. Find Inno Setup and compile a completely new full installer.
echo [4/7] Building full Inno Setup installer...
set "ISCC="
where ISCC.exe >nul 2>nul
if not errorlevel 1 set "ISCC=ISCC.exe"
if not defined ISCC if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
if not defined ISCC (
  echo ERROR: Inno Setup 6 not found.
  echo Install Inno Setup 6 and run this BAT again.
  exit /b 1
)
"%ISCC%" installer.iss
if errorlevel 1 exit /b 1

set "SETUP=installer_output\LinkVideo_VPN_Helper_Setup.exe"
if not exist "%SETUP%" (
  echo ERROR: Setup file was not created.
  exit /b 1
)

rem 5. Verify the installer itself really says the same version.
echo [5/7] Verifying installer version and SHA256...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$f=Get-Item '.\%SETUP%'; $raw=[string]$f.VersionInfo.ProductVersion; $m=[regex]::Match($raw,'\d+(?:\.\d+){0,3}'); $v=$m.Value; Write-Host ('Setup ProductVersion: ' + $v); Write-Host ('Setup size: ' + [math]::Round($f.Length/1MB,2) + ' MB'); $p='^'+[regex]::Escape('%APPVER%')+'(?:\.0+)?$'; if((-not $m.Success) -or ($v -notmatch $p)){Write-Host ('Expected: %APPVER%; raw: ['+$raw+']; parsed: ['+$v+']'); exit 10}; Get-FileHash $f.FullName -Algorithm SHA256 | Format-List"
if errorlevel 1 (
  echo ERROR: Installer version does not match %APPVER%.
  exit /b 1
)

rem 6. Copy exactly the verified installer to the upload folder.
echo [6/7] Preparing Google Drive upload file...
copy /y "%SETUP%" "release_upload\LinkVideo_VPN_Helper_Setup.exe" >nul
if errorlevel 1 exit /b 1

rem 7. Generate version.json from the exact verified installer that will be uploaded.
echo [7/7] Generating version.json with installer SHA256...
%PY% scripts\make_release_manifest.py
if errorlevel 1 (
  echo ERROR: Could not generate release manifest.
  exit /b 1
)

echo.
echo ============================================================
echo READY: release_upload\LinkVideo_VPN_Helper_Setup.exe
echo READY: release_upload\version.json
echo VERSION: %APPVER%
echo ============================================================
echo.
echo 1. Upload Setup as a NEW VERSION of the existing Google Drive setup file.
echo 2. Only after Setup upload is complete, upload release_upload\version.json

echo Google Drive IDs/URLs remain unchanged.
