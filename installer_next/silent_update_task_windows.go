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
    script := fmt.Sprintf(
        `$ErrorActionPreference='Stop';`+
            `$t=Get-ScheduledTask -TaskName '%s';`+
            `if(-not $t){throw 'task missing'};`+
            `if($t.Principal.UserId -ne 'SYSTEM'){throw 'task principal is not SYSTEM'};`+
            `$a=$t.Actions|Select-Object -First 1;`+
            `if([string]$a.Execute -ne '%s'){throw 'task action mismatch'}`,
        psEscape(silentUpdateTaskName), psEscape(updaterPath),
    )
    if err := runPowerShell(script); err != nil {
        return errors.New("задача фонового обновления зарегистрирована некорректно")
    }
    return nil
}
