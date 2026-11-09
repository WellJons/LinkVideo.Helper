# Operator-only startup task: no RouterOS config writes.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$RepositoryRoot,
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [Parameter(Mandatory=$true)][string]$ServiceAccountJson,
    [string]$RouterOSUsername = "",
    [string]$SpreadsheetId = "",
    # Canary only. Opt in to additional servers explicitly with -Servers.
    [string[]]$Servers = @('vpn01.linkvideo.ru')
)
$ErrorActionPreference = 'Stop'
$who = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $who.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Запустите PowerShell от имени администратора.'
}
$root = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$python = (Resolve-Path -LiteralPath $PythonExe).Path
$keySource = (Resolve-Path -LiteralPath $ServiceAccountJson).Path
$agent = Join-Path $root 'scripts\vpn_fleet_health_agent.py'
if (-not (Test-Path -LiteralPath $agent -PathType Leaf)) { throw "Не найден агент: $agent" }
if ($root -match '(?i)\\(Downloads|Temp)\\') {
    throw 'Репозиторий должен находиться в постоянной папке, а не Downloads/Temp.'
}
if (-not $RouterOSUsername) { $RouterOSUsername = Read-Host 'RouterOS user (read-only recommended)' }
if (-not $RouterOSUsername.Trim()) { throw 'RouterOS user is empty' }
$secure = Read-Host 'RouterOS password' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    if ([string]::IsNullOrWhiteSpace($password)) { throw 'RouterOS password is empty' }
    $rootData = Join-Path $env:ProgramData 'LinkVideo.Helper\VPNFleet'
    New-Item -ItemType Directory -Force -Path $rootData | Out-Null
    # Well-known SIDs avoid Windows localization differences.
    & icacls.exe $rootData '/inheritance:r' '/grant:r' '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not secure configuration directory' }
    $destKey = Join-Path $rootData 'google-service-account.json'
    Copy-Item -LiteralPath $keySource -Destination $destKey -Force
    & icacls.exe $destKey '/inheritance:r' '/grant:r' '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not protect Google service-account JSON' }
    $configFile = Join-Path $rootData 'config.json'
    $config = [ordered]@{
        routeros_username = $RouterOSUsername.Trim()
        routeros_password = $password
        routeros_port = 8728
        routeros_timeout = 6
        servers = @($Servers)
        service_account_file = $destKey
        interval_seconds = 300
        log_file = (Join-Path $rootData 'vpn-health.log')
    }
    if ($SpreadsheetId.Trim()) { $config['spreadsheet_id'] = $SpreadsheetId.Trim() }
    $json = $config | ConvertTo-Json -Depth 4
    [IO.File]::WriteAllText($configFile, $json, (New-Object Text.UTF8Encoding($false)))
    & icacls.exe $configFile '/inheritance:r' '/grant:r' '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not protect RouterOS config file' }
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    $password = $null
    $secure.Dispose()
}
# Preflight uses only RouterOS reads. Google is not contacted.
& $python $agent --config $configFile --once --dry-run
if ($LASTEXITCODE -ne 0) { throw 'Read-only preflight failed; task was NOT installed' }

$taskName = 'LinkVideo.VPN.FleetMonitor'
$argument = ('"{0}" --config "{1}"' -f $agent, $configFile)
$action = New-ScheduledTaskAction -Execute $python -Argument $argument -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Host "Готово: $taskName запускается при старте Windows, независимо от окна Helper."
Write-Host "Статус: Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
Write-Host "Журнал: $rootData\vpn-health.log"
Write-Host 'Google Sheets: обновляется только LV Мониторинг; PPP passwords не экспортируются.'
