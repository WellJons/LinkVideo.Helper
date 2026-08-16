param(
    [Parameter(Mandatory = $true)]
    [string]$AppDir
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$targetDir = Join-Path $AppDir '_internal\tools'
$target = Join-Path $targetDir 'ffmpeg.exe'
$localSource = Join-Path $root 'tools\ffmpeg.exe'

New-Item -ItemType Directory -Force -Path $targetDir | Out-Null

if (Test-Path $localSource) {
    Write-Host '[FFmpeg] Using repository-local tools\ffmpeg.exe'
    Copy-Item -Force $localSource $target
} else {
    $url = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
    $tempDir = Join-Path ([IO.Path]::GetTempPath()) ('lv_ffmpeg_build_' + [Guid]::NewGuid().ToString('N'))
    $zipPath = Join-Path $tempDir 'ffmpeg.zip'
    New-Item -ItemType Directory -Force -Path $tempDir | Out-Null

    try {
        Write-Host '[FFmpeg] Downloading build dependency once in CI...'
        Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing
        if (-not (Test-Path $zipPath) -or (Get-Item $zipPath).Length -lt 10MB) {
            throw 'Downloaded FFmpeg archive is missing or unexpectedly small'
        }

        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [IO.Compression.ZipFile]::OpenRead($zipPath)
        try {
            $entry = $archive.Entries |
                Where-Object { ($_.FullName -replace '\\','/').ToLowerInvariant().EndsWith('/bin/ffmpeg.exe') } |
                Select-Object -First 1
            if (-not $entry) {
                throw 'ffmpeg.exe was not found in downloaded archive'
            }
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
        } finally {
            if ($archive) { $archive.Dispose() }
        }
    } finally {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tempDir
    }
}

if (-not (Test-Path $target)) {
    throw "Bundled FFmpeg is missing: $target"
}
$item = Get-Item $target
if ($item.Length -lt 10MB) {
    throw "Bundled FFmpeg is unexpectedly small: $($item.Length) bytes"
}

$versionText = (& $target -version 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $versionText -notmatch 'ffmpeg version') {
    throw 'Bundled FFmpeg failed runtime validation'
}

$hash = (Get-FileHash -Algorithm SHA256 $target).Hash.ToLowerInvariant()
Write-Host "[FFmpeg] Bundled: $([math]::Round($item.Length/1MB,2)) MB | SHA256 $hash"
