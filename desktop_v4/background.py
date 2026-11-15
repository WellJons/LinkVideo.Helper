"""Opt-in emergency backup, isolated from the normal per-user Electron app.

The privileged entry point runs ONLY from the separate, administrator-installed
Program Files component. No SYSTEM action points into a per-user directory.
The old 3.x task and config are never modified.
"""
from __future__ import annotations
import base64
import csv
import ctypes
import io
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from ctypes import wintypes
from xml.etree import ElementTree

TASK="LinkVideo.Helper.4.VPNSheetsMirror"
LEGACY="LinkVideo.Helper.VPNSheetsMirror"
REGISTRY=r"SOFTWARE\LinkVideo\Helper4\Background"
DIRECTORY="LinkVideo.Helper.4.Background"
INTERVALS=(6,12,24)
PREFIX="--lv4-background-"
ALLOWED_ACTIONS=("enable","disable","verify","run")
MESSAGES={3:"Требуются права администратора и отдельный компонент в Program Files.",
          10:"Не удалось прочитать защищённую временную конфигурацию.",
          11:"Конфигурация не прошла проверку.",
          12:"Не удалось защитить каталог данных.",
          14:"Планировщик не подтвердил изменение; проверьте состояние задачи.",
          18:"Существует задача Helper 3.x. Сначала отключите её штатным способом в 3.x.",
          19:"Фоновое резервирование выполняется. Повторите после завершения.",
          21:"Задание 4.0 не найдено или его действие не подтверждено.",
          22:"Проверка с правами администратора не выполнена.",
          25:"Перед обновлением компонента отключите задачу 4.0."}

def known_folder(csid):
    if os.name!="nt":raise RuntimeError("Поддерживается только Windows.")
    buf=ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None,csid,None,0,buf)!=0 or not buf.value:
        raise RuntimeError("Не найден системный каталог.")
    return Path(buf.value)

def root():
    return known_folder(0x26)/DIRECTORY  # 64-bit worker and 64-bit installer

def executable():
    return root()/"helper-core.exe"

def no_reparse(path):
    try:
        s=path.lstat()
        return not path.is_symlink() and not (getattr(s,"st_file_attributes",0)&getattr(stat,"FILE_ATTRIBUTE_REPARSE_POINT",1024))
    except OSError:return False

def installed():
    try:
        p=executable()
        return p.is_file() and no_reparse(root()) and no_reparse(p) and p.resolve()==p
    except (OSError,RuntimeError):return False

def is_runner():
    if not getattr(sys,"frozen",False) or not installed():return False
    try:return Path(sys.executable).resolve().samefile(executable())
    except OSError:return False

def system_tool(name):
    if name not in ("schtasks.exe","icacls.exe"):raise ValueError("Unexpected system command")
    buf=ctypes.create_unicode_buffer(32768)
    n=ctypes.windll.kernel32.GetSystemDirectoryW(buf,len(buf))
    if not 0<n<len(buf):raise RuntimeError("Не найден System32.")
    return str(Path(buf.value)/name)

def command(name,*args,check=True):
    result=subprocess.run([system_tool(name),*args],cwd=str(Path(system_tool(name)).parent),
          capture_output=True,timeout=45,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if check and result.returncode:raise RuntimeError("Ошибка Windows Task Scheduler.")
    return result

def decode_output(raw):
    if raw.startswith((b"\xff\xfe",b"\xfe\xff")) or b"\x00" in raw[:80]:
        return raw.decode("utf-16" if raw.startswith((b"\xff\xfe",b"\xfe\xff")) else "utf-16-le",errors="replace")
    try:return raw.decode("utf-8-sig")
    except UnicodeError:return raw.decode("oem",errors="replace")

def task_names():
    raw=command("schtasks.exe","/Query","/FO","CSV","/NH").stdout
    return {row[0].strip().lstrip("\\").casefold() for row in csv.reader(io.StringIO(decode_output(raw))) if row}

def definition():
    result=command("schtasks.exe","/Query","/TN",TASK,"/XML",check=False)
    return result.stdout if result.returncode==0 else None

def expected_definition(raw,exe):
    if not raw:return False
    try:
        tree=ElementTree.fromstring(decode_output(raw))
        ns={"t":"http://schemas.microsoft.com/windows/2004/02/mit/task"}
        actions=tree.find("t:Actions",ns)
        if actions is None or len(actions)!=1:return False
        entry=actions[0]
        if not entry.tag.endswith("}Exec"):return False
        command_text=(entry.findtext("t:Command","",ns) or "").strip().strip('"')
        arguments=(entry.findtext("t:Arguments","",ns) or "").strip()
        user=tree.findtext("t:Principals/t:Principal/t:UserId","",ns).casefold()
        return Path(command_text).absolute()==Path(exe).absolute() and arguments==PREFIX+"worker" and user in ("s-1-5-18","system","nt authority\\system")
    except (ValueError,TypeError,ElementTree.ParseError):return False

def receipt():
    if os.name!="nt":return {}
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,REGISTRY,0,winreg.KEY_READ|winreg.KEY_WOW64_64KEY) as key:
            out={}
            for name in ("Installed","IntervalHours","CheckedUTC","State","Servers","Successful","Failed"):
                try:out[name]=winreg.QueryValueEx(key,name)[0]
                except OSError:pass
            return out
    except OSError:return {}

def write_receipt(**fields):
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE,REGISTRY,0,winreg.KEY_SET_VALUE|winreg.KEY_WOW64_64KEY) as key:
        for name,value in fields.items():
            winreg.SetValueEx(key,name,0,winreg.REG_DWORD if type(value) is int else winreg.REG_SZ,value)

def status():
    if os.name!="nt":return {"component":False,"installed":False,"verified":False,"source":"unsupported"}
    data=receipt();component=installed();verified=False
    if component:
        try:verified=expected_definition(definition(),executable())
        except (OSError,RuntimeError,subprocess.SubprocessError):pass
    return {"component":component,"installed":verified or data.get("Installed")==1,"verified":verified,
            "source":"task_scheduler" if verified else "admin_receipt" if data.get("Installed")==1 else "unknown",
            "interval_hours":data.get("IntervalHours"),"status":data.get("State"),"checked_utc":data.get("CheckedUTC"),
            "servers":data.get("Servers"),"successful":data.get("Successful"),"failed":data.get("Failed")}

def validate_payload(payload):
    from linkvideo_vpn_helper.services.vpn_background_service import _validate_settings
    from linkvideo_vpn_helper.services.vpn_sheets_sync import SPREADSHEET_ID
    _validate_settings(payload)
    if payload.get("spreadsheet_id")!=SPREADSHEET_ID:raise ValueError("Unexpected spreadsheet")
    key=payload["google_service_account"]
    if key.get("token_uri") not in ("https://oauth2.googleapis.com/token","https://accounts.google.com/o/oauth2/token") or key.get("universe_domain","googleapis.com")!="googleapis.com":
        raise ValueError("Unexpected Google credentials endpoint")
    if payload.get("routeros_port")!=8728 or not isinstance(payload.get("routeros_timeout"),(int,float)) or not 1<=payload["routeros_timeout"]<=30:
        raise ValueError("Unexpected RouterOS parameters")
    if any(len(str(payload.get(k,"")))>512 for k in ("routeros_username","routeros_password")) or len(str(key["private_key"]))>16000:
        raise ValueError("Oversize credentials")
    # Restrict Google credential configuration to supported service-account fields.
    fields=("type","project_id","private_key_id","private_key","client_email","client_id","auth_uri","token_uri","auth_provider_x509_cert_url","client_x509_cert_url","universe_domain")
    payload["google_service_account"]={k:key[k] for k in fields if k in key}
    return payload

def stage(credentials,google,servers,spreadsheet_id,interval):
    from linkvideo_vpn_helper.services.vpn_background_service import _pack_settings
    payload=validate_payload({"routeros_username":str(credentials.username),"routeros_password":str(credentials.password),
      "routeros_port":int(credentials.port),"routeros_timeout":float(credentials.timeout),"google_service_account":dict(google),
      "servers":list(dict.fromkeys(servers)),"spreadsheet_id":spreadsheet_id,"interval_hours":interval})
    folder=known_folder(0x1c)/DIRECTORY/"staging"
    folder.mkdir(parents=True,exist_ok=True)
    if not no_reparse(folder) or not no_reparse(folder.parent):raise RuntimeError("Каталог staging не подтверждён.")
    file=folder/("pending-"+uuid.uuid4().hex+".json")
    try:
        with file.open("x",encoding="utf-8") as out:json.dump(_pack_settings(payload),out)
        return file
    except Exception:
        file.unlink(missing_ok=True);raise

def elevate(action,staged=None):
    if action not in ALLOWED_ACTIONS or not installed():raise RuntimeError(MESSAGES[3])
    class Info(ctypes.Structure):
        _fields_=[("cbSize",wintypes.DWORD),("fMask",ctypes.c_ulong),("hwnd",wintypes.HWND),("lpVerb",wintypes.LPCWSTR),
         ("lpFile",wintypes.LPCWSTR),("lpParameters",wintypes.LPCWSTR),("lpDirectory",wintypes.LPCWSTR),("nShow",ctypes.c_int),
         ("hInstApp",wintypes.HINSTANCE),("lpIDList",ctypes.c_void_p),("lpClass",wintypes.LPCWSTR),("hkeyClass",wintypes.HKEY),
         ("dwHotKey",wintypes.DWORD),("hIcon",wintypes.HANDLE),("hProcess",wintypes.HANDLE)]
    args=[PREFIX+action]
    if action=="enable":
        if staged is None:raise RuntimeError(MESSAGES[10])
        args.append(str(staged))
    info=Info();info.cbSize=ctypes.sizeof(info);info.fMask=0x40;info.lpVerb="runas"
    info.lpFile=str(executable());info.lpParameters=subprocess.list2cmdline(args);info.lpDirectory=str(root());info.nShow=0
    hr=ctypes.windll.ole32.CoInitializeEx(None,2)
    try:
        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):raise RuntimeError("Запуск компонента отменён или Windows отказала.")
        try:
            if ctypes.windll.kernel32.WaitForSingleObject(info.hProcess,240000)!=0:raise RuntimeError("Результат операции Windows неизвестен. Проверьте планировщик.")
            code=wintypes.DWORD()
            if not ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess,ctypes.byref(code)):raise RuntimeError("Результат Windows не подтверждён.")
            if code.value:raise RuntimeError(MESSAGES.get(code.value,"Операция Windows не подтверждена."))
        finally:ctypes.windll.kernel32.CloseHandle(info.hProcess)
    finally:
        if hr in (0,1):ctypes.windll.ole32.CoUninitialize()

def state_dir():
    folder=root()/"state"
    if folder.exists() and not no_reparse(folder):raise RuntimeError(MESSAGES[12])
    folder.mkdir(exist_ok=True)
    if not no_reparse(folder):raise RuntimeError(MESSAGES[12])
    # Directory is inside administrator-owned Program Files; additionally
    # remove inheritance so only SYSTEM/Administrators may read machine-DPAPI blobs.
    command("icacls.exe",str(folder),"/inheritance:r","/grant:r","*S-1-5-18:(OI)(CI)F","*S-1-5-32-544:(OI)(CI)F")
    return folder

@contextmanager
def locked(folder):
    import msvcrt
    path=folder/"operation.lock"
    if path.exists() and not no_reparse(path):raise RuntimeError(MESSAGES[12])
    with path.open("a+b") as handle:
        handle.seek(0);handle.write(b"0");handle.flush();handle.seek(0)
        try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise BlockingIOError(MESSAGES[19]) from None
        try:yield
        finally:
            handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)

def read_staged(file):
    from linkvideo_vpn_helper.services.vpn_background_service import _unpack_settings
    p=Path(file)
    if not p.is_absolute() or not re.fullmatch(r"pending-[0-9a-f]{32}\.json",p.name) or p.parent.name!="staging" or p.parent.parent.name!=DIRECTORY or not no_reparse(p):
        raise ValueError("Invalid staged configuration")
    with p.open("rb") as f: raw=f.read(256*1024+1)
    if len(raw)>256*1024:raise ValueError("Oversize staged configuration")
    return validate_payload(_unpack_settings(json.loads(raw)))

def config_read(folder):
    from linkvideo_vpn_helper.services.vpn_background_service import _unpack_settings
    path=folder/"config.json"
    if not no_reparse(path) or path.stat().st_size>256*1024:raise ValueError("Invalid local configuration")
    return validate_payload(_unpack_settings(json.loads(path.read_text(encoding="utf-8"))))

def save_config(folder,payload):
    from linkvideo_vpn_helper.services.vpn_background_service import _pack_settings
    target=folder/"config.json";temp=folder/("config-"+uuid.uuid4().hex+".tmp")
    if target.exists() and not no_reparse(target):raise RuntimeError(MESSAGES[12])
    try:
        with temp.open("x",encoding="utf-8") as f:json.dump(_pack_settings(payload),f)
        os.replace(temp,target)
    finally:temp.unlink(missing_ok=True)

def enable(folder,payload):
    names=task_names()
    if LEGACY.casefold() in names:return 18
    before=definition() if TASK.casefold() in names else None
    if TASK.casefold() in names and not expected_definition(before,executable()):return 21
    target=folder/"config.json"
    if target.exists() and not no_reparse(target):return 12
    previous=target.read_bytes() if target.exists() else None
    if previous is not None and len(previous)>256*1024:return 12
    interval=payload["interval_hours"];schedule=["/SC","DAILY","/MO","1"] if interval==24 else ["/SC","HOURLY","/MO",str(interval)]
    # Explicit consent to automatic emergency backups; no surprise /Run.
    # The existing config is restored if registration/readback fails.
    try:
        save_config(folder,payload)
        action=subprocess.list2cmdline([str(executable()),PREFIX+"worker"])
        command("schtasks.exe","/Create","/TN",TASK,*schedule,"/RU","SYSTEM","/RL","HIGHEST","/TR",action,"/F")
        if not expected_definition(definition(),executable()):raise RuntimeError("Task action not confirmed")
        write_receipt(Installed=1,IntervalHours=interval,State="scheduled",CheckedUTC="",Servers=len(payload["servers"]),Successful=0,Failed=0)
        return 0
    except Exception:
        # Remove our newly created task before removing its config; restore
        # existing definition using only the trusted prior XML if applicable.
        try:
            if before:
                xml=folder/("rollback-"+uuid.uuid4().hex+".xml")
                try:
                    xml.write_bytes(before)
                    command("schtasks.exe","/Create","/TN",TASK,"/XML",str(xml),"/F")
                finally:xml.unlink(missing_ok=True)
            else:
                command("schtasks.exe","/Delete","/TN",TASK,"/F",check=False)
            if previous is None:target.unlink(missing_ok=True)
            else:
                temp=folder/("rollback-"+uuid.uuid4().hex+".tmp");temp.write_bytes(previous);os.replace(temp,target)
        except Exception:pass
        return 14

def disable(folder):
    names=task_names()
    if TASK.casefold() in names:
        if not expected_definition(definition(),executable()):return 21
        command("schtasks.exe","/End","/TN",TASK,check=False)
        command("schtasks.exe","/Delete","/TN",TASK,"/F")
        if TASK.casefold() in task_names():return 14
    target=folder/"config.json"
    if target.exists() and not no_reparse(target):return 12
    target.unlink(missing_ok=True)
    write_receipt(Installed=0,IntervalHours=12,State="disabled",CheckedUTC="",Servers=0,Successful=0,Failed=0)
    return 0

def run_worker(folder):
    payload=config_read(folder)
    if LEGACY.casefold() in task_names():return 18
    from linkvideo_vpn_helper.services.vpn_sheets_retention_compat import install_vpn_sheets_retention_compat
    from linkvideo_vpn_helper.services.vpn_sheets_resilience import install_vpn_sheets_resilience
    from linkvideo_vpn_helper.services.vpn_sheets_operator_view import install_vpn_sheets_operator_view
    from linkvideo_vpn_helper.services.vpn_sheets_sync import GoogleSheetsBackend,VPNSheetsSyncService
    from linkvideo_vpn_helper.services.vpn_service import VPNService,SessionCredentials
    install_vpn_sheets_retention_compat();install_vpn_sheets_resilience();install_vpn_sheets_operator_view()
    creds=SessionCredentials(payload["routeros_username"],payload["routeros_password"],payload["routeros_port"],payload["routeros_timeout"])
    backend=GoogleSheetsBackend(payload["google_service_account"],spreadsheet_id=payload["spreadsheet_id"])
    sync=VPNSheetsSyncService(VPNService(),backend);success=failed=0
    def record(state):
        write_receipt(State=state,CheckedUTC=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),Servers=len(payload["servers"]),Successful=success,Failed=failed)
    record("running")
    for host in payload["servers"]:
        try:
            result=sync.sync_server(host,creds,source="LinkVideo.Helper 4 explicit emergency schedule",initiator="scheduled-sync")
            if result.success:success+=1
            else:failed+=1
        except Exception:failed+=1
        record("running")
    record("ok" if not failed else "partial" if success else "failed")
    return 0 if not failed else 1

def dispatch_cli(args):
    if not args or not args[0].startswith(PREFIX):return None
    if not is_runner():return 3
    action=args[0][len(PREFIX):]
    if action=="check-installation" and len(args)==1:return 0
    if not ctypes.windll.shell32.IsUserAnAdmin():return 3
    if action not in (*ALLOWED_ACTIONS,"worker","installer-check") or len(args)!=(2 if action=="enable" else 1):return 3
    # Do not consult user-provided proxy/CA configuration in privileged context.
    for key in list(os.environ):
        if key.upper() in ("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","NO_PROXY","REQUESTS_CA_BUNDLE","CURL_CA_BUNDLE","SSL_CERT_FILE","SSL_CERT_DIR","GOOGLE_APPLICATION_CREDENTIALS"):
            os.environ.pop(key,None)
    try:
        if action=="installer-check":return 25 if TASK.casefold() in task_names() else 0
        if action=="verify":
            if not expected_definition(definition(),executable()):return 21
            payload=config_read(root()/"state")
            write_receipt(Installed=1,IntervalHours=payload["interval_hours"]);return 0
        if action=="run":
            if LEGACY.casefold() in task_names():return 18
            if not expected_definition(definition(),executable()):return 21
            config_read(root()/"state");command("schtasks.exe","/Run","/TN",TASK);return 0
        payload=None
        if action=="enable":
            try:payload=read_staged(args[1])
            except Exception:return 10
        folder=state_dir()
        with locked(folder):
            if action=="enable":return enable(folder,payload)
            if action=="disable":return disable(folder)
            if action=="worker":
                if not expected_definition(definition(),executable()):return 21
                return run_worker(folder)
    except BlockingIOError:return 19
    except Exception:return 22
    return 3
