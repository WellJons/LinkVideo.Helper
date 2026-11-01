$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -c "import struct,sys; sys.exit(0 if struct.calcsize('P') == 4 else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Use a 32-bit Python interpreter for the Ula bridge' }

python -m pip install --only-binary=:all: 'cx_Oracle==8.3.0' 'pyinstaller>=6,<7'
if ($LASTEXITCODE -ne 0) { throw 'Failed to install x86 Oracle bridge tools' }

$out = Join-Path $root 'build\ula_x86_dist'
$work = Join-Path $root 'build\ula_x86_work'
$spec = Join-Path $root 'build\ula_x86_spec'
python -m PyInstaller --noconfirm --clean --onefile --console --name LinkVideo.UlaBridge32 --paths $root --distpath $out --workpath $work --specpath $spec scripts\ula_x86_bridge.py
if ($LASTEXITCODE -ne 0) { throw 'Failed to build Ula x86 bridge' }
$exe = Join-Path $out 'LinkVideo.UlaBridge32.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'Ula x86 bridge binary missing' }
& $exe --self-test
if ($LASTEXITCODE -ne 0) { throw 'Ula x86 bridge self-test failed' }

Write-Host "Ula x86 bridge built and verified: $exe"
if ($env:GITHUB_ENV) {
    "ULA_BRIDGE_X86_PATH=$exe" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
}
