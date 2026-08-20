$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Invoke-Checked([string]$Label, [scriptblock]$Command) {
    Write-Host ""
    Write-Host "=== $Label ==="
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Get-ProductVersion([string]$Path) {
    $raw = [string](Get-Item -LiteralPath $Path).VersionInfo.ProductVersion
    $match = [regex]::Match($raw, '\d+(?:\.\d+){0,3}')
    if (-not $match.Success) { throw "No ProductVersion in $Path (raw: $raw)" }
    return $match.Value
}

Write-Host '============================================================'
Write-Host ' LinkVideo.Helper FULL RELEASE VERIFICATION'
Write-Host '============================================================'

$gitAvailable = $null -ne (Get-Command git -ErrorAction SilentlyContinue)
$isGitRepo = $gitAvailable -and (Test-Path (Join-Path $root '.git'))
$sourceCommit = 'no-git'
if ($isGitRepo) {
    $dirty = @(& git status --porcelain --untracked-files=no)
    if ($LASTEXITCODE -ne 0) { throw 'git status failed' }
    if ($dirty.Count -gt 0) {
        throw "Tracked working tree is dirty. Commit/stash changes before release verification:`n$($dirty -join "`n")"
    }
    $sourceCommit = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'git rev-parse HEAD failed' }
}

$version = (& python -c "from linkvideo_vpn_helper.version import APP_VERSION; print(APP_VERSION)").Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($version)) {
    throw 'Could not read APP_VERSION'
}
if ($version -notmatch '^\d+\.\d+\.\d+(?:\.\d+)?$') {
    throw "Invalid APP_VERSION: $version"
}
Write-Host "Version: $version"
Write-Host "Source:  $sourceCommit"

Invoke-Checked 'Install/check Python release dependencies' {
    python -m pip install -r requirements.txt pyinstaller ruff
}

# build_onedir is intentionally the single Python build entry point. It runs
# sync_release_version, generates Windows version metadata, executes the complete
# release_preflight (full audit + every core regression), and then runs a clean
# PyInstaller build.
Invoke-Checked 'Build and preflight application runtime' {
    cmd /d /c build_onedir.bat
}

Invoke-Checked 'Ruff critical correctness audit' {
    ruff check linkvideo_vpn_helper scripts --select E9,F63,F7,F82
}

Invoke-Checked 'Go installer/patcher/updater audit' {
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\audit_go.ps1
}

Invoke-Checked 'Build authoritative Setup and Uninstall' {
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_next_installer.ps1
}

Invoke-Checked 'Compile differential patch pipeline' {
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test_patch_builder.ps1
}

$setup = Join-Path $root 'installer_next\output\LinkVideo.Helper_Setup.exe'
$uninstall = Join-Path $root 'installer_next\output\Uninstall.exe'
$appExe = Join-Path $root 'dist\LinkVideo.Helper\LinkVideo.Helper.exe'
foreach ($required in @($setup, $uninstall, $appExe)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required build output missing: $required" }
}

Invoke-Checked 'Self-test exact produced Setup payload' {
    & $setup --self-test
}

$expected = @($version.Split('.') | ForEach-Object { [int]$_ })
while ($expected.Count -lt 4) { $expected += 0 }
function Assert-Version([string]$Path) {
    $actualText = Get-ProductVersion $Path
    $actual = @($actualText.Split('.') | ForEach-Object { [int]($_ -replace '[^0-9].*$','') })
    while ($actual.Count -lt 4) { $actual += 0 }
    if (($actual -join '.') -ne ($expected -join '.')) {
        throw "ProductVersion mismatch: $Path -> $actualText, expected $version"
    }
    return $actualText
}

$appVersion = Assert-Version $appExe
$setupVersion = Assert-Version $setup
$uninstallVersion = Assert-Version $uninstall

$setupItem = Get-Item -LiteralPath $setup
if ($setupItem.Length -lt 10MB) { throw "Setup is unexpectedly small: $($setupItem.Length) bytes" }
$setupHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $setup).Hash.ToLowerInvariant()

if ($isGitRepo) {
    $after = @(& git status --porcelain --untracked-files=no)
    if ($LASTEXITCODE -ne 0) { throw 'git status after build failed' }
    if ($after.Count -gt 0) {
        throw "Release build modified tracked source files:`n$($after -join "`n")"
    }
}

$outDir = Join-Path $root 'release_candidate'
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $outDir
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$rcName = "LinkVideo.Helper_Setup_${version}_RC.exe"
$rcSetup = Join-Path $outDir $rcName
Copy-Item -Force -LiteralPath $setup -Destination $rcSetup

$report = [ordered]@{
    version = $version
    source_commit = $sourceCommit
    verified_at_utc = [DateTime]::UtcNow.ToString('o')
    app_product_version = $appVersion
    setup_product_version = $setupVersion
    uninstall_product_version = $uninstallVersion
    setup_sha256 = $setupHash
    setup_size_bytes = $setupItem.Length
    exact_setup_self_test = 'passed'
    python_release_preflight = 'passed'
    ruff_critical = 'passed'
    go_vet = 'passed'
    patch_pipeline_compile = 'passed'
    live_routeros_google_archive_smoke = 'manual smoke of exact final draft Setup required before Publish Release'
}
$reportPath = Join-Path $outDir 'verification.json'
$report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $reportPath -Encoding utf8

Write-Host ""
Write-Host '============================================================'
Write-Host ' RELEASE VERIFICATION PASSED'
Write-Host '============================================================'
Write-Host "RC:      $rcSetup"
Write-Host "Version: $version"
Write-Host "SHA256:  $setupHash"
Write-Host "Size:    $([math]::Round($setupItem.Length / 1MB, 2)) MB"
Write-Host "Report:  $reportPath"
Write-Host 'Next gate: manual smoke of this RC, then the exact final draft Setup, against real RouterOS / Google Sheets / archive endpoints.'
