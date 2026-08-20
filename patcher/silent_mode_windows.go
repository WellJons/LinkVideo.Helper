//go:build windows

package main

import (
    "encoding/json"
    "errors"
    "fmt"
    "os"
    "path/filepath"
    "strings"
)

// The privileged scheduled updater launches official patches with --silent.
// Handle that mode before the normal interactive main() so there are no
// message boxes and no attempt to start Helper from the SYSTEM session.
func init() {
    if !patchHasArg("--silent") {
        return
    }
    if err := applyPatchSilently(); err != nil {
        recordSilentPatchError(err)
        os.Exit(1)
    }
    recordSilentPatchError(nil)
    os.Exit(0)
}

func patchHasArg(wanted string) bool {
    for _, arg := range os.Args[1:] {
        if strings.EqualFold(strings.TrimSpace(arg), wanted) {
            return true
        }
    }
    return false
}

func applyPatchSilently() error {
    var m manifest
    if err := json.Unmarshal(patchManifest, &m); err != nil {
        return fmt.Errorf("повреждён manifest патча: %w", err)
    }
    if m.Format != 1 || m.FromVersion == "" || m.ToVersion == "" || len(m.Changed) == 0 && len(m.Deleted) == 0 {
        return errors.New("manifest патча неполный")
    }
    if !validVersion(m.FromVersion) || !validVersion(m.ToVersion) {
        return errors.New("manifest содержит некорректную версию")
    }
    if !strings.EqualFold(sha256Bytes(patchPayload), m.PayloadSHA256) {
        return errors.New("SHA-256 встроенного payload не совпадает с manifest")
    }

    elevated, err := ensureElevated()
    if err != nil {
        return err
    }
    if !elevated {
        // ensureElevated has launched an elevated copy with the same --silent
        // argument. The non-elevated bootstrap has nothing else to do.
        return nil
    }

    installDir := defaultInstallDir()
    appPath := filepath.Join(installDir, "LinkVideo.Helper.exe")
    installedVersion, err := productVersion(appPath)
    if err != nil {
        return err
    }
    if !sameVersion(installedVersion, m.FromVersion) {
        return fmt.Errorf("патч предназначен для %s, но установлена %s", m.FromVersion, installedVersion)
    }

    // Never touch Program Files until Windows confirms that all application
    // processes capable of holding runtime files open are actually gone.
    if err := stopHelperVerified(); err != nil {
        return err
    }

    backupRoot, err := os.MkdirTemp("", "LinkVideo.Helper-Patch-Backup-")
    if err != nil {
        return err
    }
    defer os.RemoveAll(backupRoot)

    affected := make(map[string]struct{}, len(m.Changed)+len(m.Deleted))
    for name := range m.Changed {
        safe, err := safeRelative(name)
        if err != nil {
            return err
        }
        affected[safe] = struct{}{}
    }
    for _, name := range m.Deleted {
        safe, err := safeRelative(name)
        if err != nil {
            return err
        }
        affected[safe] = struct{}{}
    }

    existingBefore := make(map[string]bool, len(affected))
    for name := range affected {
        src := filepath.Join(installDir, filepath.FromSlash(name))
        if info, err := os.Stat(src); err == nil && !info.IsDir() {
            existingBefore[name] = true
            dst := filepath.Join(backupRoot, filepath.FromSlash(name))
            if err := copyFile(src, dst); err != nil {
                return fmt.Errorf("не удалось создать резервную копию %s: %w", name, err)
            }
        }
    }

    if err := applyChangedFiles(installDir, m); err != nil {
        return rollbackAfterFailure(err, installDir, backupRoot, existingBefore, affected)
    }
    if err := applyDeletes(installDir, m.Deleted); err != nil {
        return rollbackAfterFailure(err, installDir, backupRoot, existingBefore, affected)
    }
    if err := verifyChangedFiles(installDir, m.Changed); err != nil {
        return rollbackAfterFailure(err, installDir, backupRoot, existingBefore, affected)
    }

    nextVersion, err := productVersion(appPath)
    if err != nil {
        return rollbackAfterFailure(
            fmt.Errorf("после патча не удалось прочитать версию приложения: %w", err),
            installDir, backupRoot, existingBefore, affected,
        )
    }
    if !sameVersion(nextVersion, m.ToVersion) {
        return rollbackAfterFailure(
            fmt.Errorf("после патча приложение сообщает версию %s вместо %s", nextVersion, m.ToVersion),
            installDir, backupRoot, existingBefore, affected,
        )
    }

    if err := runHidden(
        "reg.exe", "add",
        `HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\LinkVideo.Helper`,
        "/v", "DisplayVersion", "/t", "REG_SZ", "/d", m.ToVersion, "/f",
    ); err != nil {
        return rollbackAfterFailure(
            fmt.Errorf("не удалось обновить версию программы в реестре: %w", err),
            installDir, backupRoot, existingBefore, affected,
        )
    }
    return nil
}
