//go:build windows

package main

import (
    "os"
    "path/filepath"
    "strings"
    "time"
)

func silentPatchErrorPath() string {
    programData := strings.TrimSpace(os.Getenv("PROGRAMDATA"))
    if programData == "" {
        programData = `C:\ProgramData`
    }
    return filepath.Join(programData, "LinkVideo.Helper", "Updates", "patch-error.txt")
}

func recordSilentPatchError(err error) {
    path := silentPatchErrorPath()
    if err == nil {
        _ = os.Remove(path)
        return
    }
    _ = os.MkdirAll(filepath.Dir(path), 0o755)
    text := time.Now().Format(time.RFC3339) + "\r\n" + err.Error() + "\r\n"
    _ = os.WriteFile(path, []byte(text), 0o644)
}
