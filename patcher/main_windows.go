//go:build windows

package main

import (
    "archive/zip"
    "bytes"
    "context"
    "crypto/sha256"
    "encoding/hex"
    "encoding/json"
    "errors"
    "fmt"
    "io"
    "os"
    "os/exec"
    "path/filepath"
    "regexp"
    "sort"
    "strconv"
    "strings"
    "syscall"
    "time"
    "unsafe"
)

const (
    createNoWindowFlag       = 0x08000000
    productVersionPathEnvKey = "LINKVIDEO_PRODUCT_VERSION_FILE"
)

var errElevationDelegated = errors.New("применение патча передано процессу с правами администратора")

type changedFile struct {
    SHA256 string `json:"sha256"`
    Size   int64  `json:"size"`
}

type manifest struct {
    Format        int                    `json:"format"`
    FromVersion   string                 `json:"from_version"`
    ToVersion     string                 `json:"to_version"`
    Changed       map[string]changedFile `json:"changed"`
    Deleted       []string               `json:"deleted"`
    PayloadSHA256 string                 `json:"payload_sha256"`
}

func main() {
    if err := applyPatch(); err != nil {
        if errors.Is(err, errElevationDelegated) {
            return
        }
        messageBox("Обновление LinkVideo.Helper", "Не удалось применить патч:\n\n"+err.Error(), 0x10)
        os.Exit(1)
    }
    messageBox("Обновление LinkVideo.Helper", "Обновление установлено. LinkVideo.Helper будет запущен снова.", 0x40)
}

func applyPatch() error {
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
    actualPayloadHash := sha256Bytes(patchPayload)
    if !strings.EqualFold(actualPayloadHash, m.PayloadSHA256) {
        return errors.New("SHA-256 встроенного payload не совпадает с manifest")
    }

    elevated, err := ensureElevated()
    if err != nil {
        return err
    }
    if !elevated {
        // The elevated child owns the result UI. The non-elevated bootstrap
        // must not report a false success before that child has applied the
        // patch.
        return errElevationDelegated
    }

    installDir := defaultInstallDir()
    appPath := filepath.Join(installDir, "LinkVideo.Helper.exe")
    installedVersion, err := productVersion(appPath)
    if err != nil {
        return err
    }
    if !sameVersion(installedVersion, m.FromVersion) {
        return fmt.Errorf("патч предназначен для %s, но установлена %s. Используйте полный установщик", m.FromVersion, installedVersion)
    }

    stopHelper()

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

    if nextVersion, err := productVersion(appPath); err != nil || !sameVersion(nextVersion, m.ToVersion) {
        if err != nil {
            return rollbackAfterFailure(
                fmt.Errorf("после патча не удалось прочитать версию приложения: %w", err),
                installDir, backupRoot, existingBefore, affected,
            )
        }
        return rollbackAfterFailure(
            fmt.Errorf("после патча приложение сообщает версию %s вместо %s", nextVersion, m.ToVersion),
            installDir, backupRoot, existingBefore, affected,
        )
    }

    // Update Add/Remove Programs only after the installed executable has
    // proved its ProductVersion. Otherwise a failed patch could leave Windows
    // claiming the new version while the files were rolled back to the old one.
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

    cmd := exec.Command("explorer.exe", appPath)
    cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
    _ = cmd.Start()
    return nil
}

func defaultInstallDir() string {
    if p := strings.TrimSpace(os.Getenv("ProgramFiles")); p != "" {
        return filepath.Join(p, "LinkVideo.Helper")
    }
    return `C:\Program Files\LinkVideo.Helper`
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
    for _, p := range parts {
        n, _ := strconv.Atoi(p)
        out = append(out, n)
    }
    for len(out) > 1 && out[len(out)-1] == 0 {
        out = out[:len(out)-1]
    }
    return out
}

func sameVersion(a, b string) bool {
    x, y := versionTuple(a), versionTuple(b)
    if len(x) != len(y) {
        return false
    }
    for i := range x {
        if x[i] != y[i] {
            return false
        }
    }
    return len(x) > 0
}

func sha256Bytes(data []byte) string {
    sum := sha256.Sum256(data)
    return hex.EncodeToString(sum[:])
}

func sha256File(path string) (string, error) {
    f, err := os.Open(path)
    if err != nil {
        return "", err
    }
    defer f.Close()
    h := sha256.New()
    if _, err := io.Copy(h, f); err != nil {
        return "", err
    }
    return hex.EncodeToString(h.Sum(nil)), nil
}

func safeRelative(name string) (string, error) {
    clean := filepath.Clean(filepath.FromSlash(strings.ReplaceAll(name, "\\", "/")))
    if clean == "." || filepath.IsAbs(clean) || clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
        return "", fmt.Errorf("недопустимый путь патча: %s", name)
    }
    return filepath.ToSlash(clean), nil
}

func applyChangedFiles(installDir string, m manifest) error {
    zr, err := zip.NewReader(bytes.NewReader(patchPayload), int64(len(patchPayload)))
    if err != nil {
        return fmt.Errorf("payload ZIP повреждён: %w", err)
    }
    entries := make(map[string]*zip.File)
    for _, zf := range zr.File {
        if zf.FileInfo().IsDir() {
            continue
        }
        name, err := safeRelative(zf.Name)
        if err != nil {
            return err
        }
        entries[name] = zf
    }
    if len(entries) != len(m.Changed) {
        return errors.New("набор файлов payload не совпадает с manifest")
    }
    names := make([]string, 0, len(m.Changed))
    for name := range m.Changed {
        names = append(names, name)
    }
    sort.Strings(names)
    for _, rawName := range names {
        name, err := safeRelative(rawName)
        if err != nil {
            return err
        }
        meta := m.Changed[rawName]
        zf := entries[name]
        if zf == nil {
            return fmt.Errorf("в payload отсутствует %s", name)
        }
        r, err := zf.Open()
        if err != nil {
            return err
        }
        target := filepath.Join(installDir, filepath.FromSlash(name))
        if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
            r.Close()
            return err
        }
        temp := target + ".patch-new"
        w, err := os.OpenFile(temp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o755)
        if err != nil {
            r.Close()
            return err
        }
        _, copyErr := io.Copy(w, r)
        closeErr := w.Close()
        r.Close()
        if copyErr != nil {
            _ = os.Remove(temp)
            return copyErr
        }
        if closeErr != nil {
            _ = os.Remove(temp)
            return closeErr
        }
        digest, err := sha256File(temp)
        if err != nil || !strings.EqualFold(digest, meta.SHA256) {
            _ = os.Remove(temp)
            return fmt.Errorf("SHA-256 файла %s не совпадает с manifest", name)
        }
        _ = os.Remove(target)
        if err := os.Rename(temp, target); err != nil {
            _ = os.Remove(temp)
            return fmt.Errorf("не удалось заменить %s: %w", name, err)
        }
    }
    return nil
}

func applyDeletes(installDir string, deleted []string) error {
    for _, rawName := range deleted {
        name, err := safeRelative(rawName)
        if err != nil {
            return err
        }
        target := filepath.Join(installDir, filepath.FromSlash(name))
        if err := os.Remove(target); err != nil && !os.IsNotExist(err) {
            return fmt.Errorf("не удалось удалить устаревший файл %s: %w", name, err)
        }
    }
    return nil
}

func verifyChangedFiles(installDir string, changed map[string]changedFile) error {
    for rawName, meta := range changed {
        name, err := safeRelative(rawName)
        if err != nil {
            return err
        }
        digest, err := sha256File(filepath.Join(installDir, filepath.FromSlash(name)))
        if err != nil {
            return err
        }
        if !strings.EqualFold(digest, meta.SHA256) {
            return fmt.Errorf("контрольная проверка %s не пройдена", name)
        }
    }
    return nil
}

func rollback(installDir, backupRoot string, existingBefore map[string]bool, affected map[string]struct{}) error {
    var first error
    for name := range affected {
        target := filepath.Join(installDir, filepath.FromSlash(name))
        if existingBefore[name] {
            backup := filepath.Join(backupRoot, filepath.FromSlash(name))
            if err := copyFile(backup, target); err != nil && first == nil {
                first = err
            }
        } else {
            if err := os.Remove(target); err != nil && !os.IsNotExist(err) && first == nil {
                first = err
            }
        }
    }
    return first
}

func rollbackAfterFailure(cause error, installDir, backupRoot string, existingBefore map[string]bool, affected map[string]struct{}) error {
    if rollbackErr := rollback(installDir, backupRoot, existingBefore, affected); rollbackErr != nil {
        return fmt.Errorf("%v; откат предыдущей версии также не удался: %w", cause, rollbackErr)
    }
    return fmt.Errorf("%w; предыдущая версия восстановлена", cause)
}

func copyFile(src, dst string) error {
    in, err := os.Open(src)
    if err != nil {
        return err
    }
    defer in.Close()
    if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
        return err
    }
    out, err := os.OpenFile(dst, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o755)
    if err != nil {
        return err
    }
    _, copyErr := io.Copy(out, in)
    closeErr := out.Close()
    if copyErr != nil {
        return copyErr
    }
    return closeErr
}

func stopHelper() {
    for _, name := range []string{"LinkVideo.Helper.exe", "LinkVideo VPN Helper.exe", "updater.exe"} {
        _ = runHidden("taskkill.exe", "/IM", name, "/T", "/F")
    }
    time.Sleep(600 * time.Millisecond)
}

func productVersion(path string) (string, error) {
    if _, err := os.Stat(path); err != nil {
        return "", fmt.Errorf("не найден установленный %s", filepath.Base(path))
    }
    script := `$ErrorActionPreference='Stop';$p=[Environment]::GetEnvironmentVariable('LINKVIDEO_PRODUCT_VERSION_FILE','Process');if([string]::IsNullOrWhiteSpace($p)){throw 'version file path is empty'};[Console]::Out.Write([string](Get-Item -LiteralPath $p).VersionInfo.ProductVersion)`
    ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
    defer cancel()
    cmd := exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script)
    cmd.Env = append(os.Environ(), productVersionPathEnvKey+"="+path)
    cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindowFlag}
    out, err := cmd.CombinedOutput()
    if errors.Is(ctx.Err(), context.DeadlineExceeded) {
        return "", errors.New("Windows не ответила при проверке ProductVersion за 8 секунд")
    }
    if err != nil {
        return "", fmt.Errorf("не удалось определить установленную версию: %s", strings.TrimSpace(string(out)))
    }
    value := strings.TrimSpace(strings.ReplaceAll(string(out), "\x00", ""))
    if value == "" {
        return "", errors.New("у установленного EXE отсутствует ProductVersion")
    }
    return value, nil
}

func runHidden(name string, args ...string) error {
    ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
    defer cancel()
    cmd := exec.CommandContext(ctx, name, args...)
    cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindowFlag}
    err := cmd.Run()
    if errors.Is(ctx.Err(), context.DeadlineExceeded) {
        return fmt.Errorf("%s не ответил за 8 секунд", name)
    }
    return err
}

type shellExecuteInfoW struct {
    CBSize       uint32
    FMask        uint32
    Hwnd         uintptr
    LpVerb       *uint16
    LpFile       *uint16
    LpParameters *uint16
    LpDirectory  *uint16
    NShow        int32
    HInstApp     uintptr
    LpIDList     uintptr
    LpClass      *uint16
    HkeyClass    uintptr
    DwHotKey     uint32
    HIcon        uintptr
    HProcess     uintptr
}

func ensureElevated() (bool, error) {
    shell32 := syscall.NewLazyDLL("shell32.dll")
    admin, _, _ := shell32.NewProc("IsUserAnAdmin").Call()
    if admin != 0 {
        return true, nil
    }
    self, err := os.Executable()
    if err != nil {
        return false, err
    }
    verb, _ := syscall.UTF16PtrFromString("runas")
    file, _ := syscall.UTF16PtrFromString(self)
    var parameters *uint16
    if patchHasArg("--silent") {
        // Only forward the one supported flag; never relay arbitrary command
        // line text into an elevated process.
        parameters, _ = syscall.UTF16PtrFromString("--silent")
    }
    info := shellExecuteInfoW{
        CBSize:       uint32(unsafe.Sizeof(shellExecuteInfoW{})),
        FMask:        0x40,
        LpVerb:       verb,
        LpFile:       file,
        LpParameters: parameters,
        NShow:        1,
    }
    ok, _, callErr := shell32.NewProc("ShellExecuteExW").Call(uintptr(unsafe.Pointer(&info)))
    if ok == 0 {
        return false, fmt.Errorf("повышение прав отменено: %v", callErr)
    }
    if info.HProcess != 0 {
        syscall.NewLazyDLL("kernel32.dll").NewProc("CloseHandle").Call(info.HProcess)
    }
    return false, nil
}

func messageBox(title, text string, flags uintptr) {
    user32 := syscall.NewLazyDLL("user32.dll")
    t, _ := syscall.UTF16PtrFromString(title)
    s, _ := syscall.UTF16PtrFromString(text)
    user32.NewProc("MessageBoxW").Call(0, uintptr(unsafe.Pointer(s)), uintptr(unsafe.Pointer(t)), flags)
}
