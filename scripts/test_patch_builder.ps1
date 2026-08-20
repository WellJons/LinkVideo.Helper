$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$temp = Join-Path $env:RUNNER_TEMP 'lvh-patch-builder-test'
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $temp
New-Item -ItemType Directory -Force -Path (Join-Path $temp 'new\_internal') | Out-Null
Set-Content -Encoding ascii -NoNewline -Path (Join-Path $temp 'new\LinkVideo.Helper.exe') -Value 'new-app'
Set-Content -Encoding ascii -NoNewline -Path (Join-Path $temp 'new\_internal\same.dll') -Value 'same'
Set-Content -Encoding ascii -NoNewline -Path (Join-Path $temp 'new\_internal\new.dll') -Value 'new'
$old = Join-Path $temp 'old.zip'
python -c "import zipfile; z=zipfile.ZipFile(r'$old','w',zipfile.ZIP_DEFLATED); z.writestr('LinkVideo.Helper.exe',b'old-app'); z.writestr('_internal/same.dll',b'same'); z.writestr('_internal/delete.dll',b'delete'); z.close()"

& (Join-Path $root 'scripts\build_patch.ps1') -FromZip $old -FromVersion '3.0.8' -ToVersion '3.0.9' -ToDir (Join-Path $temp 'new')
$patch = Join-Path $root 'patch_output\LinkVideo.Helper_Patch_3.0.8_to_3.0.9.exe'
if (-not (Test-Path $patch)) { throw 'Synthetic patch EXE was not built' }
Write-Host "PATCH BUILDER COMPILE TEST OK: $((Get-Item $patch).Length) bytes"
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $root 'patch_output')
