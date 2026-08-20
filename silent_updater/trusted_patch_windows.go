//go:build windows

package main

import (
    "errors"
    "fmt"
    "io"
    "os"
    "path/filepath"
    "strings"
)

func prepareTrustedPatch(stagedPath, expectedHash string) (string, func(), error) {
    // Program Files is not writable by the normal Helper process. Copying the
    // already checked staging file here closes the hash-to-exec replacement
    // window that would exist in the user-writable ProgramData staging folder.
    trustedDir := filepath.Join(installDir(), ".update")
    if err := os.MkdirAll(trustedDir, 0o700); err != nil {
        return "", func() {}, fmt.Errorf("не удалось создать защищённый каталог патча: %w", err)
    }
    trustedPath := filepath.Join(trustedDir, "official-patch.exe")
    cleanup := func() {
        _ = os.Remove(trustedPath)
        _ = os.Remove(trustedDir)
    }
    cleanup()
    if err := copyPatchFile(stagedPath, trustedPath); err != nil {
        return "", cleanup, err
    }
    actualHash, err := sha256File(trustedPath)
    if err != nil {
        cleanup()
        return "", cleanup, err
    }
    if !strings.EqualFold(actualHash, expectedHash) {
        cleanup()
        return "", cleanup, errors.New("SHA-256 защищённой копии патча не совпадает с официальным manifest")
    }
    return trustedPath, cleanup, nil
}

func copyPatchFile(src, dst string) error {
    input, err := os.Open(src)
    if err != nil {
        return err
    }
    defer input.Close()
    output, err := os.OpenFile(dst, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o700)
    if err != nil {
        return err
    }
    _, copyErr := io.Copy(output, input)
    closeErr := output.Close()
    if copyErr != nil {
        _ = os.Remove(dst)
        return copyErr
    }
    if closeErr != nil {
        _ = os.Remove(dst)
        return closeErr
    }
    return nil
}
