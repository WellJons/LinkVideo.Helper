//go:build windows

package main

import (
    "fmt"
    "os"
    "path/filepath"
    "strings"
    "time"
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

func silentUpdateWarningPath() string {
    return filepath.Join(silentUpdateStateDir(), "setup-warning.txt")
}

func recordSilentUpdateWarning(err error) {
    if err == nil {
        _ = os.Remove(silentUpdateWarningPath())
        return
    }
    _ = os.MkdirAll(silentUpdateStateDir(), 0o755)
    _ = os.WriteFile(
        silentUpdateWarningPath(),
        []byte("Фоновые патчи недоступны: "+err.Error()+"\r\n"),
        0o644,
    )
}

func silentUpdateTaskCommand(updaterPath string) string {
    // /TR is one argument for schtasks.exe. Quoting the executable path is
    // mandatory because Program Files contains a space.
    return fmt.Sprintf(`"%s" --scheduled`, updaterPath)
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

    // Helper runs without elevation and only stages an already SHA-checked
    // pending patch here. The SYSTEM updater re-loads the official GitHub
    // manifest and validates the official SHA before executing anything.
    // Never let icacls hold the installer for minutes if Windows policy/services
    // are unhealthy.
    if err := runHiddenTimeout(
        8*time.Second,
        "icacls.exe",
        stateDir,
        "/inheritance:e",
        "/grant", "*S-1-5-32-545:(OI)(CI)M",
        "/T", "/C",
    ); err != nil {
        return fmt.Errorf("не удалось настроить права каталога обновлений: %w", err)
    }

    // PowerShell ScheduledTasks cmdlets can spend tens of seconds importing the
    // module or waiting for the scheduler service on some real workstations.
    // schtasks.exe talks to Task Scheduler directly, has no localization-sensitive
    // output parsing here, and is hard deadline-bounded by runHiddenTimeout.
    if err := runHiddenTimeout(
        10*time.Second,
        "schtasks.exe",
        "/Create",
        "/TN", silentUpdateTaskName,
        "/TR", silentUpdateTaskCommand(updaterPath),
        "/SC", "ONLOGON",
        "/RU", "SYSTEM",
        "/RL", "HIGHEST",
        "/F",
    ); err != nil {
        return fmt.Errorf("не удалось зарегистрировать фоновый updater: %w", err)
    }
    return nil
}

func removeSilentUpdateTask() {
    // Removal is cleanup, never a reason to keep the uninstall wizard waiting.
    // A missing task also isn't an uninstall failure.
    _ = runHiddenTimeout(
        6*time.Second,
        "schtasks.exe",
        "/Delete",
        "/TN", silentUpdateTaskName,
        "/F",
    )
    _ = os.RemoveAll(silentUpdateStateDir())
}

func verifySilentUpdateTask(dest string) error {
    updaterPath := filepath.Join(dest, silentUpdaterExeName)
    if _, err := os.Stat(updaterPath); err != nil {
        return err
    }

    // Registration is performed by this elevated installer with /RU SYSTEM,
    // /RL HIGHEST and /F. For runtime availability we only need to establish
    // that Task Scheduler can query the task. Parsing /Query text would make the
    // installer dependent on Windows language again. The privileged updater has
    // the actual security boundary: official manifest + exact version + SHA256.
    if err := runHiddenTimeout(
        6*time.Second,
        "schtasks.exe",
        "/Query",
        "/TN", silentUpdateTaskName,
    ); err != nil {
        return fmt.Errorf("Windows не подтвердила задачу фонового updater: %w", err)
    }
    return nil
}
