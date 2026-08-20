param(
    [Parameter(Mandatory=$true)][string]$FromZip,
    [Parameter(Mandatory=$true)][string]$FromVersion,
    [Parameter(Mandatory=$true)][string]$ToVersion,
    [string]$ToDir = 'dist\LinkVideo.Helper'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$fromZipPath = (Resolve-Path $FromZip).Path
$toDirPath = (Resolve-Path $ToDir).Path
$bundleDir = Join-Path $root 'patch_output\bundle'
$patcherDir = Join-Path $root 'patcher'
$outputDir = Join-Path $root 'patch_output'
New-Item -ItemType Directory -Force -Path $bundleDir | Out-Null

python scripts/make_patch_bundle.py `
  --from-zip $fromZipPath `
  --to-dir $toDirPath `
  --from-version $FromVersion `
  --to-version $ToVersion `
  --out-dir $bundleDir

Copy-Item -Force (Join-Path $bundleDir 'patch_payload.zip') (Join-Path $patcherDir 'patch_payload.zip')
Copy-Item -Force (Join-Path $bundleDir 'patch_manifest.json') (Join-Path $patcherDir 'patch_manifest.json')

$name = "LinkVideo.Helper_Patch_${FromVersion}_to_${ToVersion}.exe"
$out = Join-Path $outputDir $name
Push-Location $patcherDir
try {
    go build -trimpath -ldflags '-H=windowsgui' -o $out .
} finally {
    Pop-Location
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $patcherDir 'patch_payload.zip')
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $patcherDir 'patch_manifest.json')
}

if (-not (Test-Path $out)) { throw "Patch EXE was not created: $out" }
$item = Get-Item $out
if ($item.Length -lt 500000) { throw "Patch EXE is unexpectedly small: $($item.Length)" }
$hash = (Get-FileHash -Algorithm SHA256 $out).Hash.ToLowerInvariant()
Write-Host "PATCH READY: $out"
Write-Host "SHA256: $hash"
