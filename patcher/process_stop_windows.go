//go:build windows

package main

import (
    "context"
    "errors"
    "fmt"
    "os/exec"
    "strings"
    "syscall"
    "time"
)

var helperImagesToStop = []string{
    "LinkVideo.Helper.exe",
    "LinkVideo VPN Helper.exe",
    "updater.exe",
    "LinkVideo.Helper.Updater.exe",
}

func imageRunning(image string) (bool, error) {
    ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
    defer cancel()

    cmd := exec.CommandContext(
        ctx,
        "tasklist.exe",
        "/FI", "IMAGENAME eq "+image,
        "/FO", "CSV",
        "/NH",
    )
    cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindowFlag}
    out, err := cmd.CombinedOutput()
    if errors.Is(ctx.Err(), context.DeadlineExceeded) {
        return false, errors.New("tasklist.exe не ответил за 4 секунды")
    }
    if err != nil {
        return false, fmt.Errorf("tasklist.exe: %w (%s)", err, strings.TrimSpace(string(out)))
    }
    needle := `"` + strings.ToLower(image) + `"`
    return strings.Contains(strings.ToLower(string(out)), needle), nil
}

func forceKillImage(image string) error {
    ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
    defer cancel()

    cmd := exec.CommandContext(ctx, "taskkill.exe", "/IM", image, "/T", "/F")
    cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindowFlag}
    out, err := cmd.CombinedOutput()
    if errors.Is(ctx.Err(), context.DeadlineExceeded) {
        return fmt.Errorf("taskkill.exe для %s не ответил за 4 секунды", image)
    }
    if err != nil {
        return fmt.Errorf("taskkill.exe %s: %w (%s)", image, err, strings.TrimSpace(string(out)))
    }
    return nil
}

// stopHelperVerified is a hard gate before touching Program Files. taskkill can
// return while Windows is still tearing a process down, especially when Qt DLLs
// are mapped. Re-check the process table and retry until every application image
// that can lock the runtime is actually gone. If that cannot be proven within
// the deadline, leave the installed runtime untouched.
func stopHelperVerified() error {
    deadline := time.Now().Add(15 * time.Second)
    var lastKillErr error

    for {
        running := make([]string, 0, len(helperImagesToStop))
        for _, image := range helperImagesToStop {
            active, err := imageRunning(image)
            if err != nil {
                return fmt.Errorf("не удалось проверить процесс %s: %w", image, err)
            }
            if active {
                running = append(running, image)
            }
        }
        if len(running) == 0 {
            return nil
        }

        for _, image := range running {
            if err := forceKillImage(image); err != nil {
                // The process may disappear between tasklist and taskkill. The
                // next tasklist pass is authoritative, so keep the error only
                // for a useful final diagnostic if it remains alive.
                lastKillErr = err
            }
        }

        if time.Now().After(deadline) {
            names := strings.Join(running, ", ")
            if lastKillErr != nil {
                return fmt.Errorf("не удалось завершить процессы LinkVideo.Helper (%s): %v", names, lastKillErr)
            }
            return fmt.Errorf("не удалось завершить процессы LinkVideo.Helper за 15 секунд: %s", names)
        }
        time.Sleep(350 * time.Millisecond)
    }
}
