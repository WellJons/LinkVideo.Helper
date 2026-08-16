$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$versionLine = Select-String -Path 'linkvideo_vpn_helper/version.py' -Pattern '^APP_VERSION\s*=\s*["'']([^"'']+)["'']' | Select-Object -First 1
if (-not $versionLine) { throw 'APP_VERSION not found' }
$version = $versionLine.Matches[0].Groups[1].Value
$versionParts = @($version.Split('.') | ForEach-Object { [int]$_ })
while ($versionParts.Count -lt 4) { $versionParts += 0 }

$appDir = Join-Path $root 'dist\LinkVideo.Helper'
if (-not (Test-Path (Join-Path $appDir 'LinkVideo.Helper.exe'))) {
    throw 'dist\LinkVideo.Helper is missing. Build the application first.'
}

$installerDir = Join-Path $root 'installer_next'
$silentUpdaterDir = Join-Path $root 'silent_updater'
$outputDir = Join-Path $installerDir 'output'
$payloadPath = Join-Path $installerDir 'payload.zip'
$resourcePath = Join-Path $installerDir 'resource.syso'
$versionInfoPath = Join-Path $installerDir 'versioninfo.generated.json'
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $outputDir '*.exe')

function New-VersionResource([string]$Description, [string]$OriginalName, [string]$InternalName) {
    Remove-Item -Force -ErrorAction SilentlyContinue $resourcePath
    Set-Content -LiteralPath $versionInfoPath -Value '{}' -Encoding utf8NoBOM
    $iconPath = Join-Path $root 'icon.ico'
    Push-Location $installerDir
    try {
        go run github.com/josephspurrier/goversioninfo/cmd/goversioninfo@v1.7.0 `
            -64 `
            -icon $iconPath `
            -o 'resource.syso' `
            -company 'LinkVideo' `
            -file-version $version `
            -product-version $version `
            -product-name 'LinkVideo.Helper' `
            -description $Description `
            -original-name $OriginalName `
            -internal-name $InternalName `
            -ver-major $versionParts[0] `
            -ver-minor $versionParts[1] `
            -ver-patch $versionParts[2] `
            -ver-build $versionParts[3] `
            -product-ver-major $versionParts[0] `
            -product-ver-minor $versionParts[1] `
            -product-ver-patch $versionParts[2] `
            -product-ver-build $versionParts[3] `
            $versionInfoPath
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $resourcePath)) {
            throw 'Failed to generate Windows version/icon resource'
        }
    } finally {
        Pop-Location
        Remove-Item -Force -ErrorAction SilentlyContinue $versionInfoPath
    }
}

Write-Host "[Next installer] Version $version"
Write-Host '[1/7] Building standalone uninstaller...'
python -c "import zipfile; zipfile.ZipFile(r'$payloadPath','w').close()"
New-VersionResource 'LinkVideo.Helper Uninstaller' 'Uninstall.exe' 'LinkVideo.Helper.Uninstall'
Push-Location $installerDir
try {
    go build -trimpath -ldflags "-H=windowsgui -X main.version=$version -X main.buildMode=uninstaller" -o (Join-Path $outputDir 'Uninstall.exe') .
    if ($LASTEXITCODE -ne 0) { throw 'Failed to build Uninstall.exe' }
} finally {
    Pop-Location
    Remove-Item -Force -ErrorAction SilentlyContinue $resourcePath
}

Write-Host '[2/7] Building privileged silent updater...'
$updaterOutput = Join-Path $appDir 'LinkVideo.Helper.Updater.exe'
Remove-Item -Force -ErrorAction SilentlyContinue $updaterOutput
Push-Location $silentUpdaterDir
try {
    go build -trimpath -ldflags '-H=windowsgui' -o $updaterOutput .
    if ($LASTEXITCODE -ne 0) { throw 'Failed to build LinkVideo.Helper.Updater.exe' }
} finally {
    Pop-Location
}
if (-not (Test-Path $updaterOutput) -or (Get-Item $updaterOutput).Length -lt 200000) {
    throw 'Silent updater output is missing or unexpectedly small'
}

Write-Host '[3/7] Bundling FFmpeg for archive downloads...'
& (Join-Path $root 'scripts\prepare_bundled_ffmpeg.ps1') -AppDir $appDir
if ($LASTEXITCODE -ne 0) { throw 'Failed to prepare bundled FFmpeg' }
$bundledFfmpeg = Join-Path $appDir '_internal\tools\ffmpeg.exe'
if (-not (Test-Path $bundledFfmpeg)) { throw 'Bundled FFmpeg is missing from application payload' }

Write-Host '[4/7] Preparing application payload...'
Copy-Item -Force (Join-Path $outputDir 'Uninstall.exe') (Join-Path $appDir 'Uninstall.exe')
python -c "import pathlib,zipfile; root=pathlib.Path(r'$appDir'); out=pathlib.Path(r'$payloadPath'); z=zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED,compresslevel=9); [(z.write(p,p.relative_to(root).as_posix())) for p in root.rglob('*') if p.is_file()]; z.close()"
python -c "import zipfile; p=r'$payloadPath'; z=zipfile.ZipFile(p); names={n.replace('\\','/').lower() for n in z.namelist()}; assert '_internal/tools/ffmpeg.exe' in names, 'FFmpeg missing from installer payload'; z.close(); print('[FFmpeg] payload entry verified')"
if ($LASTEXITCODE -ne 0) { throw 'FFmpeg is missing from installer payload zip' }

Write-Host '[5/7] Building one-file LinkVideo installer...'
New-VersionResource 'LinkVideo.Helper Setup' 'LinkVideo.Helper_Setup.exe' 'LinkVideo.Helper.Setup'
Push-Location $installerDir
try {
    go build -trimpath -ldflags "-H=windowsgui -X main.version=$version -X main.buildMode=installer" -o (Join-Path $outputDir 'LinkVideo.Helper_Setup_Next.exe') .
    if ($LASTEXITCODE -ne 0) { throw 'Failed to build LinkVideo.Helper_Setup_Next.exe' }
} finally {
    Pop-Location
    Remove-Item -Force -ErrorAction SilentlyContinue $resourcePath
}

Write-Host '[6/7] Verifying installer files, updater and ProductVersion...'
$setup = Join-Path $outputDir 'LinkVideo.Helper_Setup_Next.exe'
$uninstall = Join-Path $outputDir 'Uninstall.exe'
foreach ($file in @($setup, $uninstall)) {
    if (-not (Test-Path $file)) { throw "Missing build output: $file" }
    $item = Get-Item $file
    if ($item.Length -lt 500000) { throw "Output is unexpectedly small: $($item.Name)" }
    $productVersion = [string]$item.VersionInfo.ProductVersion
    if ([string]::IsNullOrWhiteSpace($productVersion)) {
        throw "$($item.Name) has no ProductVersion"
    }
    $productParts = @($productVersion.Split('.') | ForEach-Object { [int]($_ -replace '[^0-9].*$','') })
    while ($productParts.Count -gt 1 -and $productParts[-1] -eq 0) { $productParts = $productParts[0..($productParts.Count-2)] }
    $expectedParts = @($versionParts)
    while ($expectedParts.Count -gt 1 -and $expectedParts[-1] -eq 0) { $expectedParts = $expectedParts[0..($expectedParts.Count-2)] }
    if (($productParts -join '.') -ne ($expectedParts -join '.')) {
        throw "$($item.Name) ProductVersion is '$productVersion', expected '$version'"
    }
    $hash = (Get-FileHash -Algorithm SHA256 $file).Hash.ToLowerInvariant()
    Write-Host "$($item.Name): $([math]::Round($item.Length/1MB,2)) MB | ProductVersion $productVersion | SHA256 $hash"
}
$updaterItem = Get-Item $updaterOutput
$updaterHash = (Get-FileHash -Algorithm SHA256 $updaterOutput).Hash.ToLowerInvariant()
Write-Host "$($updaterItem.Name): $([math]::Round($updaterItem.Length/1MB,2)) MB | SHA256 $updaterHash"

Write-Host '[7/7] Creating private payload baseline for future patches...'
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $root 'release_payload')
python scripts/make_release_payload.py --source $appDir --version $version --out-dir (Join-Path $root 'release_payload')

Remove-Item -Force -ErrorAction SilentlyContinue $payloadPath
Remove-Item -Force -ErrorAction SilentlyContinue $resourcePath
Remove-Item -Force -ErrorAction SilentlyContinue $versionInfoPath
Write-Host 'NEXT INSTALLER BUILD OK'
