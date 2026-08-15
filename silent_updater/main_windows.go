//go:build windows

package main

import (
    "crypto/sha256"
    "encoding/hex"
    "encoding/json"
    "errors"
    "fmt"
    "io"
    "net/http"
    "os"
    "os/exec"
    "path/filepath"
    "regexp"
    "strconv"
    "strings"
    "syscall"
    "time"
)

const (
    manifestURL = "https://raw.githubusercontent.com/WellJons/LinkVideo.Helper.Updates/main/update-manifest.json"
    appExeName   = "LinkVideo.Helper.exe"
)

type pendingRequest struct {
    Format      int    `json:"format"`
    FromVersion string `json:"from_version"`
    ToVersion   string `json:"to_version"`
    PatchFile   string `json:"patch_file"`
    SHA256      string `json:"sha256"`
}

type patchInfo struct {
    DownloadURL string `json:"download_url"`
    URL         string `json:"url"`
    SHA256      string `json:"sha256"`
}

type updateManifest struct {
    Version string               `json:"version"`
    Patches map[string]patchInfo `json:"patches"`
}

type updateResult struct {
    Status      string `json:"status"`
    FromVersion string `json:"from_version,omitempty"`
    ToVersion   string `json:"to_version,omitempty"`
    Error       string `json:"error,omitempty"`
    At          string `json:"at"`
}

func main() {
    if hasArg("--scheduled") {
        // Run the worker from a temporary copy. This releases the installed
        // updater EXE before the patch starts, so future patches can update the
        // updater itself without falling back to a full Setup.
        if err := launchTempWorker(); err != nil {
            _ = writeResult(updateResult{Status: "error", Error: err.Error(), At: time.Now().UTC().Format(time.RFC3339)})
            clearPending()
            os.Exit(1)
        }
        return
    }
    if !hasArg("--scheduled-worker") {
        return
    }
    defer scheduleTempSelfDelete()

    // The normal Helper close event has already been accepted when the task is
    // triggered. Give Qt a moment to release Program Files before patching.
    time.Sleep(1800 * time.Millisecond)
    if err := runScheduled(); err != nil {
        _ = writeResult(updateResult{Status: "error", Error: err.Error(), At: time.Now().UTC().Format(time.RFC3339)})
        clearPending()
        os.Exit(1)
    }
}

func launchTempWorker() error {
    self, err := os.Executable()
    if err != nil {
        return err
    }
    tempDir := filepath.Join(os.TempDir(), "LinkVideo.Helper-SilentUpdater")
    if err := os.MkdirAll(tempDir, 0o700); err != nil {
        return err
    }
    tempExe := filepath.Join(tempDir, fmt.Sprintf("Updater-%d.exe", os.Getpid()))
    data, err := os.ReadFile(self)
    if err != nil {
        return err
    }
    if err := os.WriteFile(tempExe, data, 0o700); err != nil {
        return err
    }
    cmd := exec.Command(tempExe, "--scheduled-worker")
    cmd.SysProcAttr = &syscall.SysProcAttr{
        HideWindow:    true,
        CreationFlags: 0x00000200 | 0x00000008, // CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
    }
    return cmd.Start()
}

func scheduleTempSelfDelete() {
    self, err := os.Executable()
    if err != nil {
        return
    }
    command := fmt.Sprintf(`ping 127.0.0.1 -n 3 >nul & del /f /q "%s"`, self)
    cmd := exec.Command("cmd.exe", "/C", command)
    cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x00000008}
    _ = cmd.Start()
}

func hasArg(wanted string) bool {
    for _, arg := range os.Args[1:] {
        if strings.EqualFold(strings.TrimSpace(arg), wanted) {
            return true
        }
    }
    return false
}

func programDataStateDir() string {
    root := strings.TrimSpace(os.Getenv("PROGRAMDATA"))
    if root == "" {
        root = `C:\ProgramData`
    }
    return filepath.Join(root, "LinkVideo.Helper", "Updates")
}

func installDir() string {
    root := strings.TrimSpace(os.Getenv("ProgramFiles"))
    if root == "" {
        root = `C:\Program Files`
    }
    return filepath.Join(root, "LinkVideo.Helper")
}

func runScheduled() error {
    stateDir := programDataStateDir()
    requestPath := filepath.Join(stateDir, "pending.json")
    raw, err := os.ReadFile(requestPath)
    if err != nil {
        return fmt.Errorf("pending request не найден: %w", err)
    }
    var req pendingRequest
    if err := json.Unmarshal(raw, &req); err != nil {
        return fmt.Errorf("pending request повреждён: %w", err)
    }
    if req.Format != 1 || !validVersion(req.FromVersion) || !validVersion(req.ToVersion) {
        return errors.New("pending request содержит некорректные версии")
    }
    if filepath.Base(req.PatchFile) != "pending-patch.exe" || req.PatchFile != filepath.Base(req.PatchFile) {
        return errors.New("pending request содержит недопустимый путь патча")
    }

    appPath := filepath.Join(installDir(), appExeName)
    installed, err := productVersion(appPath)
    if err != nil {
        return err
    }
    if !sameVersion(installed, req.FromVersion) {
        return fmt.Errorf("установлена версия %s, а патч подготовлен для %s", installed, req.FromVersion)
    }

    manifest, err := loadManifest()
    if err != nil {
        return err
    }
    if !sameVersion(manifest.Version, req.ToVersion) {
        return fmt.Errorf("официальный manifest предлагает %s вместо %s", manifest.Version, req.ToVersion)
    }

    official, ok := findPatch(manifest.Patches, installed)
    if !ok {
        return fmt.Errorf("в официальном manifest нет патча для %s", installed)
    }
    expectedHash := strings.ToLower(strings.TrimSpace(official.SHA256))
    if !validSHA256(expectedHash) {
        return errors.New("официальный manifest содержит некорректный SHA-256 патча")
    }
    if requestHash := strings.ToLower(strings.TrimSpace(req.SHA256)); requestHash != "" && requestHash != expectedHash {
        return errors.New("SHA-256 pending request не совпадает с официальным manifest")
    }

    patchPath := filepath.Join(stateDir, req.PatchFile)
    if info, err := os.Stat(patchPath); err != nil || info.IsDir() || info.Size() < 64*1024 {
        return errors.New("подготовленный patch EXE отсутствует или повреждён")
    }
    actualHash, err := sha256File(patchPath)
    if err != nil {
        return err
    }
    if actualHash != expectedHash {
        return errors.New("SHA-256 подготовленного патча не совпадает с официальным manifest")
    }
    file, err := os.Open(patchPath)
    if err != nil {
        return err
    }
    header := make([]byte, 2)
    _, readErr := io.ReadFull(file, header)
    file.Close()
    if readErr != nil || string(header) != "MZ" {
        return errors.New("подготовленный файл не является Windows EXE")
    }

    // Never execute directly from the user-writable staging directory. Copy to
    // Program Files and verify the official hash again there before CreateProcess.
    trustedPatch, cleanupTrustedPatch, err := prepareTrustedPatch(patchPath, expectedHash)
    if err != nil {
        return err
    }
    defer cleanupTrustedPatch()

    cmd := exec.Command(trustedPatch, "--silent")
    cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
    if out, err := cmd.CombinedOutput(); err != nil {
        detail := strings.TrimSpace(string(out))
        if detail != "" {
            return fmt.Errorf("patch завершился ошибкой: %w (%s)", err, detail)
        }
        return fmt.Errorf("patch завершился ошибкой: %w", err)
    }

    next, err := productVersion(appPath)
    if err != nil {
        return err
    }
    if !sameVersion(next, req.ToVersion) {
        return fmt.Errorf("после патча установлена версия %s вместо %s", next, req.ToVersion)
    }

    result := updateResult{
        Status:      "ok",
        FromVersion: req.FromVersion,
        ToVersion:   req.ToVersion,
        At:          time.Now().UTC().Format(time.RFC3339),
    }
    if err := writeResult(result); err != nil {
        return err
    }
    clearPending()
    return nil
}

func loadManifest() (updateManifest, error) {
    client := &http.Client{Timeout: 20 * time.Second}
    request, err := http.NewRequest(http.MethodGet, manifestURL, nil)
    if err != nil {
        return updateManifest{}, err
    }
    request.Header.Set("User-Agent", "LinkVideo.Helper.SilentUpdater")
    request.Header.Set("Cache-Control", "no-cache")
    response, err := client.Do(request)
    if err != nil {
        return updateManifest{}, fmt.Errorf("не удалось получить официальный manifest: %w", err)
    }
    defer response.Body.Close()
    if response.StatusCode < 200 || response.StatusCode >= 300 {
        return updateManifest{}, fmt.Errorf("официальный manifest вернул HTTP %d", response.StatusCode)
    }
    var manifest updateManifest
    decoder := json.NewDecoder(io.LimitReader(response.Body, 1024*1024))
    if err := decoder.Decode(&manifest); err != nil {
        return updateManifest{}, fmt.Errorf("официальный manifest повреждён: %w", err)
    }
    if !validVersion(manifest.Version) {
        return updateManifest{}, errors.New("официальный manifest не содержит корректную версию")
    }
    return manifest, nil
}

func findPatch(patches map[string]patchInfo, installed string) (patchInfo, bool) {
    for fromVersion, patch := range patches {
        if sameVersion(fromVersion, installed) {
            if strings.TrimSpace(patch.DownloadURL) == "" && strings.TrimSpace(patch.URL) == "" {
                return patchInfo{}, false
            }
            return patch, true
        }
    }
    return patchInfo{}, false
}

func validVersion(value string) bool {
    return regexp.MustCompile(`^\d+(?:\.\d+){1,3}$`).MatchString(strings.TrimSpace(value))
}

func versionTuple(value string) []int {
    match := regexp.MustCompile(`\d+(?:\.\d+){0,3}`).FindString(value)
    if match == "" {
        return nil
    }
    parts := strings.Split(match, ".")
    out := make([]int, 0, len(parts))
    for _, part := range parts {
        n, _ := strconv.Atoi(part)
        out = append(out, n)
    }
    for len(out) > 1 && out[len(out)-1] == 0 {
        out = out[:len(out)-1]
    }
    return out
}

func sameVersion(a, b string) bool {
    x, y := versionTuple(a), versionTuple(b)
    if len(x) == 0 || len(x) != len(y) {
        return false
    }
    for i := range x {
        if x[i] != y[i] {
            return false
        }
    }
    return true
}

func validSHA256(value string) bool {
    return regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(value)
}

func sha256File(path string) (string, error) {
    file, err := os.Open(path)
    if err != nil {
        return "", err
    }
    defer file.Close()
    digest := sha256.New()
    if _, err := io.Copy(digest, file); err != nil {
        return "", err
    }
    return hex.EncodeToString(digest.Sum(nil)), nil
}

func productVersion(path string) (string, error) {
    if _, err := os.Stat(path); err != nil {
        return "", fmt.Errorf("не найден установленный %s", filepath.Base(path))
    }
    script := `$ErrorActionPreference='Stop';[Console]::Out.Write([string](Get-Item -LiteralPath $args[0]).VersionInfo.ProductVersion)`
    cmd := exec.Command(
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-Command", script, path,
    )
    cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
    out, err := cmd.CombinedOutput()
    if err != nil {
        return "", fmt.Errorf("не удалось определить установленную версию: %s", strings.TrimSpace(string(out)))
    }
    value := strings.TrimSpace(strings.ReplaceAll(string(out), "\x00", ""))
    if value == "" {
        return "", errors.New("у установленного EXE отсутствует ProductVersion")
    }
    return value, nil
}

func writeResult(result updateResult) error {
    root := programDataStateDir()
    if err := os.MkdirAll(root, 0o755); err != nil {
        return err
    }
    data, err := json.MarshalIndent(result, "", "  ")
    if err != nil {
        return err
    }
    data = append(data, '\n')
    temp := filepath.Join(root, "last-result.json.new")
    final := filepath.Join(root, "last-result.json")
    if err := os.WriteFile(temp, data, 0o644); err != nil {
        return err
    }
    _ = os.Remove(final)
    return os.Rename(temp, final)
}

func clearPending() {
    root := programDataStateDir()
    _ = os.Remove(filepath.Join(root, "pending.json"))
    _ = os.Remove(filepath.Join(root, "pending.json.new"))
    _ = os.Remove(filepath.Join(root, "pending-patch.exe"))
    _ = os.Remove(filepath.Join(root, "pending-patch.exe.new"))
}
