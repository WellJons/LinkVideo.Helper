//go:build windows
package main

import (
 "archive/tar"
 "bytes"
 _ "embed"
 "encoding/json"
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
 "github.com/ulikunitz/xz"
)

//go:embed payload.tar.xz
var payload []byte
var version="4.0.0-alpha.13"
var buildMode="installer"
const appExeName="LinkVideo.Helper.4.exe"
const uninstallKey="HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\LinkVideo.Helper.4.Preview_is1"
type progressFunc func(int,string)

func defaultInstallDir() string {return filepath.Join(os.Getenv("LOCALAPPDATA"),"Programs","LinkVideo.Helper.4")}
func profileDir() string {return filepath.Join(os.Getenv("APPDATA"),"LinkVideo.Helper.v4-preview")}
func selfDir() string {p,_:=os.Executable();return filepath.Dir(p)}
func same(a,b string)bool{return strings.EqualFold(filepath.Clean(a),filepath.Clean(b))}
func hidden(exe string,args ...string)*exec.Cmd{c:=exec.Command(exe,args...);c.SysProcAttr=&syscall.SysProcAttr{HideWindow:true,CreationFlags:0x08000000};return c}
func command(exe string,args ...string)error{return hidden(exe,args...).Run()}
func report(n int,s string){_ = json.NewEncoder(os.Stdout).Encode(map[string]any{"percent":n,"label":s})}
func message(s string){t,_:=syscall.UTF16PtrFromString("LinkVideo.Helper 4");v,_:=syscall.UTF16PtrFromString(s);syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW").Call(0,uintptr(unsafe.Pointer(v)),uintptr(unsafe.Pointer(t)),0x10)}
func prepare()error{
 if !filepath.IsAbs(os.Getenv("LOCALAPPDATA"))||!filepath.IsAbs(os.Getenv("APPDATA")){return errors.New("Не найдена папка текущего пользователя Windows.")}
 var key syscall.Handle
 sub,_:=syscall.UTF16PtrFromString(strings.TrimPrefix(uninstallKey,"HKCU\\"))
 err:=syscall.RegOpenKeyEx(syscall.Handle(syscall.HKEY_CURRENT_USER),sub,0,syscall.KEY_QUERY_VALUE,&key)
 if err==nil{
  defer syscall.RegCloseKey(key)
  name,_:=syscall.UTF16PtrFromString("InstallLocation");var typ,n uint32
  if e:=syscall.RegQueryValueEx(key,name,nil,&typ,nil,&n);e==nil&&n>0{
   if typ!=1||n>65536||n%2!=0{return errors.New("Некорректная запись установки.")}
   buf:=make([]uint16,n/2);if e=syscall.RegQueryValueEx(key,name,nil,&typ,(*byte)(unsafe.Pointer(&buf[0])),&n);e!=nil{return e}
   existing:=syscall.UTF16ToString(buf);if existing!=""&&!same(existing,defaultInstallDir()){return errors.New("Версия 4.0 установлена в другой папке. Сначала удалите её через приложения Windows.")}
  }else if e!=nil&&!errors.Is(e,syscall.ERROR_FILE_NOT_FOUND){return e}
 }else if !errors.Is(err,syscall.ERROR_FILE_NOT_FOUND){return err}
 return os.Chdir(os.TempDir())
}
func operationLock()(func(),error){
 name,_:=syscall.UTF16PtrFromString("Local\\LinkVideo.Helper.4.Setup.Operation")
 h,_,e:=syscall.NewLazyDLL("kernel32.dll").NewProc("CreateMutexW").Call(0,0,uintptr(unsafe.Pointer(name)))
 if h==0{return nil,e}
 if e==syscall.Errno(183){syscall.CloseHandle(syscall.Handle(h));return nil,errors.New("Установка или удаление уже выполняется.")}
 return func(){syscall.CloseHandle(syscall.Handle(h))},nil
}
func stageDir() (string,error){return os.MkdirTemp("","LinkVideo.Helper.4-Wizard-")}
func safeStage(dir string)bool{return same(filepath.Dir(dir),os.TempDir())&&strings.HasPrefix(filepath.Base(dir),"LinkVideo.Helper.4-Wizard-")}
func copyFile(src,dst string)error{
 st,e:=os.Lstat(src);if e!=nil{return e};if !st.Mode().IsRegular(){return errors.New("Неподдерживаемый файл: "+filepath.Base(src))}
 in,e:=os.Open(src);if e!=nil{return e};defer in.Close()
 if e=os.MkdirAll(filepath.Dir(dst),0755);e!=nil{return e}
 out,e:=os.OpenFile(dst,os.O_CREATE|os.O_EXCL|os.O_WRONLY,0755);if e!=nil{return e}
 _,e=io.Copy(out,in);ce:=out.Close();if e!=nil{return e};return ce
}
func copyTree(src,dst string,uiOnly bool)error{
 return filepath.WalkDir(src,func(p string,d os.DirEntry,e error)error{
  if e!=nil{return e};rel,e:=filepath.Rel(src,p);if e!=nil{return e};if rel=="."{return nil}
  r:=filepath.ToSlash(rel)
  if uiOnly{
   if r=="resources/worker"||strings.HasPrefix(r,"resources/worker/"){if d.IsDir(){return filepath.SkipDir};return nil}
   if strings.HasPrefix(strings.ToLower(r),"unins")&&r!="Uninstall.exe"{return nil}
  }
  if d.Type()&os.ModeSymlink!=0{return errors.New("Ссылки в runtime не поддерживаются")}
  if d.IsDir(){return os.MkdirAll(filepath.Join(dst,rel),0755)}
  return copyFile(p,filepath.Join(dst,rel))
 })
}
func extract(dir string)error{
 if len(payload)<1000000{return errors.New("Установочный пакет повреждён.")}
 x,e:=xz.NewReader(bytes.NewReader(payload));if e!=nil{return e}
 t:=tar.NewReader(x)
 for{
  h,e:=t.Next();if e==io.EOF{break};if e!=nil{return e}
  name:=filepath.Clean(filepath.FromSlash(h.Name))
  if name=="."||filepath.IsAbs(name)||filepath.VolumeName(name)!=""||name==".."||strings.HasPrefix(name,".."+string(os.PathSeparator)){return errors.New("Недопустимый путь в пакете")}
  dest:=filepath.Join(dir,name)
  if h.Typeflag==tar.TypeDir{if e=os.MkdirAll(dest,0755);e!=nil{return e};continue}
  if h.Typeflag!=tar.TypeReg{return errors.New("Недопустимый тип файла в пакете")}
  if e=os.MkdirAll(filepath.Dir(dest),0755);e!=nil{return e}
  file,e:=os.OpenFile(dest,os.O_CREATE|os.O_EXCL|os.O_WRONLY,0755);if e!=nil{return e}
  _,e=io.Copy(file,t);ce:=file.Close();if e!=nil{return e};if ce!=nil{return ce}
 }
 if _,e=io.Copy(io.Discard,x);e!=nil{return e}
 return verifyRuntimeSnapshot(dir)
}
func verifyRuntimeSnapshot(dest string)error{
 for _,name:=range []string{appExeName,"Uninstall.exe","resources/app.asar","resources/worker/helper-core.exe","resources.pak","icudtl.dat"}{
  st,e:=os.Stat(filepath.Join(dest,filepath.FromSlash(name)));if e!=nil||!st.Mode().IsRegular()||st.Size()==0{return fmt.Errorf("Не найден обязательный файл: %s",name)}
 };return nil
}
func previousRuntimeUsable(dest string)bool{
 st,e:=os.Stat(filepath.Join(dest,appExeName));return e==nil&&st.Mode().IsRegular()&&st.Size()>100000
}
func renameRuntimeWithRetry(from, to string) error {
    deadline := time.Now().Add(12 * time.Second)
    for {
        err := os.Rename(from, to)
        if err == nil {
            return nil
        }
        var code syscall.Errno
        if !errors.As(err, &code) || (code != 5 && code != 32 && code != 33) {
            return err
        }
        if !time.Now().Before(deadline) {
            return fmt.Errorf("папка LinkVideo.Helper всё ещё занята другим процессом; "+
                "закройте Helper, перезапустите Windows и повторите установку: %w", err)
        }
        time.Sleep(300 * time.Millisecond)
    }
}

func recoverInterruptedRuntimeUpgrade(dest string) error {
    backup := dest + ".rollback"
    if _, err := os.Stat(backup); errors.Is(err, os.ErrNotExist) {
        return nil
    } else if err != nil {
        return fmt.Errorf("не удалось проверить резервную копию runtime: %w", err)
    }
    if verifyRuntimeSnapshot(dest) == nil {
        if err := os.RemoveAll(backup); err != nil {
            return fmt.Errorf("не удалось удалить резервную копию завершённого обновления: %w", err)
        }
        return nil
    }
    if !previousRuntimeUsable(backup) {
        return errors.New("резервная копия runtime повреждена; автоматическое удаление остановлено")
    }
    if err := os.RemoveAll(dest); err != nil {
        return fmt.Errorf("не удалось удалить незавершённый runtime: %w", err)
    }
    if err := renameRuntimeWithRetry(backup, dest); err != nil {
        return fmt.Errorf("не удалось восстановить предыдущую версию: %w", err)
    }
    return nil
}

func activateStagedRuntime(dest, staging string) (string, error) {
    backup := dest + ".rollback"
    if _, err := os.Stat(backup); err == nil {
        return "", errors.New("обнаружена необработанная резервная копия runtime")
    } else if !errors.Is(err, os.ErrNotExist) {
        return "", fmt.Errorf("не удалось проверить путь резервной копии: %w", err)
    }

    hadPrevious := false
    if _, err := os.Stat(dest); err == nil {
        if err := renameRuntimeWithRetry(dest, backup); err != nil {
            return "", fmt.Errorf("не удалось сохранить предыдущую версию: %w", err)
        }
        hadPrevious = true
    } else if !errors.Is(err, os.ErrNotExist) {
        return "", fmt.Errorf("не удалось проверить текущую версию: %w", err)
    }

    if err := renameRuntimeWithRetry(staging, dest); err != nil {
        if hadPrevious {
            if restoreErr := renameRuntimeWithRetry(backup, dest); restoreErr != nil {
                return "", fmt.Errorf("не удалось активировать новую версию (%v) и восстановить старую (%v)", err, restoreErr)
            }
        }
        return "", fmt.Errorf("не удалось активировать новую версию: %w", err)
    }
    if hadPrevious {
        return backup, nil
    }
    return "", nil
}

func rollbackActivatedRuntime(dest, backup string) error {
    if err := os.RemoveAll(dest); err != nil {
        return fmt.Errorf("не удалось удалить повреждённый новый runtime: %w", err)
    }
    if backup == "" {
        return nil
    }
    if err := renameRuntimeWithRetry(backup, dest); err != nil {
        return fmt.Errorf("не удалось восстановить runtime из резервной копии: %w", err)
    }
    return nil
}


func psQuote(v string)string{return "'"+strings.ReplaceAll(v,"'","''")+"'"}
func shortcuts(dest string,desktop,remove bool)error{
 app:=filepath.Join(dest,appExeName)
 script:="$ErrorActionPreference='Stop';$w=New-Object -ComObject WScript.Shell;"+
 "$menu=Join-Path ([Environment]::GetFolderPath('Programs')) 'LinkVideo.Helper 4';"+
 "$d=Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'LinkVideo.Helper 4 Preview.lnk';"+
 "$m=Join-Path $menu 'LinkVideo.Helper 4 Preview.lnk';"
 if remove{script+="Remove-Item -LiteralPath $m,$d -Force -ErrorAction SilentlyContinue; if((Test-Path -LiteralPath $menu) -and !(Get-ChildItem -LiteralPath $menu -Force)){Remove-Item -LiteralPath $menu -Force}"}else{
 script+="New-Item -ItemType Directory -Force -Path $menu|Out-Null;$targets=@($m);"
 if desktop{script+="$targets+=@($d);"}else{script+="Remove-Item -LiteralPath $d -Force -ErrorAction SilentlyContinue;"}
 script+="foreach($p in $targets){$s=$w.CreateShortcut($p);$s.TargetPath="+psQuote(app)+";$s.WorkingDirectory="+psQuote(dest)+";$s.IconLocation="+psQuote(app+",0")+";$s.Save()}"
 }
 return command("powershell.exe","-NoProfile","-NonInteractive","-Command",script)
}
func register(dest string)error{
 un:=filepath.Join(dest,"Uninstall.exe");q:="\""+un+"\""
 vals:=[][2]string{{"DisplayName","LinkVideo.Helper 4 Preview"},{"DisplayVersion",version},{"Publisher","LinkVideo"},{"InstallLocation",dest},{"DisplayIcon",filepath.Join(dest,appExeName)+",0"},{"UninstallString",q},{"QuietUninstallString",q+" --quiet"}}
 for _,v:=range vals{if e:=command("reg.exe","add",uninstallKey,"/v",v[0],"/t","REG_SZ","/d",v[1],"/f");e!=nil{return e}}
 for _,v:=range []string{"NoModify","NoRepair"}{if e:=command("reg.exe","add",uninstallKey,"/v",v,"/t","REG_DWORD","/d","1","/f");e!=nil{return e}}
 return nil
}
func install(src string,desktop bool,p progressFunc)error{
 release,e:=operationLock();if e!=nil{return e};defer release()
 if !safeStage(src){return errors.New("Неверная временная папка установщика")}
 if e=verifyRuntimeSnapshot(src);e!=nil{return e}
 dest:=defaultInstallDir()
 p(8,"Проверка предыдущей версии…")
 if e=recoverInterruptedRuntimeUpgrade(dest);e!=nil{return e}
 if e=os.MkdirAll(filepath.Dir(dest),0755);e!=nil{return e}
 staging,e:=os.MkdirTemp(filepath.Dir(dest),"LinkVideo.Helper.4.staging-");if e!=nil{return e};defer os.RemoveAll(staging)
 p(20,"Копирование файлов программы…")
 if e=copyTree(src,staging,false);e!=nil{return e}
 if e=verifyRuntimeSnapshot(staging);e!=nil{return e}
 p(72,"Замена рабочей версии…")
 backup,e:=activateStagedRuntime(dest,staging);if e!=nil{return fmt.Errorf("Закройте Helper 4 и повторите установку: %w",e)}
 if e=verifyRuntimeSnapshot(dest);e!=nil{_ = rollbackActivatedRuntime(dest,backup);return e}
 p(87,"Создание ярлыков и регистрация в Windows…")
 // Keep the valid runtime if Windows registration fails; retry repairs it.
 if e=shortcuts(dest,desktop,false);e!=nil{return e}
 if e=register(dest);e!=nil{return e}
 if backup!=""{_ = os.RemoveAll(backup)}
 p(100,"LinkVideo.Helper 4 установлен")
 return nil
}
func remove(removeData bool,p progressFunc)error{
 release,e:=operationLock();if e!=nil{return e};defer release()
 dest:=defaultInstallDir()
 p(10,"Проверка установки…")
 if e=recoverInterruptedRuntimeUpgrade(dest);e!=nil{return e}
 if _,e=os.Stat(dest);e==nil{
  trash:=fmt.Sprintf("%s.removing-%d",dest,os.Getpid())
  if e=renameRuntimeWithRetry(dest,trash);e!=nil{return errors.New("Закройте LinkVideo.Helper 4 и повторите удаление.")}
  p(35,"Удаление файлов программы…")
  if e=os.RemoveAll(trash);e!=nil{_ = os.Rename(trash,dest);return e}
 }else if !errors.Is(e,os.ErrNotExist){return e}
 p(75,"Удаление регистрации и ярлыков…")
 if e=shortcuts(dest,false,true);e!=nil{return e}
 if e=command("reg.exe","delete",uninstallKey,"/f");e!=nil{
  if command("reg.exe","query",uninstallKey)==nil{return e}
 }
 if removeData{p(92,"Удаление настроек и кэша 4.0…");if e=os.RemoveAll(profileDir());e!=nil{return e}}
 p(100,"LinkVideo.Helper 4 удалён")
 return nil
}
func launchWizard(dir,mode string)error{
 profile,e:=os.MkdirTemp("","LV4-Wizard-Profile-");if e!=nil{return e};defer os.RemoveAll(profile)
 c:=exec.Command(filepath.Join(dir,appExeName),"--lv4-wizard="+mode,"--lv4-wizard-profile="+profile)
 c.Dir=os.TempDir()
 return c.Run()
}
func superviseRemove(dir string)error{
 if !safeStage(dir){return errors.New("Неверная папка удаления")}
 defer os.RemoveAll(dir)
 return launchWizard(dir,"uninstall")
}
func main(){
 args:=os.Args[1:]
 has:=func(v string)bool{for _,a:=range args{if a==v{return true}};return false}
 if e:=prepare();e!=nil{if has("--quiet")||has("--wizard-operation"){report(-1,e.Error())}else{message(e.Error())};os.Exit(1)}
 var e error
 if has("--wizard-operation"){
  if !safeStage(selfDir()){report(-1,"Неверная папка мастера");os.Exit(1)}
  if len(args)!=3{os.Exit(1)}
  switch args[1]{case "install":if args[2]!="yes"&&args[2]!="no"{os.Exit(1)};e=install(selfDir(),args[2]=="yes",report)
  case "uninstall":if args[2]!="yes"&&args[2]!="no"{os.Exit(1)};e=remove(args[2]=="yes",report)
  case "launch":if args[2]!="no"{os.Exit(1)};c:=exec.Command(filepath.Join(defaultInstallDir(),appExeName));e=c.Start();if e==nil{_ = c.Process.Release()}
  default:os.Exit(1)}
  if e!=nil{report(-1,e.Error());os.Exit(1)};return
 }
 if has("--wizard-supervise-remove"){e=superviseRemove(selfDir())
 }else if buildMode=="uninstaller"{
  if has("--quiet")&&!same(selfDir(),defaultInstallDir()){e=remove(false,report)
  }else{
   var dir string;dir,e=stageDir()
   if e==nil{if has("--quiet"){e=copyFile(filepath.Join(selfDir(),"Uninstall.exe"),filepath.Join(dir,"Uninstall.exe"))}else{e=copyTree(selfDir(),dir,true)}}
   if e==nil{arg:="--wizard-supervise-remove";if has("--quiet"){arg="--quiet"};c:=exec.Command(filepath.Join(dir,"Uninstall.exe"),arg);c.Dir=os.TempDir();e=c.Start();if e==nil{_ = c.Process.Release()}}
   if e!=nil&&dir!=""{_ = os.RemoveAll(dir)}
  }
 }else{
  var dir string;dir,e=stageDir()
  if e==nil{e=extract(dir)}
  if e==nil{if has("--quiet"){e=install(dir,false,report)}else{e=launchWizard(dir,"install")}}
  if dir!=""{_ = os.RemoveAll(dir)}
 }
 if e!=nil{if has("--quiet"){report(-1,e.Error())}else{message(e.Error())};os.Exit(1)}
}
