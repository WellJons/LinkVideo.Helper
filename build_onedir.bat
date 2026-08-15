@echo off
setlocal
cd /d "%~dp0"

if exist .venv\Scripts\python.exe (set "PY=.venv\Scripts\python.exe") else (set "PY=python")

%PY% scripts\sync_release_version.py
if errorlevel 1 exit /b 1
%PY% scripts\generate_version_info.py
if errorlevel 1 exit /b 1
%PY% scripts\self_check.py
if errorlevel 1 exit /b 1
%PY% scripts\core_tests.py
if errorlevel 1 exit /b 1
%PY% scripts\core_tests_2_1.py
if errorlevel 1 exit /b 1
%PY% scripts\core_tests_2_1_1.py
if errorlevel 1 exit /b 1
%PY% scripts\core_tests_2_1_2.py
if errorlevel 1 exit /b 1
%PY% scripts\core_tests_2_2_0.py
if errorlevel 1 exit /b 1
%PY% scripts\core_tests_2_2_1.py
if errorlevel 1 exit /b 1
%PY% scripts\core_tests_2_2_2.py
if errorlevel 1 exit /b 1
%PY% scripts\core_tests_3_0.py
if errorlevel 1 exit /b 1
%PY% scripts\core_tests_3_0_1.py
if errorlevel 1 exit /b 1
%PY% scripts\core_tests_3_0_2.py
if errorlevel 1 exit /b 1
%PY% scripts\core_tests_3_0_3.py
if errorlevel 1 exit /b 1
%PY% scripts\core_tests_3_0_4.py
if errorlevel 1 exit /b 1
%PY% scripts\core_tests_3_0_5.py
if errorlevel 1 exit /b 1
%PY% scripts\core_tests_3_0_6.py
if errorlevel 1 exit /b 1
%PY% scripts\core_tests_3_0_7.py
if errorlevel 1 exit /b 1
%PY% scripts\core_tests_3_0_8.py
if errorlevel 1 exit /b 1
pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1
%PY% -m PyInstaller --noconfirm --clean LinkVideo.Helper.spec
if errorlevel 1 exit /b 1

echo.
echo Build ready: dist\LinkVideo.Helper\LinkVideo.Helper.exe
