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
$resourcePath = Join-Path $installerDir 'resource.syso'
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $outputDir '*.exe')

function New-VersionResource([string]$Description, [string]$OriginalName, [string]$InternalName) {
    Remove-Item -Force -ErrorAction SilentlyContinue $resourcePath
    Push-Location $installerDir
    try {
        # Pinned generator: resource.syso is consumed automatically by go build.
        go run github.com/josephspurrier/goversioninfo/cmd/goversioninfo@v1.7.0 `
            -64 `
            -icon=(Join-Path $root 'icon.ico') `
            -o='resource.syso' `
            -file-version=$version `
            -product-version=$version `
            -product-name='LinkVideo.Helper' `
            -description=$Description `
            -original-name=$OriginalName `
            -internal-name=$InternalName
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $resourcePath)) {
            throw 'Failed to generate Windows version/icon resource'
        }
    } finally {
        Pop-Location
    }
}

Write-Host "[Next installer] Version $version"
Write-Host '[1/5] Building standalone uninstaller...'
python -c "import zipfile; zipfile.ZipFile(r'$payloadPath','w').close()"
New-VersionResource 'LinkVideo.Helper Uninstaller' 'Uninstall.exe' 'LinkVideo.Helper.Uninstall'
Push-Location $installerDir
try {
    go build -trimpath -ldflags "-H=windowsgui -X main.version=$version -X main.buildMode=uninstaller" -o (Join-Path $outputDir 'Uninstall.exe') .
} finally {
    Pop-Location
    Remove-Item -Force -ErrorAction SilentlyContinue $resourcePath
}

Write-Host '[2/5] Preparing application payload...'
Copy-Item -Force (Join-Path $outputDir 'Uninstall.exe') (Join-Path $appDir 'Uninstall.exe')
python -c "import pathlib,zipfile; root=pathlib.Path(r'$appDir'); out=pathlib.Path(r'$payloadPath'); z=zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED,compresslevel=9); [(z.write(p,p.relative_to(root).as_posix())) for p in root.rglob('*') if p.is_file()]; z.close()"

Write-Host '[3/5] Building one-file LinkVideo installer...'
New-VersionResource 'LinkVideo.Helper Setup' 'LinkVideo.Helper_Setup.exe' 'LinkVideo.Helper.Setup'
Push-Location $installerDir
try {
    go build -trimpath -ldflags "-H=windowsgui -X main.version=$version -X main.buildMode=installer" -o (Join-Path $outputDir 'LinkVideo.Helper_Setup_Next.exe') .
} finally {
    Pop-Location
    Remove-Item -Force -ErrorAction SilentlyContinue $resourcePath
}

Write-Host '[4/5] Verifying installer files and ProductVersion...'
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
    $normalized = ($productVersion -replace '\.0$','')
    if ($normalized -ne $version -and $productVersion -ne $version) {
        throw "$($item.Name) ProductVersion is '$productVersion', expected '$version'"
    }
    $hash = (Get-FileHash -Algorithm SHA256 $file).Hash.ToLowerInvariant()
    Write-Host "$($item.Name): $([math]::Round($item.Length/1MB,2)) MB | ProductVersion $productVersion | SHA256 $hash"
}

Write-Host '[5/5] Creating private payload baseline for future patches...'
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $root 'release_payload')
python scripts/make_release_payload.py --source $appDir --version $version --out-dir (Join-Path $root 'release_payload')

Remove-Item -Force -ErrorAction SilentlyContinue $payloadPath
Remove-Item -Force -ErrorAction SilentlyContinue $resourcePath
Write-Host 'NEXT INSTALLER BUILD OK'
