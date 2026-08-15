@echo off
setlocal
cd /d "%~dp0"
call build_setup.bat
if errorlevel 1 exit /b 1

if not exist release_upload mkdir release_upload
powershell -NoProfile -Command "Compress-Archive -Path 'linkvideo_vpn_helper','scripts','server_example','.github','icon.ico','installer.iss','LinkVideo.Helper.spec','requirements.txt','run.bat','build_onedir.bat','build_setup.bat','BUILD_RELEASE_3.0.7.bat','prepare_release.bat','README_2.0.md','CHANGELOG_2.0.md','RELEASE_3.0.7_RU.txt','ROUTEROS_LV_SCRIPTS_1.0.1.txt','VPN_CLOUD_BACKUP_PLAN_RU.txt','.gitignore' -DestinationPath 'release_upload\LinkVideo.Helper_3.0.7_Project.zip' -Force"
if errorlevel 1 exit /b 1

echo.
echo Release files are in release_upload\
echo  - LinkVideo_VPN_Helper_Setup.exe
echo  - LinkVideo.Helper_3.0.7_Project.zip
echo  - version.json  ^(already includes SHA256 of this exact Setup^)
