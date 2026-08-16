//go:build windows

package main

import (
    "errors"
    "fmt"
    "os"
    "path/filepath"
    "strings"
)

const (
    silentUpdateTaskName = "LinkVideo.Helper Silent Update"
    silentUpdaterExeName = "LinkVideo.Helper.Updater.exe"
)

func silentUpdateStateDir() string {
    programData := strings.TrimSpace(os.Getenv("PROGRAMDATA"))
    if programData == "" {
        programData = `C:\ProgramData`
    }
    return filepath.Join(programData, "LinkVideo.Helper", "Updates")
}

func registerSilentUpdateTask(dest string) error {
    updaterPath := filepath.Join(dest, silentUpdaterExeName)
    if _, err := os.Stat(updaterPath); err != nil {
        return fmt.Errorf("не найден %s: %w", silentUpdaterExeName, err)
    }

    stateDir := silentUpdateStateDir()
    if err := os.MkdirAll(stateDir, 0o755); err != nil {
        return fmt.Errorf("не удалось создать каталог фоновых обновлений: %w", err)
    }

    // Helper runs without elevation and only needs to stage pending.json and
    // pending-patch.exe here. Use the built-in Users SID so this works on
    // localized Windows installations. The privileged updater independently
    // verifies the official GitHub manifest and SHA before executing anything.
    if err := runHidden(
        "icacls.exe",
        stateDir,
        "/inheritance:e",
        "/grant", "*S-1-5-32-545:(OI)(CI)M",
        "/T", "/C",
    ); err != nil {
        return fmt.Errorf("не удалось настроить права каталога обновлений: %w", err)
    }

    script := fmt.Sprintf(
        `$ErrorActionPreference='Stop';`+
            `$action=New-ScheduledTaskAction -Execute '%s' -Argument '--scheduled';`+
            `$principal=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest;`+
            `$settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10);`+
            `Register-ScheduledTask -TaskName '%s' -Action $action -Principal $principal -Settings $settings -Force|Out-Null`,
        psEscape(updaterPath), psEscape(silentUpdateTaskName),
    )
    if err := runPowerShell(script); err != nil {
        return fmt.Errorf("не удалось зарегистрировать фоновый updater: %w", err)
    }
    return nil
}

func removeSilentUpdateTask() {
    script := fmt.Sprintf(
        `$ErrorActionPreference='SilentlyContinue';`+
            `Unregister-ScheduledTask -TaskName '%s' -Confirm:$false -ErrorAction SilentlyContinue`,
        psEscape(silentUpdateTaskName),
    )
    _ = runPowerShell(script)
    _ = os.RemoveAll(silentUpdateStateDir())
}

func verifySilentUpdateTask(dest string) error {
    updaterPath := filepath.Join(dest, silentUpdaterExeName)
    if _, err := os.Stat(updaterPath); err != nil {
        return err
    }

    // Task Scheduler normalizes principals and action strings differently on
    // different Windows builds/locales. Do not compare its presentation strings
    // literally: resolve the account to the well-known SYSTEM SID and normalize
    // quotes, whitespace, environment variables and path casing first.
    script := fmt.Sprintf(
        `$ErrorActionPreference='Stop';`+
            `$t=Get-ScheduledTask -TaskName '%s' -ErrorAction Stop;`+
            `if(-not $t){throw 'task missing'};`+
            `$uid=([string]$t.Principal.UserId).Trim();`+
            `$sid='';`+
            `if($uid -eq 'S-1-5-18'){$sid=$uid}else{`+
                `try{$sid=([System.Security.Principal.NTAccount]::new($uid)).Translate([System.Security.Principal.SecurityIdentifier]).Value}catch{$sid=''}};`+
            `if($sid -ne 'S-1-5-18'){throw ('task principal is not SYSTEM: '+$uid)};`+
            `$a=$t.Actions|Select-Object -First 1;`+
            `if(-not $a){throw 'task action missing'};`+
            `$actual=[Environment]::ExpandEnvironmentVariables(([string]$a.Execute).Trim().Trim('"'));`+
            `$expected=[Environment]::ExpandEnvironmentVariables('%s');`+
            `try{$actual=[IO.Path]::GetFullPath($actual)}catch{};`+
            `try{$expected=[IO.Path]::GetFullPath($expected)}catch{};`+
            `if(-not [string]::Equals($actual,$expected,[System.StringComparison]::OrdinalIgnoreCase)){throw ('task action mismatch: '+$actual)};`+
            `$args=([string]$a.Arguments).Trim();`+
            `if($args.Length -ge 2 -and $args.StartsWith('"') -and $args.EndsWith('"')){$args=$args.Substring(1,$args.Length-2).Trim()};`+
            `if($args -ne '--scheduled'){throw ('task arguments mismatch: '+$args)}`,
        psEscape(silentUpdateTaskName), psEscape(updaterPath),
    )
    if err := runPowerShell(script); err != nil {
        return errors.New("задача фонового обновления создана, но Windows не подтвердила её параметры")
    }
    return nil
}
