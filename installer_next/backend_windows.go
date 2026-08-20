//go:build windows

package main

import (
    "archive/zip"
    "bytes"
    "context"
    "encoding/base64"
    "encoding/binary"
    "errors"
    "fmt"
    "io"
    "os"
    "os/exec"
    "path/filepath"
    "strings"
    "syscall"
    "time"
    utf16pkg "unicode/utf16"
    "unsafe"
)

const (
    productName  = "LinkVideo.Helper"
    appExeName   = "LinkVideo.Helper.exe"
    appDirName   = "LinkVideo.Helper"
    uninstallKey = `HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\LinkVideo.Helper`
    legacyInnoKey = `HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\{8D39F3B2-8D87-4D9F-B5F6-2D7B65F08C21}_is1`
    legacyInnoWowKey = `HKLM\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{8D39F3B2-8D87-4D9F-B5F6-2D7B65F08C21}_is1`
    createNoWindowFlag = 0x08000000
    moveFileDelayUntilReboot = 0x00000004
    defaultHelperProcessTimeout = 12 * time.Second
)

var version = "0.0.0-dev"
var buildMode = "installer"

type installOptions struct {
    DesktopShortcut bool
}

type progressFunc func(percent int, status string)

func defaultInstallDir() string {
    if p := strings.TrimSpace(os.Getenv("ProgramFiles")); p != "" {
        return filepath.Join(p, appDirName)
    }
    return `C:\Program Files\LinkVideo.Helper`
}

func isProcessElevated() bool {
    shell32 := syscall.NewLazyDLL("shell32.dll")
    ok, _, _ := shell32.NewProc("IsUserAnAdmin").Call()
    return ok != 0
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

func shellExecuteRunAs(path string, args []string) error {
    verb, _ := syscall.UTF16PtrFromString("runas")
    file, _ := syscall.UTF16PtrFromString(path)
    params, _ := syscall.UTF16PtrFromString(strings.Join(args, " "))
    shell32 := syscall.NewLazyDLL("shell32.dll")
    info := shellExecuteInfoW{
        CBSize:       uint32(unsafe.Sizeof(shellExecuteInfoW{})),
        FMask:        0x00000040,
        LpVerb:       verb,
        LpFile:       file,
        LpParameters: params,
        NShow:        1,
    }
    ok, _, callErr := shell32.NewProc("ShellExecuteExW").Call(uintptr(unsafe.Pointer(&info)))
    if ok == 0 {
        return fmt.Errorf("Windows не разрешила получить права администратора: %v", callErr)
    }
    if info.HProcess != 0 {
        syscall.NewLazyDLL("kernel32.dll").NewProc("CloseHandle").Call(info.HProcess)
    }
    return nil
}

func ensureElevated() (bool, error) {
    if isProcessElevated() {
        return true, nil
    }
    self, err := os.Executable()
    if err != nil {
        return false, err
    }
    if err := shellExecuteRunAs(self, os.Args[1:]); err != nil {
        return false, err
    }
    return false, nil
}

// The installed uninstaller cannot delete its own Program Files directory while
// it is still running. Always move execution to a private temp copy first.
func ensureUninstallerRunsFromTemp() (bool, error) {
    self, err := os.Executable()
    if err != nil {
        return false, err
    }
    installDir := filepath.Clean(defaultInstallDir())
    selfDir := filepath.Clean(filepath.Dir(self))
    if !strings.EqualFold(selfDir, installDir) {
        return true, nil
    }
    tempDir := filepath.Join(os.TempDir(), "LinkVideo.Helper-Uninstall")
    if err := os.MkdirAll(tempDir, 0o755); err != nil {
        return false, err
    }
    tempExe := filepath.Join(tempDir, fmt.Sprintf("Uninstall-%d.exe", os.Getpid()))
    data, err := os.ReadFile(self)
    if err != nil {
        return false, err
    }
    if err := os.WriteFile(tempExe, data, 0o700); err != nil {
        return false, err
    }
    if err := shellExecuteRunAs(tempExe, os.Args[1:]); err != nil {
        _ = os.Remove(tempExe)
        return false, err
    }
    return false, nil
}

// Every external Windows helper is deadline-bounded. A broken Scheduled Tasks
// service, WMI/PowerShell host, registry helper or taskkill must never keep the
// installer/uninstaller frozen indefinitely.
func runHiddenTimeout(timeout time.Duration, name string, args ...string) error {
    if timeout <= 0 {
        timeout = defaultHelperProcessTimeout
    }
    ctx, cancel := context.WithTimeout(context.Background(), timeout)
    defer cancel()

    cmd := exec.CommandContext(ctx, name, args...)
    cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindowFlag}
    out, err := cmd.CombinedOutput()
    if errors.Is(ctx.Err(), context.DeadlineExceeded) {
        return fmt.Errorf("%s не ответил за %s", name, timeout.Round(time.Second))
    }
    if err != nil {
        return fmt.Errorf("%s: %w (%s)", name, err, strings.TrimSpace(string(out)))
    }
    return nil
}

func runHidden(name string, args ...string) error {
    return runHiddenTimeout(defaultHelperProcessTimeout, name, args...)
}

func runCleanup(name string, args ...string) {
    _ = runHiddenTimeout(6*time.Second, name, args...)
}

func stopHelperProcesses() {
    for _, image := range []string{appExeName, "LinkVideo VPN Helper.exe", "updater.exe", silentUpdaterExeName} {
        runCleanup("taskkill.exe", "/IM", image, "/T", "/F")
    }
    time.Sleep(450 * time.Millisecond)
}

// A full installer is an authoritative runtime snapshot, not an overlay.  Old
// files that disappeared from the new payload must not survive an upgrade: a
// stale Python module, DLL or the temporary 3.0.10 bundled FFmpeg can otherwise
// silently override the new release.  User data and the on-demand FFmpeg cache
// live under LocalAppData and are intentionally not touched here.
func cleanRuntimeBeforeInstall(dest string) error {
    for _, name := range []string{"_internal", "tools", "linkvideo_vpn_helper", "scripts"} {
        target := filepath.Join(dest, name)
        if err := os.RemoveAll(target); err != nil {
            return fmt.Errorf("не удалось удалить старый каталог %s: %w", name, err)
        }
    }

    for _, name := range []string{
        appExeName,
        "LinkVideo.Helper.Updater.exe",
        "LinkVideo VPN Helper.exe",
        "updater.exe",
        "Uninstall.exe",
    } {
        target := filepath.Join(dest, name)
        if err := os.Remove(target); err != nil && !errors.Is(err, os.ErrNotExist) {
            return fmt.Errorf("не удалось удалить старый файл %s: %w", name, err)
        }
    }

    for _, pattern := range []string{"*.py", "*.pyc", "*.new"} {
        matches, _ := filepath.Glob(filepath.Join(dest, pattern))
        for _, target := range matches {
            if err := os.Remove(target); err != nil && !errors.Is(err, os.ErrNotExist) {
                return fmt.Errorf("не удалось удалить старый файл %s: %w", filepath.Base(target), err)
            }
        }
    }
    return nil
}

func extractPayload(dest string, progress progressFunc) error {
    if len(payload) == 0 {
        return errors.New("установочный пакет не содержит payload")
    }
    zr, err := zip.NewReader(bytes.NewReader(payload), int64(len(payload)))
    if err != nil {
        return fmt.Errorf("повреждён встроенный пакет: %w", err)
    }
    files := make([]*zip.File, 0, len(zr.File))
    for _, f := range zr.File {
        if !f.FileInfo().IsDir() {
            files = append(files, f)
        }
    }
    done := 0
    for _, f := range zr.File {
        clean := filepath.Clean(f.Name)
        if clean == "." || filepath.IsAbs(clean) || clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
            return fmt.Errorf("недопустимый путь внутри пакета: %s", f.Name)
        }
        target := filepath.Join(dest, clean)
        if f.FileInfo().IsDir() {
            if err := os.MkdirAll(target, 0o755); err != nil {
                return err
            }
            continue
        }
        if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
            return err
        }
        r, err := f.Open()
        if err != nil {
            return err
        }
        tmp := target + ".new"
        w, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o755)
        if err != nil {
            r.Close()
            return err
        }
        _, copyErr := io.Copy(w, r)
        closeErr := w.Close()
        r.Close()
        if copyErr != nil {
            _ = os.Remove(tmp)
            return copyErr
        }
        if closeErr != nil {
            _ = os.Remove(tmp)
            return closeErr
        }
        _ = os.Remove(target)
        if err := os.Rename(tmp, target); err != nil {
            _ = os.Remove(tmp)
            return fmt.Errorf("не удалось заменить %s: %w", filepath.Base(target), err)
        }
        done++
        if progress != nil {
            pct := 20
            if len(files) > 0 {
                pct += done * 52 / len(files)
            }
            progress(pct, "Копирование: "+filepath.Base(clean))
        }
    }
    return nil
}

func psEscape(value string) string {
    return strings.ReplaceAll(value, "'", "''")
}

func runPowerShell(script string) error {
    runes := utf16pkg.Encode([]rune(script))
    buf := make([]byte, len(runes)*2)
    for i, r := range runes {
        binary.LittleEndian.PutUint16(buf[i*2:], r)
    }
    encoded := base64.StdEncoding.EncodeToString(buf)
    return runHiddenTimeout(12*time.Second, "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded)
}

func createShortcuts(appPath, dest string, desktop bool) error {
    public := strings.TrimSpace(os.Getenv("PUBLIC"))
    programData := strings.TrimSpace(os.Getenv("PROGRAMDATA"))
    if public == "" || programData == "" {
        return errors.New("Windows не вернула пути PUBLIC/PROGRAMDATA")
    }
    desktopPath := filepath.Join(public, "Desktop", "LinkVideo.Helper.lnk")
    menuDir := filepath.Join(programData, `Microsoft\Windows\Start Menu\Programs\LinkVideo.Helper`)
    menuPath := filepath.Join(menuDir, "LinkVideo.Helper.lnk")
    desktopFlag := "$false"
    if desktop {
        desktopFlag = "$true"
    }
    script := fmt.Sprintf(`$ErrorActionPreference='Stop';`+
        `$w=New-Object -ComObject WScript.Shell;`+
        `$menu='%s';New-Item -ItemType Directory -Force -Path $menu|Out-Null;`+
        `$targets=@('%s');if(%s){$targets+=@('%s')};`+
        `foreach($p in $targets){$s=$w.CreateShortcut($p);$s.TargetPath='%s';$s.WorkingDirectory='%s';$s.IconLocation='%s,0';$s.Description='LinkVideo.Helper';$s.Save()}`+
        `if(-not %s){Remove-Item -Force -ErrorAction SilentlyContinue '%s'}`,
        psEscape(menuDir), psEscape(menuPath), desktopFlag, psEscape(desktopPath),
        psEscape(appPath), psEscape(dest), psEscape(appPath), desktopFlag, psEscape(desktopPath))
    return runPowerShell(script)
}

func removeLegacyInstallArtifacts(dest string) {
    for _, key := range []string{legacyInnoKey, legacyInnoWowKey, `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\LinkVideo.Helper`} {
        runCleanup("reg.exe", "delete", key, "/f")
    }
    for _, name := range []string{"unins000.exe", "unins000.dat", "unins001.exe", "unins001.dat"} {
        _ = os.Remove(filepath.Join(dest, name))
    }
    public := strings.TrimSpace(os.Getenv("PUBLIC"))
    programData := strings.TrimSpace(os.Getenv("PROGRAMDATA"))
    if public != "" {
        _ = os.Remove(filepath.Join(public, "Desktop", "LinkVideo VPN Helper.lnk"))
    }
    if programData != "" {
        _ = os.Remove(filepath.Join(programData, `Microsoft\Windows\Start Menu\Programs\LinkVideo.Helper`, "LinkVideo VPN Helper.lnk"))
    }
}

func registerUninstall(appPath, dest string) error {
    uninstall := filepath.Join(dest, "Uninstall.exe")
    values := [][]string{
        {"/v", "DisplayName", "/t", "REG_SZ", "/d", productName},
        {"/v", "DisplayVersion", "/t", "REG_SZ", "/d", version},
        {"/v", "Publisher", "/t", "REG_SZ", "/d", "LinkVideo"},
        {"/v", "InstallLocation", "/t", "REG_SZ", "/d", dest},
        {"/v", "DisplayIcon", "/t", "REG_SZ", "/d", appPath + ",0"},
        {"/v", "UninstallString", "/t", "REG_SZ", "/d", `"` + uninstall + `"`},
        {"/v", "QuietUninstallString", "/t", "REG_SZ", "/d", `"` + uninstall + `" --quiet`},
        {"/v", "NoModify", "/t", "REG_DWORD", "/d", "1"},
        {"/v", "NoRepair", "/t", "REG_DWORD", "/d", "1"},
    }
    runCleanup("reg.exe", "delete", uninstallKey, "/f")
    for _, args := range values {
        full := append([]string{"add", uninstallKey}, args...)
        full = append(full, "/f")
        if err := runHiddenTimeout(8*time.Second, "reg.exe", full...); err != nil {
            return err
        }
    }
    return nil
}

func installProduct(opts installOptions, progress progressFunc) (string, error) {
    dest := defaultInstallDir()
    appPath := filepath.Join(dest, appExeName)
    progress(5, "Подготовка обновления…")
    stopHelperProcesses()
    if err := os.MkdirAll(dest, 0o755); err != nil {
        return "", fmt.Errorf("не удалось создать папку установки: %w", err)
    }
    progress(12, "Удаление файлов предыдущей версии…")
    if err := cleanRuntimeBeforeInstall(dest); err != nil {
        return "", err
    }
    progress(14, "Обновление файлов программы…")
    if err := extractPayload(dest, progress); err != nil {
        return "", err
    }
    if _, err := os.Stat(appPath); err != nil {
        return "", fmt.Errorf("после распаковки не найден %s", appExeName)
    }
    progress(76, "Перенос регистрации предыдущей версии…")
    removeLegacyInstallArtifacts(dest)
    progress(82, "Создание ярлыков…")
    if err := createShortcuts(appPath, dest, opts.DesktopShortcut); err != nil {
        return "", fmt.Errorf("не удалось создать ярлыки: %w", err)
    }
    progress(90, "Регистрация в Windows…")
    if err := registerUninstall(appPath, dest); err != nil {
        return "", fmt.Errorf("не удалось зарегистрировать удаление: %w", err)
    }

    // Silent differential patches are an update convenience, not a prerequisite
    // for using Helper. Corporate policy, a disabled Task Scheduler service or
    // a broken Windows component must not roll back/brick an otherwise complete
    // application installation. When provisioning fails, Helper simply falls
    // back to its visible full-Setup update path.
    progress(95, "Настройка фоновых патчей…")
    var silentErr error
    if err := registerSilentUpdateTask(dest); err != nil {
        silentErr = err
    } else if err := verifySilentUpdateTask(dest); err != nil {
        silentErr = err
        removeSilentUpdateTask()
    }
    recordSilentUpdateWarning(silentErr)

    if silentErr != nil {
        progress(99, "Программа установлена · фоновые патчи недоступны, будет использоваться обычное обновление")
    }
    progress(100, "LinkVideo.Helper установлен")
    return appPath, nil
}

func removeShortcuts() {
    public := strings.TrimSpace(os.Getenv("PUBLIC"))
    programData := strings.TrimSpace(os.Getenv("PROGRAMDATA"))
    if public != "" {
        _ = os.Remove(filepath.Join(public, "Desktop", "LinkVideo.Helper.lnk"))
        _ = os.Remove(filepath.Join(public, "Desktop", "LinkVideo VPN Helper.lnk"))
    }
    if programData != "" {
        _ = os.RemoveAll(filepath.Join(programData, `Microsoft\Windows\Start Menu\Programs\LinkVideo.Helper`))
    }
}

func removeUserData() {
    // QSettings("LinkVideo", "LinkVideo.Helper") on Windows uses HKCU.
    runCleanup("reg.exe", "delete", `HKCU\Software\LinkVideo\LinkVideo.Helper`, "/f")
    runCleanup("reg.exe", "delete", `HKCU\Software\LinkVideo\VPNHelper`, "/f")
    for _, envName := range []string{"LOCALAPPDATA", "APPDATA"} {
        base := strings.TrimSpace(os.Getenv(envName))
        if base == "" {
            continue
        }
        root := filepath.Join(base, "LinkVideo.Helper")
        _ = os.RemoveAll(root)
    }
}

func uninstallProduct(removeData bool, progress progressFunc) error {
    dest := defaultInstallDir()
    progress(8, "Остановка LinkVideo.Helper…")
    stopHelperProcesses()
    progress(18, "Удаление фонового updater…")
    removeSilentUpdateTask()
    progress(25, "Удаление регистрации Windows…")
    for _, key := range []string{uninstallKey, legacyInnoKey, legacyInnoWowKey} {
        runCleanup("reg.exe", "delete", key, "/f")
    }
    progress(38, "Удаление ярлыков…")
    removeShortcuts()
    if removeData {
        progress(50, "Удаление настроек и кэша…")
        removeUserData()
    }
    progress(62, "Удаление файлов программы…")
    self, _ := os.Executable()
    entries, _ := os.ReadDir(dest)
    for _, entry := range entries {
        target := filepath.Join(dest, entry.Name())
        if strings.EqualFold(filepath.Clean(target), filepath.Clean(self)) {
            continue
        }
        _ = os.RemoveAll(target)
    }
    progress(100, "LinkVideo.Helper удалён")
    return nil
}

func scheduleSelfDelete() {
    self, err := os.Executable()
    if err != nil {
        return
    }
    // Program Files is no longer occupied because the uninstaller is running
    // from its bootstrap copy. Remove the now-empty product directory directly.
    _ = os.RemoveAll(defaultInstallDir())

    // Windows cannot delete the currently executing bootstrap EXE. Mark it for
    // deletion at reboot with MoveFileExW instead of starting cmd.exe/ping/del.
    path, err := syscall.UTF16PtrFromString(self)
    if err != nil {
        return
    }
    kernel32 := syscall.NewLazyDLL("kernel32.dll")
    moveFileEx := kernel32.NewProc("MoveFileExW")
    moveFileEx.Call(
        uintptr(unsafe.Pointer(path)),
        0,
        uintptr(moveFileDelayUntilReboot),
    )
}

func launchApplication(appPath string) error {
    cmd := exec.Command("explorer.exe", appPath)
    cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
    return cmd.Start()
}
