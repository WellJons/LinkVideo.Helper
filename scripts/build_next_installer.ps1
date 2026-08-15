$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$versionLine = Select-String -Path 'linkvideo_vpn_helper/version.py' -Pattern '^APP_VERSION\s*=\s*["'']([^"'']+)["'']' | Select-Object -First 1
if (-not $versionLine) { throw 'APP_VERSION not found' }
$version = $versionLine.Matches[0].Groups[1].Value

$appDir = Join-Path $root 'dist\LinkVideo.Helper'
if (-not (Test-Path (Join-Path $appDir 'LinkVideo.Helper.exe'))) {
    throw 'dist\LinkVideo.Helper is missing. Build the application first.'
}

$installerDir = Join-Path $root 'installer_next'
$outputDir = Join-Path $installerDir 'output'
$payloadPath = Join-Path $installerDir 'payload.zip'
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $outputDir '*.exe')

Write-Host "[Next installer] Version $version"
Write-Host '[1/4] Building standalone uninstaller...'
python -c "import zipfile; zipfile.ZipFile(r'$payloadPath','w').close()"
Push-Location $installerDir
try {
    go build -trimpath -ldflags "-H=windowsgui -X main.version=$version -X main.buildMode=uninstaller" -o (Join-Path $outputDir 'Uninstall.exe') .
} finally { Pop-Location }

Write-Host '[2/4] Preparing application payload...'
Copy-Item -Force (Join-Path $outputDir 'Uninstall.exe') (Join-Path $appDir 'Uninstall.exe')
python -c "import pathlib,zipfile; root=pathlib.Path(r'$appDir'); out=pathlib.Path(r'$payloadPath'); z=zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED,compresslevel=9); [(z.write(p,p.relative_to(root).as_posix())) for p in root.rglob('*') if p.is_file()]; z.close()"

Write-Host '[3/4] Building one-file LinkVideo installer...'
Push-Location $installerDir
try {
    go build -trimpath -ldflags "-H=windowsgui -X main.version=$version -X main.buildMode=installer" -o (Join-Path $outputDir 'LinkVideo.Helper_Setup_Next.exe') .
} finally { Pop-Location }

Write-Host '[4/4] Verifying files...'
$setup = Join-Path $outputDir 'LinkVideo.Helper_Setup_Next.exe'
$uninstall = Join-Path $outputDir 'Uninstall.exe'
foreach ($file in @($setup, $uninstall)) {
    if (-not (Test-Path $file)) { throw "Missing build output: $file" }
    $item = Get-Item $file
    if ($item.Length -lt 500000) { throw "Output is unexpectedly small: $($item.Name)" }
    $hash = (Get-FileHash -Algorithm SHA256 $file).Hash.ToLowerInvariant()
    Write-Host "$($item.Name): $([math]::Round($item.Length/1MB,2)) MB | SHA256 $hash"
}

Remove-Item -Force -ErrorAction SilentlyContinue $payloadPath
Write-Host 'NEXT INSTALLER BUILD OK'
