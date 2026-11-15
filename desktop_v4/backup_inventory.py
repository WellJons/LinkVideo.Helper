"""Read-only verification of local FULL backups, never project secrets or raw contents."""
from pathlib import Path
import hashlib
import json
import re

def safe_child(root,name):
    if not isinstance(name,str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,240}",name) or name in (".",".."):
        raise ValueError("Недопустимое имя резервной копии.")
    root=Path(root).resolve()
    p=root/name
    if p.is_symlink() or p.resolve().parent!=root or (hasattr(p,"is_junction") and p.is_junction()):
        raise ValueError("Путь резервной копии не подтверждён.")
    return p

def manifest(folder):
    path=safe_child(folder,"manifest.json")
    if not path.is_file() or path.stat().st_size>2*1024*1024:raise ValueError("Manifest недоступен.")
    data=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data,dict) or data.get("format")!="LinkVideo.Helper VPN backup manifest" or data.get("format_version")!=1:raise ValueError("Неподдерживаемый manifest.")
    if not isinstance(data.get("files"),list) or len(data["files"])>500:raise ValueError("Некорректный список файлов.")
    return data

def public(folder):
    data=manifest(folder)
    return {"id":folder.name,"created":str(data.get("created_at_utc") or "")[:50],
            "success":len(data.get("servers_ok") or []),"failed":len(data.get("servers_failed") or []),
            "files":len(data["files"])}

def inventory(root):
    root=Path(root)
    if not root.is_dir():return []
    out=[]
    for folder in sorted(root.iterdir(),key=lambda p:p.name,reverse=True)[:200]:
        try:
            p=safe_child(root,folder.name)
            if p.is_dir():out.append(public(p))
        except (ValueError,OSError,TypeError):continue
    return sorted(out,key=lambda x:x["created"],reverse=True)

def verify(root,name,cancel,emit):
    folder=safe_child(root,name)
    data=manifest(folder);rows=[];seen=set()
    for i,item in enumerate(data["files"]):
        if cancel.is_set():break
        emit("Проверка резервной копии",round(i*100/max(1,len(data["files"]))))
        result={"name":f"Файл {i+1}","ok":False}
        try:
            if not isinstance(item,dict):raise ValueError()
            file=safe_child(folder,item["name"])
            if file.name in seen:raise ValueError()
            seen.add(file.name);result["name"]=file.name
            if not file.is_file() or file.stat().st_size!=item["size"] or not re.fullmatch(r"[a-f0-9]{64}",item["sha256"]):raise ValueError()
            digest=hashlib.sha256()
            with file.open("rb") as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b""):
                    if cancel.is_set():break
                    digest.update(chunk)
            result["ok"]=not cancel.is_set() and digest.hexdigest()==item["sha256"]
        except (KeyError,ValueError,OSError,TypeError):pass
        rows.append(result)
    return {"id":name,"files":rows,"cancelled":cancel.is_set(),"verified":bool(rows) and len(rows)==len(data["files"]) and all(r["ok"] for r in rows) and not cancel.is_set(),"complete":bool(data.get("servers_ok")) and not data.get("servers_failed")}
