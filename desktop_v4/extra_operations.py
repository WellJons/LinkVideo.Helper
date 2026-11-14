"""Additional migration adapters; secrets never appear in normal renderer data."""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import os
import base64
import hashlib
import json
import re
import subprocess
import shutil

METHODS=frozenset(("copy_vpn","backup","delete_preview","backup_status","sheets_load","deleted_search","restore_preview","sheets_preview","cloud_connect","activity","camera_inspect","b2o_read","b2o_create_preview","billing_recipient","billing_transfer_preview","templates_import","templates_attach","templates_images","templates_detach","winbox","registry_list","registry_update"))
class Extended:
    def __init__(self,ops):
        self.o=ops;self.cloud=None;self.sheets=None;self.deleted={};self.recipients={}
    def backup_service(self):
        from linkvideo_vpn_helper.services.vpn_backup_service import VPNBackupService
        class PrivateBackup(VPNBackupService):
            @staticmethod
            def default_root():
                return Path(os.environ.get("LV4_DATA_DIR") or (Path.home()/".linkvideo-helper-v4"))/"VPN_Backups"
        return PrivateBackup()
    def sheets_service(self):
        from desktop_v4.operations import InputError
        if self.sheets is None: raise InputError("Выберите JSON-ключ аварийного хранилища в разделе Восстановление.")
        return self.sheets
    def restore_service(self):
        from linkvideo_vpn_helper.services.vpn_restore_service import VPNRestoreService
        return VPNRestoreService(self.o.w.service,self.sheets_service())
    @staticmethod
    def destructive_enabled():
        from desktop_v4.operations import InputError
        if os.environ.get("LV4_ENABLE_DESTRUCTIVE")!="1": raise InputError("Удаление и восстановление отключены до отдельной полевой проверки.")
    def delete_revision(self,h,login):
        from desktop_v4.operations import InputError
        svc=self.o.w.service
        data=svc.fetch_config_snapshot(h,self.o.w.session)
        secrets=data.get("secrets",[]);profiles=data.get("profiles",[]);rules=data.get("nat_rules",[])
        target=[r for r in secrets if r.get("name")==login]
        if len(target)!=1: raise InputError("Не удалось однозначно определить PPP-учётку.")
        target=target[0];name=target.get("profile")
        if name!=login or not target.get(".id"):
            raise InputError("Профиль не выделен этой учётке. Автоматическое удаление запрещено.")
        matches=[r for r in profiles if r.get("name")==name]
        if len(matches)!=1 or not matches[0].get(".id"): raise InputError("Профиль не подтверждён.")
        remote=svc._get_client_remote_address(target,matches[0])
        if not remote: raise InputError("VPN IP не определён.")
        for other in secrets:
            if other.get("name")==login: continue
            profile=next((r for r in profiles if r.get("name")==other.get("profile")),None)
            if other.get("profile")==name or svc._get_client_remote_address(other,profile)==remote:
                raise InputError("Общий профиль или IP. Автоматическое удаление запрещено.")
        if any(r.get("name")==login for r in data.get("actives",[])): raise InputError("Клиент в сети. Сначала проверьте активную сессию.")
        own=[r for r in rules if svc._get_rule_remote(r)==remote]
        if any(r.get("comment")!=login or not r.get(".id") or r.get("chain")!="dstnat" or r.get("action")!="dst-nat" for r in own):
            raise InputError("Неоднозначные NAT-правила. Автоматическое удаление запрещено.")
        # Compare the exact records returned by the deletion method with
        # the validated set. Hash privately; never return raw secrets.
        narrow=svc.fetch_client_snapshot(h,self.o.w.session,login)
        ids=lambda rows: sorted(str(x.get(".id","")) for x in rows)
        if ids(narrow.get("nat_rules",[]))!=ids(own) or ids(narrow.get("secrets",[]))!=[target[".id"]]:
            raise InputError("Снимки конфигурации расходятся. Повторите проверку.")
        return hashlib.sha256(json.dumps([target,matches[0],sorted(own,key=lambda r:r[".id"])],sort_keys=True,default=str).encode()).hexdigest()
    def dispatch(self,m,a,cancel,emit):
        from desktop_v4.operations import InputError,text,number
        o=self.o;svc=o.w.service;creds=o.w.session
        if m in ("registry_list","registry_update"):
            if o.w.registry is None: raise InputError("Реестр недоступен.")
            registry=o.w.registry
            if m=="registry_update":
                o.admin_only()
                act=text(a,"action",16);h=registry.validate_host(text(a,"host",253))
                rows={r.host:r for r in registry.all()}
                if act=="trust":
                    registry.add(h,text(a,"country",50,False) or None)
                    o.w.trusted_custom.add(h)
                elif act=="enable" and h in rows: registry.set_enabled(h,True)
                elif act=="disable" and h in rows: registry.set_enabled(h,False)
                elif act=="remove" and h in rows and not rows[h].builtin:
                    registry.remove(h);o.w.trusted_custom.discard(h)
                else: raise InputError("Недопустимое изменение реестра.")
                registry.settings.sync();o.w.refresh_hosts()
            return [{"host":r.host,"country":r.country,"builtin":r.builtin,"enabled":r.enabled,"trusted":r.builtin or r.host in o.w.trusted_custom} for r in registry.all()]
        if m=="copy_vpn":
            c=o.get_client(o.host(a),text(a,"login"))
            return c.copy_text() # Main process writes clipboard; never returned by generic RPC.
        if m=="winbox":
            o.admin_only()
            host=o.host(a)
            paths=(shutil.which("winbox64.exe"),shutil.which("winbox.exe"),r"C:\Program Files\Winbox\winbox64.exe",r"C:\Program Files (x86)\Winbox\winbox.exe",r"C:\Winbox\winbox64.exe")
            exe=next((p for p in paths if p and Path(p).is_file()),None)
            if exe is None: raise InputError("WinBox не найден. Установите его или добавьте в PATH.")
            # Do not expose the work password through the OS command line.
            subprocess.Popen([exe,host],close_fds=True)
            return {"opened":True}
        if m=="backup":
            o.admin_only()
            hosts=list(o.w.hosts) if a.get("all") is True else [o.host(a)]
            def progress(done,total,h):emit("Резервное копирование "+h,round(done*100/max(total,1)))
            result=self.backup_service().backup_all(hosts,creds,progress,cancel)
            return {"folder":str(result.folder),"success":result.success_count,"failed":result.failure_count,
                    "servers":[{"server":r.server,"ok":r.ok} for r in result.servers]}
        if m=="backup_status":
            o.admin_only();return {"folder":str(self.backup_service().default_root())}
        if m=="delete_preview":
            o.admin_only();self.destructive_enabled();o.writes_enabled()
            h=o.host(a);login=text(a,"login")
            if text(a,"confirm",400)!=h+"/"+login: raise InputError("Введите точное server/login для проверки удаления.")
            revision=self.delete_revision(h,login)
            return o.stage("delete",(h,login),f"Удалить VPN {h}/{login} после FULL backup",revision)
        if m=="sheets_load":
            o.admin_only()
            path=Path(text(a,"path",2048))
            if not path.is_absolute() or path.suffix.lower()!=".json" or path.stat().st_size>128*1024:
                raise InputError("Выберите JSON service account.")
            data=json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data,dict) or data.get("type")!="service_account" or data.get("token_uri") not in ("https://oauth2.googleapis.com/token","https://accounts.google.com/o/oauth2/token"):
                raise InputError("Это не поддерживаемый Google service account.")
            from linkvideo_vpn_helper.services.vpn_sheets_sync import GoogleSheetsBackend
            self.sheets=GoogleSheetsBackend(data)
            self.deleted={}
            return {"configured":True}
        if m=="deleted_search":
            o.admin_only()
            rows=self.restore_service().search_deleted(text(a,"query",128),list(o.w.hosts))
            rows=[r for r in rows if r.server in o.w.hosts]
            self.deleted={(r.server,r.login):r for r in rows}
            return [{k:getattr(r,k) for k in ("server","login","password_saved","remote_address","deleted_at","ports")} for r in rows]
        if m=="restore_preview":
            o.admin_only();o.writes_enabled();self.destructive_enabled()
            h=o.host(a);login=text(a,"login")
            row=self.deleted.get((h,login))
            if not row or not row.password_saved: raise InputError("Найдите подтверждённую удалённую учётку с recovery-данными.")
            return o.stage("restore",(h,login),f"Восстановить VPN {h}/{login} после FULL backup")
        if m=="sheets_preview":
            o.admin_only();self.sheets_service();h=o.host(a)
            return o.stage("sheets",(h,),f"Аварийная выгрузка конфигурации {h} в Google Sheets.")
        if m=="cloud_connect":
            o.admin_only()
            from linkvideo_vpn_helper.services.cloud_vpnsync import CloudVPNSyncClient,CloudServerConfig
            host=text(a,"host",253)
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}",host): raise InputError("Укажите только DNS-имя или IPv4 облачного сервера.")
            password=a.get("password")
            if not isinstance(password,str) or not password or len(password)>512: raise InputError("Введите пароль облачного аккаунта.")
            config=CloudServerConfig(host=host,port=number(a,"port",1,65535),username=text(a,"username"),password=password,use_tls=a.get("tls") is True,remember=False)
            if not config.use_tls and a.get("allow_http") is not True: raise InputError("HTTP не шифруется. Используйте HTTPS или явно разрешите HTTP внутри доверенной сети.")
            self.cloud=None
            client=CloudVPNSyncClient(o.settings);client.set_config(config,save=False);client.test_connection();self.cloud=client
            return {"connected":True}
        if m=="activity":
            o.admin_only()
            if self.cloud is None: raise InputError("Подключите отдельный облачный аккаунт.")
            rows=self.cloud.activity(200,login=text(a,"login",128,False),source=text(a,"source",80,False))
            return [{k:str(row.get(k) or "")[:160] for k in ("created_at","timestamp","action","server","login","source","username","actor","success")} for row in rows if isinstance(row,dict)]
        if m=="camera_inspect":
            from linkvideo_vpn_helper.services.camera_diagnostics import inspect_camera
            b2o=o.archives().b2o
            c=inspect_camera(b2o,text(a,"camera"),o.operator(a),with_preview=False,with_network=True)
            return {k:getattr(c,k) for k in ("camera_id","server","ip","rtsp_port","ping_text","tcp_text","operator","name","status","serial","account_id","archive_depth","model","port_source","rtsp_host","last_stream")}
        if m=="b2o_read":
            from linkvideo_vpn_helper.services.b2o_client_admin import B2OOperatorReadOnly
            manager=B2OOperatorReadOnly(o.archives().b2o)
            c=manager.read_client(text(a,"country",32),number(a,"id",1,10**15))
            return {k:str(c.get(k) or "")[:200] for k in ("id_account","login","client_name","account_type","phone")}
        if m=="b2o_create_preview":
            from linkvideo_vpn_helper.services.b2o_client_admin import B2OClientDraft
            o.archives()
            o.writes_enabled()
            country=text(a,"country",32)
            password=a.get("password")
            if not isinstance(password,str) or len(password)>128: raise InputError("Введите пароль клиента.")
            draft=B2OClientDraft(country,text(a,"name",200),text(a,"login",100),password,text(a,"phone",30,False),"1",True,country!="Россия")
            draft.verified_payload()
            return o.stage("b2o",(draft,),f"B2O: создать {draft.login}, {draft.country}, обычный аккаунт с биллингом.")
        if m=="billing_recipient":
            rows=o.crm().search_clients(text(a,"query",100))
            self.recipients={r.id:r for r in rows}
            return [asdict(r) for r in rows]
        if m=="billing_transfer_preview":
            c=o.source_client();rid=text(a,"recipient",32);camera=text(a,"camera",32)
            r=self.recipients.get(rid)
            if not r or r.id==c.id or r.country!=c.country or not any(s.id==camera and s.camera_id==camera for s in c.services):
                raise InputError("Проверьте владельца камеры и получателя из той же страны.")
            return o.stage("transfer",(camera,r),f"Передать камеру {camera}: {c.login} → {r.login}, только CRM.",o.billing_revision(c))
        if m=="templates_import":
            store=o.template_store();path=Path(text(a,"path",2048))
            if not path.is_absolute() or path.suffix.lower()!=".docx" or path.stat().st_size>20*1024*1024: raise InputError("Выберите DOCX до 20 МБ.")
            return {"count":len(store.import_docx_many(path,text(a,"category",70,False)))}
        if m=="templates_attach":
            path=Path(text(a,"path",2048));tid=number(a,"id",1,2**31-1)
            if not path.is_absolute(): raise InputError("Выберите изображение.")
            o.template_store().attach_image(tid,path)
            return {"ok":True}
        if m=="templates_images":
            tid=number(a,"id",1,2**31-1)
            mime={".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".webp":"image/webp",".gif":"image/gif"}
            items=[]
            for i,p in o.template_store().images(tid)[:12]:
                if p.suffix.lower() in mime and p.stat().st_size<=5*1024*1024:
                    items.append({"id":i,"src":"data:"+mime[p.suffix.lower()]+";base64,"+base64.b64encode(p.read_bytes()).decode("ascii")})
            return items
        if m=="templates_detach":
            o.template_store().detach_image(number(a,"image",1,2**31-1),number(a,"id",1,2**31-1))
            return {"ok":True}
        raise InputError("Неизвестное действие.")
    def execute(self,kind,p,revision,cancel,emit):
        from desktop_v4.operations import InputError
        o=self.o;svc=o.w.service;creds=o.w.session
        if kind in ("delete","restore"):
            o.admin_only();self.destructive_enabled();h,login=p
            if kind=="delete" and self.delete_revision(h,login)!=revision: raise InputError("Конфигурация изменилась.")
            emit("Проверка FULL backup")
            result=self.backup_service().backup_one(h,creds,prefix="BEFORE_"+kind.upper())
            if not result.ok or cancel.is_set(): raise InputError("FULL backup не подтверждён. Операция не начата.")
            if kind=="delete":
                if self.delete_revision(h,login)!=revision: raise InputError("Конфигурация изменилась после backup.")
                svc.delete_client(h,creds,login)
                if svc.get_client(h,creds,login) is not None: raise InputError("Удаление не подтверждено.")
                return {"deleted":True}
            return asdict(self.restore_service().restore(h,creds,login))
        if kind=="sheets":
            o.admin_only()
            from linkvideo_vpn_helper.services.vpn_sheets_sync import VPNSheetsSyncService
            h,=p
            result=VPNSheetsSyncService(svc,self.sheets_service()).sync_server(h,creds,source="Helper 4 explicit emergency export")
            return {"server":result.server,"success":result.success,"clients":result.clients,"added":result.added,"changed":result.changed,"deleted":result.deleted}
        if kind=="b2o":
            from linkvideo_vpn_helper.services.b2o_client_admin import B2OOperatorClientManager
            draft,=p
            return asdict(B2OOperatorClientManager(o.archives().b2o).create_client(draft))
        if kind=="transfer":
            camera,r=p;g=o.crm();c=g.refresh_client(o.source_client())
            if o.billing_revision(c)!=revision: raise InputError("Карточка CRM изменилась.")
            _,fresh=g.transfer_camera(c,camera,r,move_in_vc=False)
            o.client=fresh or g.refresh_client(c)
            return asdict(o.client)
        raise InputError("Неизвестная операция.")
