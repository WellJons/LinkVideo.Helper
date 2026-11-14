"""Explicit adapters for Helper services. No public HTTP or generic command execution."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, date
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event
import os
import re
import time
import secrets
from desktop_v4.extra_operations import Extended, METHODS as EXTRA_METHODS

class InputError(Exception):
    pass

def text(args,key,maxlen=128,required=True):
    v=args.get(key,"")
    if not isinstance(v,str) or len(v)>maxlen or (required and not v.strip()):
        raise InputError("Проверьте поле: "+key)
    return v.strip()

def number(args,key,lo,hi):
    v=args.get(key)
    if type(v) is not int or not lo<=v<=hi:
        raise InputError(f"{key}: допустимо от {lo} до {hi}")
    return v

def public_client(c):
    fields=("server","login","remote_address","ports","active_ports","disabled_ports","is_online","is_enabled","uptime","last_logged_out")
    out={k:getattr(c,k) for k in fields}
    out["port_conflicts"]={str(k):len(v) for k,v in getattr(c,"port_conflicts",{}).items()}
    out.update(rx_bytes=getattr(c,"rx_bytes",0),tx_bytes=getattr(c,"tx_bytes",0))
    return out

class MemorySettings:
    """Qt-compatible ephemeral settings, never persist B2O session or secrets."""
    def __init__(self): self.data={}
    def value(self,key,default=None,type=None):
        val=self.data.get(key,default)
        return type(val) if type is not None and val is not None else val
    def setValue(self,key,value): self.data[key]=value
    def remove(self,key):
        for k in list(self.data):
            if k==key or k.startswith(key+"/"): self.data.pop(k,None)
    def contains(self,key): return key in self.data
    def sync(self): pass

class Operations:
    METHODS=frozenset(("client_detail","create_preview","vpn_preview","commit","inactive","lv_status","lv_preview","admin_login","admin_logout","admin_status","billing_search","billing_select","billing_catalog","billing_history","billing_preview","archive_discover","archive_diagnose","archive_download","agent_search","agent_preview","templates_list","templates_save","templates_favorite","templates_delete","background_status")) | EXTRA_METHODS
    def __init__(self,workspace):
        self.w=workspace
        self.settings=MemorySettings()
        self.billing=None
        self.transport=None
        self.hits={}
        self.client=None
        self.archive=None
        self.discovery=None
        self.camera=None
        self.agent=None
        self.templates=None
        self.plan=None
        self.admin_name=""
        self.b2o_ready=False
        self.extra=Extended(self)

    def close(self):
        if self.transport: self.transport.close()
        self.__init__(self.w)

    def host(self,args):
        h=text(args,"host",253)
        if h not in self.w.hosts: raise InputError("Выберите доверенный VPN-сервер.")
        return h

    def operator(self,args):
        op=number(args,"operator",1,9999)
        if op not in (241,1721,1741): raise InputError("Выберите доступный кластер B2O.")
        return op

    def admin_only(self):
        if self.w.identity["role"]!="sysadmin": raise InputError("Действие доступно только системному администратору.")

    def writes_enabled(self):
        # First alpha intentionally requires explicit local environment opt-in.
        if os.environ.get("LV4_ENABLE_WRITES")!="1":
            raise InputError("Изменения выключены в тестовой сборке. Требуется отдельная проверка и разрешение.")

    def stage(self,action,params,label,revision=None):
        self.writes_enabled()
        token=secrets.token_urlsafe(24)
        self.plan=(token,time.monotonic()+120,action,params,revision)
        return {"token":token,"label":label,"expires_seconds":120}

    def get_client(self,host,login):
        c=self.w.service.get_client(host,self.w.session,login,include_port_conflicts=True)
        if c is None: raise InputError("Клиент не найден. Обновите поиск.")
        return c

    @staticmethod
    def client_revision(c):
        return (c.login,c.remote_address,getattr(c,"secret_id",""),tuple(sorted(c.ports)),tuple(sorted(c.disabled_ports)),c.is_enabled)

    def crm(self):
        if self.billing is None: raise InputError("Выполните независимый вход в CRM.")
        return self.billing

    def source_client(self):
        if self.client is None: raise InputError("Выберите клиента в CRM.")
        return self.client

    def archives(self):
        if not self.b2o_ready or self.archive is None: raise InputError("Выполните независимый вход в B2O.")
        return self.archive

    def template_store(self):
        if self.templates is None:
            from linkvideo_vpn_helper.services.support_templates import TemplateStore
            root=Path(os.environ.get("LV4_DATA_DIR") or (Path.home()/".linkvideo-helper-v4"))
            self.templates=TemplateStore(root/"support_templates")
        return self.templates

    def dispatch(self,method,a,cancel=None,emit=None):
        cancel=cancel or Event()
        emit=emit or (lambda *_:None)
        svc=self.w.service
        creds=self.w.session
        if method in EXTRA_METHODS:
            return self.extra.dispatch(method,a,cancel,emit)
        if method=="client_detail":
            return public_client(self.get_client(self.host(a),text(a,"login")))
        if method=="create_preview":
            self.writes_enabled()
            h=self.host(a)
            login=text(a,"login",64)
            if not re.fullmatch(r"[A-Za-z0-9_.-]+",login): raise InputError("Недопустимый VPN-логин.")
            ports=number(a,"ports",1,14);count=number(a,"count",1,20)
            return self.stage("create",(h,login,ports,count),f"Создать {count} VPN-учёток, по {ports} портов: {h} / {login}.")
        if method=="vpn_preview":
            h=self.host(a);login=text(a,"login");act=text(a,"action",32)
            if act not in ("add","enable","disable","port_enable","port_disable","disconnect","remove_port","recreate_port","password"):
                raise InputError("Операция недоступна.")
            self.writes_enabled()
            if act not in ("add","disconnect"): self.admin_only()
            c=self.get_client(h,login)
            value=None
            if act=="add": value=number(a,"count",1,14)
            if act in ("port_enable","port_disable","remove_port","recreate_port"):
                value=number(a,"port",1,65535)
                if value not in c.ports: raise InputError("Порт не принадлежит клиенту.")
                if c.port_conflicts.get(value): raise InputError("Обнаружен конфликт NAT. Операция заблокирована.")
            if act=="password":
                value=text(a,"password",128)
                if len(value)<8: raise InputError("Пароль должен быть не короче 8 символов.")
            labels={"add":"Добавить порты","enable":"Включить VPN","disable":"Отключить VPN","port_enable":"Включить порт","port_disable":"Отключить порт","disconnect":"Разорвать сессию","remove_port":"Удалить порт","recreate_port":"Пересоздать порт","password":"Сменить пароль"}
            return self.stage("vpn",(h,login,act,value),f"{labels[act]}: {h} / {login}"+(f" / {value}" if type(value) is int else ""),self.client_revision(c))
        if method=="inactive":
            h=self.host(a);days=number(a,"days",1,3650)
            rows=svc.list_lifecycle_clients(h,creds,days,True)
            return [{k:getattr(r,k) for k in ("server","login","last_logged_out","is_enabled","remote_address","lifecycle_state","is_online","lifecycle_source")} for r in rows]
        if method in ("lv_status","lv_preview"):
            from linkvideo_vpn_helper.services.vpn_automation_service import VPNAutomationService
            h=self.host(a); self.admin_only()
            if method=="lv_status":
                s=VPNAutomationService().get_status(h,creds)
                return {**asdict(s),"installed":s.installed,"paused":s.paused}
            action=text(a,"action",32)
            if action not in ("install","start","pause","seed","quarantine_on","quarantine_off"): raise InputError("Неизвестное LV-действие.")
            labels={"install":"Установить/обновить LV","start":"Запустить LV","pause":"Приостановить LV","seed":"Инициализировать жизненный цикл","quarantine_on":"Включить карантин","quarantine_off":"Отключить карантин"}
            return self.stage("lv",(h,action),labels[action]+": "+h+". Сначала будет проверен FULL backup.")
        if method=="admin_status":
            return {"username":self.admin_name,"b2o":self.b2o_ready,"crm":self.billing is not None}
        if method=="admin_logout":
            self.close();return {"username":"","b2o":False,"crm":False}
        if method=="admin_login":
            self.close()
            username=text(a,"username",128);password=a.get("password")
            if not isinstance(password,str) or not password or len(password)>512: raise InputError("Введите пароль.")
            target=text(a,"target",12)
            if target not in ("crm","b2o","both"): raise InputError("Выберите систему.")
            errors=[]
            if target in ("b2o","both"):
                try:
                    from linkvideo_vpn_helper.services.archive_service import ArchiveService
                    self.archive=ArchiveService(self.settings)
                    self.archive.b2o.login(username,password)
                    self.b2o_ready=bool(self.archive.b2o.token())
                    if not self.b2o_ready: raise ValueError()
                except Exception:
                    self.archive=None;self.b2o_ready=False;self.settings.remove("b2o")
                    errors.append("B2O: вход не подтверждён.")
            if target in ("crm","both"):
                try:
                    from linkvideo_vpn_helper.services.billing_auth_contract import BillingLoginRequest
                    from linkvideo_vpn_helper.services.billing_login_probe import check_billing_login
                    from linkvideo_vpn_helper.services.billing_token_transport import BillingTokenTransport
                    from linkvideo_vpn_helper.services.billing_editable_gateway import BillingEditableGateway
                    token=check_billing_login(BillingLoginRequest(username,password)).token
                    self.transport=BillingTokenTransport(token,"auto")
                    self.billing=BillingEditableGateway(self.transport)
                    # Verify a real authenticated read, not just existence of token.
                    self.billing.all_tariffs()
                except Exception:
                    if self.transport: self.transport.close()
                    self.transport=self.billing=None;errors.append("CRM: вход или защищённый запрос не подтверждён.")
            self.admin_name=username if self.b2o_ready or self.billing else ""
            return {"username":self.admin_name,"b2o":self.b2o_ready,"crm":self.billing is not None,"errors":errors}
        if method=="billing_search":
            g=self.crm();self.client=None;self.hits={};self.plan=None
            rows=g.search_clients(text(a,"query",100))
            self.hits={r.id:r for r in rows}
            return [asdict(r) for r in rows]
        if method=="billing_select":
            g=self.crm();hit=self.hits.get(text(a,"id",32))
            if hit is None: raise InputError("Выберите запись из актуального поиска.")
            self.client=g.find_client_by_hit(hit);self.plan=None
            return asdict(self.client)
        if method=="billing_catalog":
            g=self.crm()
            if a.get("scope")=="client": return asdict(g.reference_catalog(self.source_client()))
            return asdict(g.all_tariffs())
        if method=="billing_history":
            return list(self.crm().camera_history(self.source_client(),text(a,"camera",32)))
        if method=="billing_preview":
            self.writes_enabled();g=self.crm();c=self.source_client()
            act=text(a,"action",24);camera=text(a,"camera",32)
            if not any(s.id==camera and s.camera_id==camera for s in c.services): raise InputError("Камера не принадлежит клиенту.")
            if act not in ("tariff","status"): raise InputError("Неизвестное действие CRM.")
            try: when=date.fromisoformat(text(a,"date",10))
            except ValueError: raise InputError("Выберите дату.")
            if when<date.today(): raise InputError("Дата не может быть в прошлом.")
            comment=text(a,"comment",500,False)
            value=text(a,"tariff",32) if act=="tariff" else number(a,"status",0,1)
            if act=="tariff":
                from linkvideo_vpn_helper.services.billing_catalog import filter_tariffs_for_client
                options=filter_tariffs_for_client(g.reference_catalog(c).tariffs,c)
                opt=next((t for t in options if t.id==value),None)
                if opt is None: raise InputError("Тариф недоступен этому клиенту.")
                detail=opt.name
            else: detail="Включить" if value else "Отключить"
            return self.stage("billing",(act,camera,value,when,comment),f"CRM: {c.login} / камера {camera} / {detail} / {when}",self.billing_revision(c))
        if method in ("archive_discover","archive_diagnose"):
            archive=self.archives();self.discovery=None
            cam=text(a,"camera",128);operator=self.operator(a)
            try: start=datetime.fromisoformat(text(a,"start",25));end=datetime.fromisoformat(text(a,"end",25))
            except ValueError: raise InputError("Введите период.")
            if start.tzinfo or end.tzinfo or not 0<(end-start).total_seconds()<=7*86400: raise InputError("Период от 1 секунды до 7 суток, локальное время камеры.")
            d=archive.discover(cam,operator,start,end,lambda title,_:emit(title),cancel)
            self.discovery=d
            out=self.archive_public(d)
            if method=="archive_diagnose":
                from linkvideo_vpn_helper.services.archive_diagnosis_engine import ArchiveDiagnosisEngine
                ids=a.get("compare",[]);server_ids=a.get("server_compare",[])
                if any(not isinstance(group,list) or len(group)>8 or any(not isinstance(x,str) or not x.strip() or len(x)>128 for x in group) for group in (ids,server_ids)): raise InputError("Не более 8 камер в каждой группе.")
                def compare(group):
                    out=[]
                    clean=list(dict.fromkeys(x.strip() for x in group if x.strip()!=cam))
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        tasks={pool.submit(archive.diagnose_epoch,x,operator,d.requested_start,d.requested_end,fast=False,cancel_event=cancel):x for x in clean}
                        for task in as_completed(tasks):
                            x=tasks[task]
                            try: out.append((x,task.result(),None))
                            except Exception: out.append((x,None,RuntimeError("Не удалось проверить камеру")))
                    return out
                addr=compare(ids);other=compare(server_ids)
                dx=ArchiveDiagnosisEngine().analyze(d,addr,other)
                out["diagnosis"]={"title":dx.title,"summary":dx.summary,"confidence":dx.confidence_text,"side":dx.side_text,"cautions":dx.cautions}
                out["compared"]={"address":len(addr),"server":len(other),"failed":sum(row[1] is None for row in addr+other)}
            return out
        if method=="archive_download":
            archive=self.archives()
            if self.discovery is None or not self.discovery.has_downloadable_archive: raise InputError("Сначала найдите доступный архив.")
            path=Path(text(a,"output",2048))
            if not path.is_absolute() or path.suffix.lower()!=".mp4" or path.exists(): raise InputError("Выберите новый MP4-файл без перезаписи.")
            mode=text(a,"mode",20)
            if mode not in ("ffmpeg","ffmpeg_no_audio","curl"): raise InputError("Выберите метод загрузки.")
            archive._lv_archive_download_method=mode
            def progress(p):
                if isinstance(p,dict):
                    val=p.get("value")
                    emit("Скачивание архива",val if type(val) in (int,float) else None)
            result=archive.download(self.discovery,path,progress,cancel)
            return {"output":str(result.output),"downloaded_seconds":result.downloaded_duration,"failed_seconds":result.failed_duration,"partial":result.partial,"error_count":len(result.errors)}
        if method=="agent_search":
            from linkvideo_vpn_helper.services.agent_service import AgentService
            self.agent=AgentService(self.settings,creds)
            self.camera=self.agent.find_camera(text(a,"serial",96));self.plan=None
            return {**asdict(self.camera),"version":self.camera.version_text}
        if method=="agent_preview":
            self.writes_enabled()
            if self.camera is None: raise InputError("Сначала найдите камеру.")
            action=text(a,"action",24)
            if action=="timezone": value=number(a,"value",-12,14)
            elif action=="rotation":
                value=number(a,"value",0,270)
                if value not in (0,90,180,270): raise InputError("Выберите угол 0, 90, 180 или 270.")
            elif action=="bitrate": value=number(a,"value",128,16384)
            else: raise InputError("Неизвестная настройка.")
            return self.stage("agent",(action,value),f"Камера {self.camera.serial_number}: {action} = {value}",self.camera.serial_number)
        if method=="templates_list":
            store=self.template_store()
            return {"categories":store.categories(),"items":[asdict(row) for row in store.list(text(a,"query",256,False),text(a,"category",70,False),a.get("favorites") is True)]}
        if method=="templates_save":
            store=self.template_store();tid=a.get("id")
            if tid is not None and (type(tid) is not int or tid<1): raise InputError("Некорректный шаблон.")
            return {"id":store.save(text(a,"title",180),text(a,"body",50000),text(a,"category",70,False),tid)}
        if method in ("templates_favorite","templates_delete"):
            store=self.template_store();tid=number(a,"id",1,2**31-1)
            if method=="templates_favorite": store.favorite(tid,a.get("selected") is True)
            else: store.delete(tid)
            return {"ok":True}
        if method=="background_status":
            from linkvideo_vpn_helper.services.vpn_background_service import read_status, read_task_receipt
            self.admin_only()
            status=read_status();receipt=read_task_receipt()
            # Never serialize task secrets or arbitrary status payload.
            return {"installed":bool(receipt.get("installed")),"interval_hours":receipt.get("interval_hours"),"status":str(status.get("state") or status.get("status") or "")[:80]}
        if method=="commit":
            token=text(a,"token",128)
            plan=self.plan;self.plan=None
            if plan is None or not secrets.compare_digest(token,plan[0]) or time.monotonic()>plan[1]:
                raise InputError("Подтверждение устарело. Повторите подготовку.")
            self.writes_enabled()
            _,_,kind,params,revision=plan
            if cancel.is_set(): raise InputError("Операция отменена.")
            return self.execute(kind,params,revision,cancel,emit)
        raise InputError("Неизвестная операция.")

    @staticmethod
    def billing_revision(c):
        return (c.id,tuple((s.id,s.tariff_name,s.status) for s in c.services))

    @staticmethod
    def archive_public(d):
        return {"camera":d.camera.label,"server":d.camera.server,"timezone":d.camera.timezone_offset,
                "requested_seconds":d.requested_duration,"covered_seconds":d.covered_duration,
                "coverage":d.coverage_percent,"downloadable":d.has_downloadable_archive,
                "exact_timeline":bool(d.slices),"checked":len(d.checked_hosts),"error_count":len(d.errors),
                "gaps":[{"start":x.start,"end":x.end} for x in d.gaps],
                "slices":[{"start":x.start,"end":x.end,"host":x.host,"source":x.source} for x in d.slices]}

    def execute(self,kind,p,revision,cancel,emit):
        svc=self.w.service;creds=self.w.session
        if kind in ("delete","restore","sheets","b2o","transfer"):
            return self.extra.execute(kind,p,revision,cancel,emit)
        if kind=="create":
            h,login,ports,count=p
            rows=svc.create_clients_batch(h,creds,login,ports,count,cancel_event=cancel)
            return {"clients":[public_client(c) for c in rows]}
        if kind=="vpn":
            h,login,act,value=p
            if act not in ("add","disconnect"): self.admin_only()
            current=self.get_client(h,login)
            if self.client_revision(current)!=revision: raise InputError("Данные клиента изменились. Повторите подготовку.")
            if act=="add": result=svc.add_ports(h,creds,login,value)
            elif act in ("enable","disable"): result=svc.set_secret_enabled(h,creds,login,act=="enable")
            elif act in ("port_enable","port_disable"): result=svc.set_port_enabled(h,creds,login,value,act=="port_enable")
            elif act=="disconnect": return {"disconnected":svc.disconnect_client_session(h,creds,login)}
            elif act=="password": result=svc.set_password(h,creds,login,value)
            elif act in ("remove_port","recreate_port"):
                if current.port_conflicts.get(value): raise InputError("Обнаружен конфликт NAT.")
                if act=="remove_port": result=svc.remove_port(h,creds,login,value)
                else: result=svc.recreate_port(h,creds,login,value)
            return public_client(result)
        if kind=="lv":
            self.admin_only();h,action=p
            from linkvideo_vpn_helper.services.vpn_backup_service import VPNBackupService
            from linkvideo_vpn_helper.services.vpn_automation_service import VPNAutomationService
            emit("Резервное копирование")
            backup=self.extra.backup_service().backup_one(h,creds)
            if not backup.ok or cancel.is_set(): raise InputError("FULL backup не подтверждён; изменение не начато.")
            lv=VPNAutomationService()
            if action=="install": lv.install_or_update(h,creds)
            elif action in ("start","pause"): lv.set_automation_enabled(h,creds,action=="start")
            elif action=="seed": lv.seed_lifecycle(h,creds)
            else: lv.set_quarantine_enabled(h,creds,action=="quarantine_on")
            s=lv.get_status(h,creds)
            return {**asdict(s),"installed":s.installed,"paused":s.paused}
        if kind=="billing":
            from linkvideo_vpn_helper.services.billing_gateway import TariffChange
            act,camera,value,when,comment=p
            g=self.crm();c=g.refresh_client(self.source_client())
            if self.billing_revision(c)!=revision: raise InputError("Карточка CRM изменилась. Повторите подготовку.")
            if act=="tariff": g.change_tariff(TariffChange(c.id,camera,value,when,comment),client=c)
            else: g.set_camera_status(c,camera,value,when,comment)
            self.client=g.refresh_client(c)
            return asdict(self.client)
        if kind=="agent":
            action,value=p
            if self.camera is None or self.camera.serial_number!=revision: raise InputError("Камера изменилась.")
            camera=self.agent.find_camera(revision)
            if camera.server!=self.camera.server: raise InputError("Камера сменила сервер. Повторите поиск.")
            if action=="timezone":
                replies=self.agent.configure_time(camera,value)
                return {"responses":[int(r.status_code) for r in replies]}
            if action=="rotation": before,after=self.agent.set_image_rotation(camera,self.agent.get_image_enhance(camera),value)
            else: before,after=self.agent.set_bitrate(camera,self.agent.get_video_streams(camera),value)
            return {"before":before,"after":after}
        raise InputError("Неизвестное подтверждение.")
