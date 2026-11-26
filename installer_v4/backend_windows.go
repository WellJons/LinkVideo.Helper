//go:build windows

package main

import (
    _ "embed"
    "errors"
    "fmt"
    "os"
    "os/exec"
    "path/filepath"
    "strings"
    "syscall"
    "time"
)

// The original Win32 wizard is the only visible installer. The compressed
// Inno payload runs silently, preserving its existing 4.x registration.
//go:embed payload.exe
var payload []byte
var version = "4.0.0-alpha.13"
var buildMode = "installer"

const productName = "LinkVideo.Helper 4"
const appExeName = "LinkVideo.Helper.4.exe"
const uninstallKey = `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\LinkVideo.Helper.4.Preview_is1`

type installOptions struct { DesktopShortcut bool }
type progressFunc func(int, string)

func defaultInstallDir() string {
    return filepath.Join(os.Getenv("LOCALAPPDATA"), "Programs", "LinkVideo.Helper.4")
}
func hidden(name string, args ...string) *exec.Cmd {
    c := exec.Command(name,args...)
    c.SysProcAttr=&syscall.SysProcAttr{HideWindow:true, CreationFlags:0x08000000}
    return c
}
func registeredDir() string {
    out,err:=hidden("reg.exe","query",uninstallKey,"/v","InstallLocation").Output()
    if err!=nil { return "" }
    for _,line:=range strings.Split(string(out),"\n") {
        if i:=strings.Index(line,"REG_SZ");i>=0 { return strings.TrimSpace(line[i+6:]) }
    }
    return ""
}
func prepare() error {
    if !filepath.IsAbs(os.Getenv("LOCALAPPDATA")) || !filepath.IsAbs(os.Getenv("APPDATA")) { return errors.New("Не найдена папка текущего пользователя Windows.") }
    if existing:=registeredDir(); existing!="" && !strings.EqualFold(filepath.Clean(existing),filepath.Clean(defaultInstallDir())) {
        return errors.New("Версия 4.0 установлена в другой папке. Сначала удалите её через приложения Windows, затем запустите установщик.")
    }
    return os.Chdir(os.TempDir())
}
func registerWizard(dest string) error {
    un:=filepath.Join(dest,"Uninstall.exe")
    for _,v:=range [][2]string{{"UninstallString",fmt.Sprintf("%q",un)},{"QuietUninstallString",fmt.Sprintf("%q --quiet",un)}} {
        // Windows command-line quoting, not Go string-literal escaping.
        data:="\""+un+"\""
        if v[0]=="QuietUninstallString" {data+=" --quiet"}
        if err:=hidden("reg.exe","add",uninstallKey,"/v",v[0],"/t","REG_SZ","/d",data,"/f").Run();err!=nil {return fmt.Errorf("Не удалось зарегистрировать мастер удаления: %w",err)}
    }
    return nil
}
func installProduct(opts installOptions, progress progressFunc)(string,error) {
    dest:=defaultInstallDir()
    if len(payload)<1000000 {return "",errors.New("Установочный пакет повреждён.")}
    progress(5,"Подготовка сжатого пакета…")
    tmp,err:=os.MkdirTemp("","LinkVideo.Helper.4-setup-")
    if err!=nil{return "",err};defer os.RemoveAll(tmp)
    exe:=filepath.Join(tmp,"payload.exe")
    if err=os.WriteFile(exe,payload,0600);err!=nil{return "",err}
    task:="/TASKS="
    if opts.DesktopShortcut {task="/TASKS=desktopicon"}
    progress(20,"Установка файлов LinkVideo.Helper 4…")
    cmd:=hidden(exe,"/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/DIR="+dest,task,"/LOG="+filepath.Join(tmp,"setup.log"))
    if err=cmd.Run();err!=nil{return "",fmt.Errorf("Установка не завершена: %w",err)}
    progress(85,"Проверка установленной программы…")
    app:=filepath.Join(dest,appExeName)
    for _,name:=range []string{appExeName,"Uninstall.exe","resources/worker/helper-core.exe","resources/app.asar"} {
        if st,e:=os.Stat(filepath.Join(dest,name));e!=nil||!st.Mode().IsRegular()||st.Size()==0{return "",fmt.Errorf("Не найден обязательный файл: %s",name)}
    }
    if err=registerWizard(dest);err!=nil{return "",err}
    progress(100,"LinkVideo.Helper 4 установлен")
    return app,nil
}
func ensureUninstallerRunsFromTemp()(bool,error){
    self,err:=os.Executable();if err!=nil{return false,err}
    if !strings.EqualFold(filepath.Clean(filepath.Dir(self)),filepath.Clean(defaultInstallDir())){return true,nil}
    folder,err:=os.MkdirTemp("","LinkVideo.Helper.4-uninstall-");if err!=nil{return false,err}
    data,err:=os.ReadFile(self);if err!=nil{return false,err}
    target:=filepath.Join(folder,"Uninstall.exe")
    if err=os.WriteFile(target,data,0700);err!=nil{return false,err}
    c:=exec.Command(target,os.Args[1:]...);c.Dir=folder
    if err=c.Start();err!=nil{return false,err}
    _=c.Process.Release()
    return false,nil
}
func uninstallProduct(removeData bool,progress progressFunc) error{
    dest:=defaultInstallDir()
    progress(8,"Подготовка к удалению…")
    // Only the specific 4.x Inno uninstaller in the fixed user directory.
    matches,err:=filepath.Glob(filepath.Join(dest,"unins*.exe"))
    if err!=nil||len(matches)!=1{return errors.New("Не найден однозначный пакет удаления версии 4.0.")}
    progress(30,"Удаление файлов и ярлыков…")
    if err=hidden(matches[0],"/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART").Run();err!=nil{return fmt.Errorf("Удаление не завершено: %w",err)}
    // Inno waits for its own bootstrap. Verify the app is gone before reporting success.
    for i:=0;i<30;i++{
        if _,err=os.Stat(filepath.Join(dest,appExeName));os.IsNotExist(err){break}
        time.Sleep(200*time.Millisecond)
    }
    if _,err=os.Stat(filepath.Join(dest,appExeName));!os.IsNotExist(err){return errors.New("Файлы программы остались. Закройте Helper 4 и повторите удаление.")}
    if removeData {
        progress(90,"Удаление настроек и кэша версии 4.0…")
        if err=os.RemoveAll(filepath.Join(os.Getenv("APPDATA"),"LinkVideo.Helper.v4-preview"));err!=nil{return err}
    }
    progress(100,"LinkVideo.Helper 4 удалён")
    return nil
}
func scheduleSelfDelete() {
    // No elevated operations and no recursive removal outside the 4.x profile.
    // The tiny bootstrap executable is left in TEMP for Windows cleanup.
}
func launchApplication(appPath string) error {return exec.Command(appPath).Start()}
