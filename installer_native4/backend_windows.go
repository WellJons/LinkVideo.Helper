//go:build windows

// Native 4 installer: original Win32 wizard, completely separate safe backend.
// NEVER touch files/tasks/processes/registry keys belonging to stable Helper 3.
package main

import (
 "archive/zip"
 "bytes"
 "context"
 "errors"
 "fmt"
 "io"
 "os"
 "os/exec"
 "path/filepath"
 "strings"
 "syscall"
 "time"
 "unsafe"
)

const (
 productName = "LinkVideo.Helper 4 Native Preview"
 appExeName = "LinkVideo.Helper.4.exe"
 appDirName = "LinkVideo.Helper.Native4"
 uninstallKey = "HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\LinkVideo.Helper.Native4"
 moveFileDelayUntilReboot = 0x00000004
)
var version="4.0.0-alpha.14"
var buildMode="installer"
type installOptions struct{DesktopShortcut bool}
type progressFunc func(int,string)

func defaultInstallDir()string{
 if p:=strings.TrimSpace(os.Getenv("ProgramFiles"));p!=""{return filepath.Join(p,appDirName)}
 return "C:\\Program Files\\LinkVideo.Helper.Native4"
}
func detachInstallerWorkingDirectory()error{
 temp:=filepath.Clean(os.TempDir())
 dest:=filepath.Clean(defaultInstallDir())
 if strings.EqualFold(temp,dest)||strings.HasPrefix(strings.ToLower(temp),strings.ToLower(dest)+string(os.PathSeparator)){return errors.New("Небезопасный каталог TEMP")}
 return os.Chdir(temp)
}
func isProcessElevated()bool{
 r,_,_:=syscall.NewLazyDLL("shell32.dll").NewProc("IsUserAnAdmin").Call()
 return r!=0
}
type shellExecuteInfoW struct {
 CBSize uint32; FMask uint32; Hwnd uintptr; LpVerb *uint16; LpFile *uint16; LpParameters *uint16
 LpDirectory *uint16; NShow int32; HInstApp uintptr; LpIDList uintptr; LpClass *uint16
 HkeyClass uintptr; DwHotKey uint32; HIcon uintptr; HProcess uintptr
}
func shellExecuteRunAs(exe string,args []string)error{
 verb,_:=syscall.UTF16PtrFromString("runas")
 file,_:=syscall.UTF16PtrFromString(exe)
 // Flags are fixed; never accepts untrusted arbitrary command strings.
 params,_:=syscall.UTF16PtrFromString(strings.Join(args," "))
 dir,_:=syscall.UTF16PtrFromString(os.TempDir())
 info:=shellExecuteInfoW{CBSize:uint32(unsafe.Sizeof(shellExecuteInfoW{})),FMask:0x40,LpVerb:verb,LpFile:file,LpParameters:params,LpDirectory:dir,NShow:1}
 r,_,err:=syscall.NewLazyDLL("shell32.dll").NewProc("ShellExecuteExW").Call(uintptr(unsafe.Pointer(&info)))
 if r==0{return fmt.Errorf("Windows не разрешила запуск от администратора: %v",err)}
 if info.HProcess!=0{syscall.NewLazyDLL("kernel32.dll").NewProc("CloseHandle").Call(info.HProcess)}
 return nil
}
func ensureElevated()(bool,error){
 if isProcessElevated(){return true,nil}
 self,err:=os.Executable();if err!=nil{return false,err}
 return false,shellExecuteRunAs(self,os.Args[1:])
}
func ensureUninstallerRunsFromTemp()(bool,error){
 self,err:=os.Executable();if err!=nil{return false,err}
 if !strings.EqualFold(filepath.Clean(filepath.Dir(self)),filepath.Clean(defaultInstallDir())){return true,nil}
 dir:=filepath.Join(os.TempDir(),"LinkVideo.Helper.Native4.Uninstall")
 if err=os.MkdirAll(dir,0700);err!=nil{return false,err}
 dst:=filepath.Join(dir,fmt.Sprintf("Uninstall-%d.exe",os.Getpid()))
 raw,err:=os.ReadFile(self);if err!=nil{return false,err}
 if err=os.WriteFile(dst,raw,0700);err!=nil{return false,err}
 if err=shellExecuteRunAs(dst,os.Args[1:]);err!=nil{os.Remove(dst);return false,err}
 return false,nil
}
func scheduleSelfDelete(){
 self,err:=os.Executable();if err!=nil{return}
 utf,err:=syscall.UTF16PtrFromString(self);if err!=nil{return}
 syscall.NewLazyDLL("kernel32.dll").NewProc("MoveFileExW").Call(uintptr(unsafe.Pointer(utf)),0,moveFileDelayUntilReboot)
}
func runWindows(name string,args ...string)error{
 ctx,cancel:=context.WithTimeout(context.Background(),20*time.Second);defer cancel()
 cmd:=exec.CommandContext(ctx,name,args...)
 cmd.SysProcAttr=&syscall.SysProcAttr{HideWindow:true}
 out,err:=cmd.CombinedOutput()
 if err!=nil{return fmt.Errorf("%s не завершён: %w (%s)",filepath.Base(name),err,strings.TrimSpace(string(out)))}
 return nil
}
func psQuote(s string)string{return "'"+strings.ReplaceAll(s,"'","''")+"'"}
func shortcuts(app string,desktop bool)error{
 data:=strings.TrimSpace(os.Getenv("PROGRAMDATA"));public:=strings.TrimSpace(os.Getenv("PUBLIC"))
 if data==""||public==""{return errors.New("Windows не сообщила каталоги ярлыков")}
 dir:=filepath.Join(data,"Microsoft","Windows","Start Menu","Programs","LinkVideo.Helper 4")
 if err:=os.MkdirAll(dir,0755);err!=nil{return err}
 start:=filepath.Join(dir,"LinkVideo.Helper 4.lnk")
 desk:=filepath.Join(public,"Desktop","LinkVideo.Helper 4.lnk")
 script:="$ErrorActionPreference='Stop';$w=New-Object -ComObject WScript.Shell;"+
  "$x=$w.CreateShortcut("+psQuote(start)+");$x.TargetPath="+psQuote(app)+";$x.WorkingDirectory="+psQuote(filepath.Dir(app))+";$x.Save();"
 if desktop{
  script+="$x=$w.CreateShortcut("+psQuote(desk)+");$x.TargetPath="+psQuote(app)+";$x.WorkingDirectory="+psQuote(filepath.Dir(app))+";$x.Save();"
 }else{_ =os.Remove(desk)}
 return runWindows("powershell.exe","-NoProfile","-NonInteractive","-Command",script)
}
func removeShortcuts(){
 data:=strings.TrimSpace(os.Getenv("PROGRAMDATA"));public:=strings.TrimSpace(os.Getenv("PUBLIC"))
 if data!=""{_ =os.RemoveAll(filepath.Join(data,"Microsoft","Windows","Start Menu","Programs","LinkVideo.Helper 4"))}
 if public!=""{_ =os.Remove(filepath.Join(public,"Desktop","LinkVideo.Helper 4.lnk"))}
}
func regAdd(field,kind,value string)error{
 return runWindows("reg.exe","add",uninstallKey,"/v",field,"/t",kind,"/d",value,"/f")
}
func registerUninstall(app,dest string)error{
 uninstaller:=filepath.Join(dest,"Uninstall.exe")
 if st,err:=os.Stat(uninstaller);err!=nil||!st.Mode().IsRegular()||st.Size()<100000{return errors.New("Установщик не нашёл исправный Uninstall.exe")}
 for _,entry:=range [][3]string{
  {"DisplayName","REG_SZ",productName},{"DisplayVersion","REG_SZ",version},
  {"Publisher","REG_SZ","LinkVideo"},{"InstallLocation","REG_SZ",dest},
  {"DisplayIcon","REG_SZ",app+",0"},{"UninstallString","REG_SZ","\""+uninstaller+"\""},
  {"NoModify","REG_DWORD","1"},{"NoRepair","REG_DWORD","1"},
 }{
  if err:=regAdd(entry[0],entry[1],entry[2]);err!=nil{return err}
 }
 return nil
}
func validateEntry(name string) (string,error){
 if name==""||strings.Contains(name,"\\")||strings.Contains(name,":")||strings.HasPrefix(name,"/"){return "",errors.New("Небезопасный путь ZIP")}
 cleaned:=filepath.Clean(filepath.FromSlash(name))
 if cleaned=="."||cleaned==".."||strings.HasPrefix(cleaned,".."+string(os.PathSeparator))||filepath.IsAbs(cleaned){return "",errors.New("Небезопасный путь ZIP")}
 return cleaned,nil
}
func extractPayload(dest string,progress progressFunc)error{
 zr,err:=zip.NewReader(bytes.NewReader(payload),int64(len(payload)));if err!=nil{return err}
 if len(zr.File)>30000{return errors.New("Слишком много файлов в архиве")}
 var total uint64
 for n,f:=range zr.File{
  name,err:=validateEntry(f.Name);if err!=nil{return err}
  if f.Mode()&os.ModeSymlink!=0{return errors.New("Ссылки в архиве запрещены")}
  total+=f.UncompressedSize64
  if total>1500*1024*1024{return errors.New("Размер распаковки превышен")}
  target:=filepath.Join(dest,name)
  if f.FileInfo().IsDir(){if err=os.MkdirAll(target,0755);err!=nil{return err};continue}
  if err=os.MkdirAll(filepath.Dir(target),0755);err!=nil{return err}
  src,err:=f.Open();if err!=nil{return err}
  out,err:=os.OpenFile(target,os.O_CREATE|os.O_EXCL|os.O_WRONLY,0600)
  if err!=nil{src.Close();return err}
  _,err=io.CopyN(out,src,int64(f.UncompressedSize64)+1)
  closeErr:=out.Close();src.Close()
  if err==nil||err==io.EOF{err=closeErr}else if err==nil{err=closeErr}
  if err!=nil{return err}
  if n%100==0{progress(10+int(float64(n)*66/float64(len(zr.File))),"Распаковка файлов приложения…")}
 }
 for _,file:=range []string{appExeName,"Uninstall.exe"}{
  st,err:=os.Stat(filepath.Join(dest,file));if err!=nil||st.IsDir()||st.Size()<100000{return fmt.Errorf("Не найден файл %s",file)}
 }
 return nil
}
func installProduct(opts installOptions,progress progressFunc)(string,error){
 dest:=defaultInstallDir();parent:=filepath.Dir(dest)
 if err:=os.MkdirAll(parent,0755);err!=nil{return "",err}
 stage:=filepath.Join(parent,fmt.Sprintf(".lv4-native-stage-%d",os.Getpid()))
 backup:=dest+".rollback"
 if _,err:=os.Lstat(backup);err==nil{return "",errors.New("Обнаружена резервная папка Native4. Требуется ручная проверка установки.")}
 if _,err:=os.Lstat(stage);err==nil{return "",errors.New("Временный каталог уже существует.")}
 if err:=os.Mkdir(stage,0700);err!=nil{return "",err}
 defer os.RemoveAll(stage)
 progress(5,"Проверяю дистрибутив…")
 if err:=extractPayload(stage,progress);err!=nil{return "",err}
 hadOld:=false
 if st,err:=os.Lstat(dest);err==nil{
  if !st.IsDir()||st.Mode()&os.ModeSymlink!=0{return "",errors.New("Каталог Native4 не является обычной папкой")}
  hadOld=true
  if err:=os.Rename(dest,backup);err!=nil{return "",errors.New("Сначала закройте Helper 4, затем повторите установку")}
 }else if !os.IsNotExist(err){return "",err}
 progress(83,"Устанавливаю настольную версию…")
 if err:=os.Rename(stage,dest);err!=nil{
  if hadOld{_ =os.Rename(backup,dest)}
  return "",err
 }
 exe:=filepath.Join(dest,appExeName)
 if err:=registerUninstall(exe,dest);err!=nil{
  _=os.RemoveAll(dest)
  if hadOld{_ =os.Rename(backup,dest)}
  return "",err
 }
 progress(92,"Создаю ярлыки…")
 if err:=shortcuts(exe,opts.DesktopShortcut);err!=nil{
  // Installed app is valid, shortcut failure is not silent.
  progress(92,"Установка завершена; проверьте ярлыки.")
 }
 if hadOld{_ =os.RemoveAll(backup)}
 progress(100,"LinkVideo.Helper 4 установлен")
 return exe,nil
}
func removeUserData(){
 // Only Native4 preferences. Do not delete the stable 3.x account/settings.
 _=runWindows("reg.exe","delete","HKCU\\Software\\LinkVideo\\LinkVideo.Helper.Native4","/f")
}
func uninstallProduct(removeData bool,progress progressFunc)error{
 dest:=defaultInstallDir()
 st,err:=os.Lstat(dest);if os.IsNotExist(err){return nil};if err!=nil{return err}
 if !st.IsDir()||st.Mode()&os.ModeSymlink!=0{return errors.New("Подмена каталога Native4: отменено")}
 progress(20,"Удаляю только LinkVideo.Helper 4…")
 if err:=os.RemoveAll(dest);err!=nil{return fmt.Errorf("Закройте Helper 4 и повторите удаление: %w",err)}
 removeShortcuts()
 _=runWindows("reg.exe","delete",uninstallKey,"/f")
 if removeData{removeUserData()}
 progress(100,"Native4 удалён; версия 3.x не затронута")
 return nil
}
func launchApplication(app string)error{
 cmd:=exec.Command("explorer.exe",app)
 cmd.SysProcAttr=&syscall.SysProcAttr{HideWindow:true}
 return cmd.Start()
}

// Read-only packaged installer verification. Does not touch registry or install files.
func verifyEmbeddedPayload() error {
    if len(payload) < 500000 { return errors.New("Native4 installer payload is empty") }
    zr,err:=zip.NewReader(bytes.NewReader(payload),int64(len(payload)));if err!=nil{return err}
    foundApp:=false;foundUninstaller:=false
    for _,f:=range zr.File {
        if _,err:=validateEntry(f.Name);err!=nil{return err}
        if f.Name==appExeName { foundApp=true }
        if f.Name=="Uninstall.exe" { foundUninstaller=true }
    }
    if !foundApp||!foundUninstaller{return errors.New("Missing Native4 executable/uninstaller")}
    return nil
}
