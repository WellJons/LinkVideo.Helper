//go:build windows

package main

import (
    "archive/zip"
    "bytes"
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
    productName = "LinkVideo.Helper"
    appExeName  = "LinkVideo.Helper.exe"
    appDirName  = "LinkVideo.Helper"
    uninstallKey = `HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\LinkVideo.Helper`
    legacyInnoKey = `HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\{8D39F3B2-8D87-4D9F-B5F6-2D7B65F08C21}_is1`
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

func ensureElevated() (bool, error) {
    if isProcessElevated() {
        return true, nil
    }
    self, err := os.Executable()
    if err != nil {
        return false, err
    }
    verb, _ := syscall.UTF16PtrFromString("runas")
    file, _ := syscall.UTF16PtrFromString(self)
    params, _ := syscall.UTF16PtrFromString(strings.Join(os.Args[1:], " "))
    shell32 := syscall.NewLazyDLL("shell32.dll")
    proc := shell32.NewProc("ShellExecuteExW")
    info := shellExecuteInfoW{
        CBSize: uint32(unsafe.Sizeof(shellExecuteInfoW{})),
        FMask:  0x00000040,
        LpVerb: verb,
        LpFile: file,
        LpParameters: params,
        NShow: 1,
    }
    ok, _, callErr := proc.Call(uintptr(unsafe.Pointer(&info)))
    if ok == 0 {
        return false, fmt.Errorf("Windows не разрешила получить права администратора: %v", callErr)
    }
    return false, nil
}

func runHidden(name string, args ...string) error {
    cmd := exec.Command(name, args...)
    cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
    if out, err := cmd.CombinedOutput(); err != nil {
        return fmt.Errorf("%s: %w (%s)", name, err, strings.TrimSpace(string(out)))
    }
    return nil
}

func runCleanup(name string, args ...string) {
    _ = runHidden(name, args...)
}

func stopHelperProcesses() {
    for _, image := range []string{appExeName, "LinkVideo VPN Helper.exe", "updater.exe"} {
        runCleanup("taskkill.exe", "/IM", image, "/T", "/F")
    }
    time.Sleep(450 * time.Millisecond)
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
        if clean == "." || filepath.IsAbs(clean) || strings.HasPrefix(clean, "..") {
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

func shortcutScript(appPath, dest string, desktop bool) string {
    public := os.Getenv("PUBLIC")
    programData := os.Getenv("PROGRAMDATA")
    desktopPath := filepath.Join(public, "Desktop", "LinkVideo.Helper.lnk")
    menuDir := filepath.Join(programData, `Microsoft\Windows\Start Menu\Programs\LinkVideo.Helper`)
    menuPath := filepath.Join(menuDir, "LinkVideo.Helper.lnk")
    desktopFlag := "$false"
    if desktop {
        desktopFlag = "$true"
    }
    return fmt.Sprintf(`$ErrorActionPreference='Stop';`+
        `$w=New-Object -ComObject WScript.Shell;`+
        `$menu='%s';New-Item -ItemType Directory -Force -Path $menu|Out-Null;`+
        `$targets=@('%s');if(%s){$targets+=@('%s')};`+
        `foreach($p in $targets){$s=$w.CreateShortcut($p);$s.TargetPath='%s';$s.WorkingDirectory='%s';$s.IconLocation='%s,0';$s.Description='LinkVideo.Helper';$s.Save()}`+
        `if(-not %s){Remove-Item -Force -ErrorAction SilentlyContinue '%s'}`,
        psEscape(menuDir), psEscape(menuPath), desktopFlag, psEscape(desktopPath),
        psEscape(appPath), psEscape(dest), psEscape(appPath), desktopFlag, psEscape(desktopPath))
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
    return runHidden("powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded)
}

func createShortcuts(appPath, dest string, desktop bool) error {
    return runPowerShell(shortcutScript(appPath, dest, desktop))
}

func removeLegacyInstallArtifacts(dest string) {
    runCleanup("reg.exe", "delete", legacyInnoKey, "/f")
    runCleanup("reg.exe", "delete", `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\LinkVideo.Helper`, "/f")
    for _, name := range []string{"unins000.exe", "unins000.dat", "unins001.exe", "unins001.dat"} {
        _ = os.Remove(filepath.Join(dest, name))
    }
    for _, path := range []string{
        filepath.Join(os.Getenv("PUBLIC"), "Desktop", "LinkVideo VPN Helper.lnk"),
        filepath.Join(os.Getenv("PROGRAMDATA"), `Microsoft\Windows\Start Menu\Programs\LinkVideo.Helper`, "LinkVideo VPN Helper.lnk"),
    } {
        _ = os.Remove(path)
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
        if err := runHidden("reg.exe", full...); err != nil {
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
    progress(91, "Регистрация в Windows…")
    if err := registerUninstall(appPath, dest); err != nil {
        return "", fmt.Errorf("не удалось зарегистрировать удаление: %w", err)
    }
    progress(100, "LinkVideo.Helper установлен")
    return appPath, nil
}

func removeShortcuts() {
    for _, path := range []string{
        filepath.Join(os.Getenv("PUBLIC"), "Desktop", "LinkVideo.Helper.lnk"),
        filepath.Join(os.Getenv("PUBLIC"), "Desktop", "LinkVideo VPN Helper.lnk"),
        filepath.Join(os.Getenv("PROGRAMDATA"), `Microsoft\Windows\Start Menu\Programs\LinkVideo.Helper`),
    } {
        _ = os.RemoveAll(path)
    }
}

func removeUserData() {
    // QSettings("LinkVideo", "LinkVideo.Helper") on Windows uses HKCU.
    runCleanup("reg.exe", "delete", `HKCU\Software\LinkVideo\LinkVideo.Helper`, "/f")
    // Remove the legacy settings only on an explicit full-clean request.
    runCleanup("reg.exe", "delete", `HKCU\Software\LinkVideo\VPNHelper`, "/f")
    for _, root := range []string{
        filepath.Join(os.Getenv("LOCALAPPDATA"), "LinkVideo.Helper"),
        filepath.Join(os.Getenv("APPDATA"), "LinkVideo.Helper"),
    } {
        if strings.TrimSpace(root) != "" && filepath.Clean(root) != "." {
            _ = os.RemoveAll(root)
        }
    }
}

func uninstallProduct(removeData bool, progress progressFunc) error {
    dest := defaultInstallDir()
    progress(8, "Остановка LinkVideo.Helper…")
    stopHelperProcesses()
    progress(25, "Удаление регистрации Windows…")
    runCleanup("reg.exe", "delete", uninstallKey, "/f")
    runCleanup("reg.exe", "delete", legacyInnoKey, "/f")
    progress(38, "Удаление ярлыков…")
    removeShortcuts()
    if removeData {
        progress(50, "Удаление настроек и кэша…")
        removeUserData()
    }
    progress(62, "Удаление файлов программы…")
    self, _ := os.Executable()
    for _, entry := range []string{"_internal", "tools", "linkvideo_vpn_helper", "scripts", appExeName, "LinkVideo VPN Helper.exe", "updater.exe"} {
        target := filepath.Join(dest, entry)
        if strings.EqualFold(filepath.Clean(target), filepath.Clean(self)) {
            continue
        }
        _ = os.RemoveAll(target)
    }
    // Other payload files are safe to remove after known settings are preserved.
    entries, _ := os.ReadDir(dest)
    for _, entry := range entries {
        target := filepath.Join(dest, entry.Name())
        if strings.EqualFold(filepath.Clean(target), filepath.Clean(self)) {
            continue
        }
        _ = os.RemoveAll(target)
    }
    progress(95, "Завершение удаления…")
    progress(100, "LinkVideo.Helper удалён")
    return nil
}

func scheduleSelfDelete() {
    self, err := os.Executable()
    if err != nil {
        return
    }
    dest := filepath.Dir(self)
    cmd := exec.Command("cmd.exe", "/C", fmt.Sprintf(`ping 127.0.0.1 -n 3 >nul & del /f /q "%s" & rmdir /s /q "%s"`, self, dest))
    cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
    _ = cmd.Start()
}

func launchApplication(appPath string) error {
    cmd := exec.Command("explorer.exe", appPath)
    cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
    return cmd.Start()
}
