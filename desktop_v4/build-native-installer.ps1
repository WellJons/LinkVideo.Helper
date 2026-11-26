$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$dir=Join-Path $root 'installer_v4'
$version=(Get-Content (Join-Path $root 'desktop_v4/package.json') -Raw | ConvertFrom-Json).version
$payload=Join-Path $dir 'payload.tar.xz'
$runtime=Join-Path $root 'dist-v4/LinkVideo.Helper.4-win32-x64'
$env:GOOS='windows';$env:GOARCH='amd64';$env:CGO_ENABLED='0'
Push-Location $dir
try{
 [IO.File]::WriteAllBytes($payload,[byte[]]@(0))
 [IO.File]::WriteAllText((Join-Path $dir 'versioninfo.json'),'{}')
 go run github.com/josephspurrier/goversioninfo/cmd/goversioninfo@v1.7.0 -64 -icon (Join-Path $root 'icon.ico') -o resource.syso -company LinkVideo -file-version $version -product-version $version -product-name LinkVideo.Helper -description 'LinkVideo.Helper 4 Setup' -ver-major 4 versioninfo.json
 if($LASTEXITCODE -ne 0){throw 'Installer icon build failed'}
 go mod tidy
 if($LASTEXITCODE -ne 0){throw 'XZ dependency resolution failed'}
 go build -trimpath -ldflags "-H=windowsgui -s -w -X main.version=$version -X main.buildMode=uninstaller" -o (Join-Path $runtime 'Uninstall.exe') .
 if($LASTEXITCODE -ne 0){throw 'Uninstaller build failed'}
 python (Join-Path $root 'desktop_v4/build-installer-payload.py') $runtime $payload
 if($LASTEXITCODE -ne 0){throw 'Payload compression failed'}
 $output=Join-Path $root "dist-v4/LinkVideo.Helper.$version-Setup.exe"
 go build -trimpath -ldflags "-H=windowsgui -s -w -X main.version=$version -X main.buildMode=installer" -o $output .
 if($LASTEXITCODE -ne 0){throw 'Bootstrap build failed'}
}finally{Pop-Location}
