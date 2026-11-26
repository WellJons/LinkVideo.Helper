$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$dir=Join-Path $root 'installer_v4'
$version=(Get-Content (Join-Path $root 'desktop_v4/package.json') -Raw | ConvertFrom-Json).version
$payload=Join-Path $dir 'payload.exe'
$env:GOOS='windows';$env:GOARCH='amd64';$env:CGO_ENABLED='0'
Push-Location $dir
try {
    [IO.File]::WriteAllBytes($payload,[byte[]]@(0))
    [IO.File]::WriteAllText((Join-Path $dir 'versioninfo.json'),'{}')
    go run github.com/josephspurrier/goversioninfo/cmd/goversioninfo@v1.7.0 -64 -icon (Join-Path $root 'icon.ico') -o resource.syso -company LinkVideo -file-version $version -product-version $version -product-name LinkVideo.Helper -description 'LinkVideo.Helper 4 Setup' -ver-major 4 versioninfo.json
    if ($LASTEXITCODE -ne 0) {throw 'Failed to generate installer icon'}
    go build -trimpath -ldflags "-H=windowsgui -X main.version=$version -X main.buildMode=uninstaller" -o Uninstall.exe .
    if ($LASTEXITCODE -ne 0) {throw 'Uninstaller build failed'}
    & (Join-Path $root 'desktop_v4/installer-brand.ps1')
    $iscc=Join-Path ([Environment]::GetEnvironmentVariable('ProgramFiles(x86)')) 'Inno Setup 6\ISCC.exe'
    & $iscc (Join-Path $root 'desktop_v4/installer.iss')
    if ($LASTEXITCODE -ne 0) {throw 'Compressed payload build failed'}
    if ((Get-Item $payload).Length -lt 1000000) {throw 'Invalid compressed payload'}
    $output=Join-Path $root "dist-v4/LinkVideo.Helper.$version-Setup.exe"
    go build -trimpath -ldflags "-H=windowsgui -X main.version=$version -X main.buildMode=installer" -o $output .
    if ($LASTEXITCODE -ne 0) {throw 'Native installer build failed'}
} finally {Pop-Location}
