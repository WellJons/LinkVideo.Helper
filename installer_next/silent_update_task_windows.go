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

    // Local ACL/task commands normally finish in fractions of a second. A few
    // seconds is enough to distinguish a healthy Windows component from a stuck
    // service/policy without making the user stare at 95% for a minute.
    if err := runHiddenTimeout(
        4*time.Second,
        "icacls.exe",
        stateDir,
        "/inheritance:e",
        "/grant", "*S-1-5-32-545:(OI)(CI)M",
        "/T", "/C", "/Q",
    ); err != nil {
        return fmt.Errorf("не удалось настроить права каталога обновлений: %w", err)
    }

    if err := runHiddenTimeout(
        6*time.Second,
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
    _ = runHiddenTimeout(
        4*time.Second,
        "schtasks.exe",
        "/Delete",
        "/TN", silentUpdateTaskName,
        "/F",
    )
    _ = os.RemoveAll(silentUpdateStateDir())
}

func verifySilentUpdateTask(dest string) error {
    // /Create /F is already the authoritative write result. A second synchronous
    // /Query added no security (the SYSTEM updater verifies manifest/version/SHA)
    // and doubled the opportunity to wait on a sick Task Scheduler service.
    // Runtime availability is checked later by Helper's bounded task_exists().
    updaterPath := filepath.Join(dest, silentUpdaterExeName)
    if _, err := os.Stat(updaterPath); err != nil {
        return err
    }
    return nil
}
