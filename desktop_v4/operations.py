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
    out.update(rx_bytes=getattr(c,"rx_bytes",0),tx_bytes=getattr(c,"tx_bytes",0),tunnel_source_ip=getattr(c,"tunnel_source_ip",""),port_nat_bytes=getattr(c,"port_nat_bytes",{}),port_nat_packets=getattr(c,"port_nat_packets",{}))
    return out

class MemorySettings:
    """Only explicit non-secret archive preferences persist in the 4.0 profile."""
    PREFIXES=("archive/extra_cluster_enabled/","archive/custom_servers/","vpn_create/start_ip/")
    def __init__(self,workspace=None): self.data={};self.workspace=workspace
    def disk(self,key):
        registry=getattr(self.workspace,"registry",None)
        return registry.settings if registry is not None and key.startswith(self.PREFIXES) else None
    def value(self,key,default=None,type=None):
        disk=self.disk(key)
        if disk is not None: return disk.value(key,default,type) if type is not None else disk.value(key,default)
        val=self.data.get(key,default)
        return type(val) if type is not None and val is not None else val
    def setValue(self,key,value):
        disk=self.disk(key)
        if disk is not None: disk.setValue(key,value);disk.sync()
        else: self.data[key]=value
    def remove(self,key):
        disk=self.disk(key)
        if disk is not None: disk.remove(key);disk.sync()
        for k in list(self.data):
            if k==key or k.startswith(key+"/"): self.data.pop(k,None)
    def contains(self,key):
        disk=self.disk(key)
        return disk.contains(key) if disk is not None else key in self.data
    def sync(self): pass

class Operations:
    METHODS=frozenset(("client_detail","client_refresh","traffic_sample","camera_ports","inactive_fleet","inactive_batch_preview","create_preview","vpn_preview","commit","inactive","lv_status","lv_preview","lv_fleet","lv_batch_preview","create_suggest","create_ip","create_check","camera_lookup","admin_login","admin_logout","admin_status","admin_retry","billing_refresh","billing_filter","billing_search","billing_select","billing_catalog","billing_history","billing_preview","billing_bulk_preview","billing_camera_image","create_auto","archive_discover","archive_diagnose","archive_download","archive_settings","archive_settings_update","agent_search","agent_settings","agent_preview","templates_list","templates_migrate","server_fleet","templates_save","templates_favorite","templates_delete","background_status","background_preview","background_verify_admin")) | EXTRA_METHODS
    def __init__(self,workspace):
        self.w=workspace
        self.settings=MemorySettings(workspace)
        self.billing=None
        self.transport=None
        self.hits={}
        self.client=None
        self.archive=None
        self.discovery=None
        self.download_token=None
        self.camera=None
        self.agent=None
        self.agent_values={}
        self.created_batch=set()
        self.templates=None
        self.plan=None
        self.admin_name=""
        self.admin_credentials=None
        self.work_admin_credentials=None
        self.inactive_records={}
        self.catalog_snapshot=None
        self.catalog_scope=None
        self.b2o_ready=False
        self.uncertain=set()
        self.extra=Extended(self)

    def clear_crm(self):
        if self.transport:
            try: self.transport.close()
            except Exception: pass
        self.transport=self.billing=self.client=None
        self.hits={};self.extra.recipients={};self.plan=None
        self.catalog_snapshot=self.catalog_scope=None

    def clear_b2o(self):
        self.archive=self.discovery=None
        self.download_token=None
        self.extra.camera_report=None;self.extra.camera_operator=None;self.b2o_ready=False;self.admin_credentials=None;self.plan=None
        self.settings.remove("b2o")

    def clear_admin(self):
        self.clear_crm();self.clear_b2o()
        self.admin_name="";self.work_admin_credentials=None
        self.camera=self.agent=None;self.agent_values={}

    def admin_state(self,errors=None):
        result={"username":self.admin_name,"b2o":self.b2o_ready,"crm":self.billing is not None}
        if errors is not None: result["errors"]=errors
        return result

    def connect_admin(self,username,password,target):
        from types import SimpleNamespace
        old=self.work_admin_credentials
        # Different credentials invalidate both services. Explicit retry with
        # the same verified work account touches only the requested service.
        if old is None or old.username!=username or old.password!=password:
            self.clear_admin()
        else:
            if target in ("crm","both"): self.clear_crm()
            if target in ("b2o","both"): self.clear_b2o()
        errors=[]
        if target in ("b2o","both"):
            try:
                from linkvideo_vpn_helper.services.archive_service import ArchiveService
                self.archive=ArchiveService(self.settings)
                self.archive.b2o.login(username,password)
                self.b2o_ready=bool(self.archive.b2o.token())
                if not self.b2o_ready: raise ValueError()
                self.admin_credentials=SimpleNamespace(username=username,password=password)
            except Exception:
                self.clear_b2o();errors.append("B2O: вход не подтверждён.")
        if target in ("crm","both"):
            try:
                from linkvideo_vpn_helper.services.billing_auth_contract import BillingLoginRequest
                from linkvideo_vpn_helper.services.billing_login_probe import check_billing_login
                from linkvideo_vpn_helper.services.billing_token_transport import BillingTokenTransport
                from linkvideo_vpn_helper.services.billing_editable_gateway import BillingEditableGateway
                token=check_billing_login(BillingLoginRequest(username,password)).token
                self.transport=BillingTokenTransport(token,"auto")
                self.billing=BillingEditableGateway(self.transport)
                self.billing.all_tariffs()
            except Exception:
                self.clear_crm();errors.append("CRM: вход или защищённый запрос не подтверждён.")
        if self.b2o_ready or self.billing is not None:
            self.admin_name=username
            self.work_admin_credentials=SimpleNamespace(username=username,password=password)
        else:
            self.admin_name="";self.work_admin_credentials=None
        return self.admin_state(errors)

    def catalog_public(self,a):
        from linkvideo_vpn_helper.services.billing_tariff_filter import archive_days_from_name, bitrate_mbps_from_name, select_tariffs
        snapshot=self.catalog_snapshot
        if snapshot is None: raise InputError("Сначала загрузите справочник тарифов.")
        country=text(a,"country",60,False)
        if self.catalog_scope=="client":
            country=self.source_client().country
        days=number(a,"days",0,9999) if "days" in a else 0
        speed=a.get("bitrate",0)
        if type(speed) not in (int,float) or not 0<=speed<=10000: raise InputError("Некорректный битрейт.")
        rows=select_tariffs(snapshot.tariffs,country=country,days=days,search=text(a,"query",200,False),bitrate_mbps=float(speed),account_type=text(a,"account_type",80,False))
        countries=list(getattr(snapshot,"countries",()) or ())
        if self.catalog_scope=="client": countries=[snapshot.country]
        codes={x.name:x.currency_code for x in countries}
        return {"scope":self.catalog_scope,"tariffs":[{**asdict(x),"price":str(x.price),"archive_days":archive_days_from_name(x.name),"bitrate_mbps":bitrate_mbps_from_name(x.name),"currency":codes.get(x.country,"")} for x in rows],
                "countries":[asdict(x) for x in countries],"account_types":sorted({x.account_type for x in snapshot.tariffs if x.account_type}),
                "archive_periods":[asdict(x) for x in getattr(snapshot,"archive_periods",())],"total":len(snapshot.tariffs)}

    def close(self):
        self.clear_admin()
        self.__init__(self.w)

    def host(self,args):
        h=text(args,"host",253)
        if h not in self.w.hosts: raise InputError("Выберите доверенный VPN-сервер.")
        return h

    def operator(self,args):
        op=number(args,"operator",1,9999)
        if op not in (241,1721,1741,1442,621): raise InputError("Выберите доступный кластер B2O.")
        if op in (1442,621):
            from linkvideo_vpn_helper.services.archive_service_core import B2OService
            if not B2OService(self.settings).is_operator_enabled(op): raise InputError("Включите дополнительный кластер в Настройках → Архив.")
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
        if not self.b2o_ready or self.archive is None or not self.archive.b2o.token(): raise InputError("Выполните независимый вход в B2O.")
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
        if method in ("archive_settings","archive_settings_update"):
            from linkvideo_vpn_helper.services.archive_service_core import B2OService
            service=B2OService(self.settings)
            if method=="archive_settings_update":
                action=text(a,"action",24)
                if action=="cluster":
                    key=text(a,"cluster",32)
                    if key not in {x[0] for x in service.EXTRA_CLUSTERS} or type(a.get("enabled")) is not bool: raise InputError("Неизвестный кластер.")
                    service.set_extra_cluster_enabled(key,a["enabled"])
                elif action in ("add_host","remove_host"):
                    self.admin_only()
                    operator=number(a,"operator",1,9999)
                    if operator not in service.OPERATOR_PROFILES: raise InputError("Неизвестный оператор.")
                    host=text(a,"host",253)
                    if action=="add_host": service.add_custom_archive_server(operator,host)
                    else: service.remove_custom_archive_server(operator,host)
                else: raise InputError("Неизвестная настройка архива.")
            return {"clusters":[{"key":k,"name":n,"operator":op,"enabled":service.extra_cluster_enabled(k)} for k,n,op in service.EXTRA_CLUSTERS],"hosts":[{"operator":op,"country":service.operator_country(op),"custom":service.custom_archive_servers(op)} for op in service.OPERATOR_PROFILES]}
        if method=="server_fleet":
            self.admin_only()
            hosts=list(self.w.hosts)
            results={};failed={}
            with ThreadPoolExecutor(max_workers=min(6,max(1,len(hosts)))) as pool:
                tasks={pool.submit(svc.analyze_server_quick,h,creds):h for h in hosts}
                completed=0
                for future in as_completed(tasks):
                    if cancel.is_set():
                        for f in tasks: f.cancel()
                        break
                    h=tasks[future]
                    try:
                        row=asdict(future.result())
                        results[h]={k:row.get(k) for k in ("server","cpu_load","memory_usage_percent","clients_total","clients_online","ports_total","ports_active")}
                    except Exception: failed[h]="Нет подтверждённых данных"
                    completed+=1;emit("Проверено серверов "+str(completed)+"/"+str(len(hosts)),round(completed*100/max(1,len(hosts))))
            return {"rows":[{"host":h,"country":self.w.hosts[h].country,"metrics":results.get(h),"error":failed.get(h)} for h in hosts],"checked":len(results)+len(failed),"cancelled":cancel.is_set()}
        if method=="client_detail":
            h=self.host(a);c=self.get_client(h,text(a,"login"))
            out=public_client(c)
            if c.ports:
                try: out["port_conflicts"]={str(k):len(v) for k,v in svc.inspect_port_conflicts(h,creds,c).items()};out["conflicts_verified"]=True
                except Exception: out["conflicts_verified"]=False
            return out
        if method=="client_refresh":
            h=self.host(a);login=text(a,"login")
            c=svc.get_client(h,creds,login,include_port_conflicts=False)
            if c is None:return {"found":False,"server":h,"login":login}
            return {"found":True,"client":public_client(c)}
        if method=="traffic_sample":
            h=self.host(a);login=text(a,"login")
            c=svc.get_client(h,creds,login)
            if c is None: raise InputError("Клиент не найден.")
            return {"server":h,"login":login,"online":bool(c.is_online),"rx":max(0,int(c.rx_bytes or 0)),"tx":max(0,int(c.tx_bytes or 0)),"uptime":str(c.uptime or ""),"interface":str(c.interface_name or ""),"sample_time":time.monotonic()}
        if method=="camera_lookup":
            from linkvideo_vpn_helper.services.camera_lookup import search_by_login
            if self.admin_credentials is None: raise InputError("Выполните независимый вход в Админку.")
            query=text(a,"query",100)
            rows=search_by_login(query,self.archives().b2o,self.admin_credentials)
            return [{k:getattr(c,k) for k in ("camera_id","operator_id","country","label","customer_login","status","tariff")} for c in rows][:200]
        if method=="camera_ports":
            from linkvideo_vpn_helper.services.vpn_camera_port_match import match_vpn_ports
            h=self.host(a);login=text(a,"login")
            b2o=self.archives().b2o
            if self.admin_credentials is None: raise InputError("Выполните независимый вход в Админку.")
            c=self.get_client(h,login)
            report=match_vpn_ports(c.login,tuple(c.ports),b2o,self.admin_credentials,vpn_server=h,override_login=text(a,"override",100,False),country=self.w.hosts[h].country)
            return {**asdict(report),"matched_camera_count":report.matched_camera_count}
        if method=="inactive_fleet":
            self.admin_only()
            days=number(a,"days",1,3650);hosts=list(self.w.hosts) if a.get("all") is True else [self.host(a)]
            rows=[];errors=[];self.inactive_records={}
            with ThreadPoolExecutor(max_workers=min(8,max(1,len(hosts)))) as pool:
                tasks={pool.submit(svc.list_lifecycle_clients,h,creds,days,a.get("unknown") is not False):h for h in hosts}
                checked=0
                for future in as_completed(tasks):
                    if cancel.is_set():
                        for x in tasks: x.cancel()
                        break
                    h=tasks[future]
                    try: rows.extend(future.result())
                    except Exception: errors.append(h)
                    checked+=1;emit("Проверено "+str(checked)+"/"+str(len(hosts)),round(checked*100/max(1,len(hosts))))
            if not cancel.is_set(): self.inactive_records={(c.server,c.login):c for c in rows if not c.is_online}
            return {"rows":[{**{k:getattr(c,k) for k in ("server","login","last_logged_out","is_enabled","remote_address","lifecycle_state","is_online","lifecycle_source")},"last_seen":c.last_logged_out_dt.isoformat() if c.last_logged_out_dt else None} for c in svc.sort_inactive_records(rows,"old")],"errors":errors,"total":len(hosts),"cancelled":cancel.is_set()}
        if method=="inactive_batch_preview":
            self.admin_only();self.writes_enabled()
            action=text(a,"action",16)
            if action not in ("enable","disable","delete"): raise InputError("Недопустимое массовое действие.")
            if action=="delete": self.extra.destructive_enabled()
            targets=a.get("targets")
            if not isinstance(targets,list) or not 1<=len(targets)<=50: raise InputError("Выберите от 1 до 50 учёток.")
            if action=="delete" and text(a,"confirm",50)!=f"УДАЛИТЬ {len(targets)}": raise InputError("Введите УДАЛИТЬ "+str(len(targets))+" для подтверждения списка.")
            prepared=[];seen=set()
            for i,item in enumerate(targets):
                if not isinstance(item,dict): raise InputError("Некорректная учётка.")
                h=self.host(item);login=text(item,"login")
                if (h,login) in seen or (h,login) not in self.inactive_records: raise InputError("Повторите проверку неактивных учёток.")
                seen.add((h,login))
                if cancel.is_set(): raise InputError("Операция отменена.")
                c=self.get_client(h,login)
                if c.is_online or (action!="delete" and c.is_enabled==(action=="enable")): raise InputError("Состояние "+login+" изменилось или уже соответствует действию. Повторите проверку.")
                scanned=self.inactive_records[(h,login)]
                if action=="delete" and (not scanned.last_logged_out_dt or c.last_logged_out!=scanned.last_logged_out or c.remote_address!=scanned.remote_address or c.is_enabled!=scanned.is_enabled): raise InputError("Активность или конфигурация "+login+" изменилась/неизвестна. Повторите проверку.")
                rev=self.extra.delete_revision(h,login) if action=="delete" else self.client_revision(c)
                prepared.append((h,login,rev))
                emit("Проверка учёток",round((i+1)*100/len(targets)))
            verb={"enable":"Включить","disable":"Отключить","delete":"Удалить"}[action]
            return self.stage("inactive_batch",(action,tuple(prepared)),f"{verb} {len(prepared)} неактивных VPN: "+", ".join(h+"/"+login for h,login,_ in prepared[:5])+("…" if len(prepared)>5 else "")+(". FULL backup каждой конфигурации перед удалением." if action=="delete" else ". Отдельные операции без автоматического отката."))
        if method=="create_auto":
            region=text(a,"country",50)
            hosts=[h for h,row in self.w.hosts.items() if row.country==region]
            if not hosts: raise InputError("Нет активных VPN-серверов выбранного региона.")
            emit("Сравниваю нагрузку VPN-серверов")
            return {"host":svc.pick_best_server_parallel(hosts,creds,cancel_event=cancel)}
        if method=="create_ip":
            from linkvideo_vpn_helper.services.vpn_address_allocator import DEFAULT_START_IP,validate_start_ip
            h=self.host(a);key="vpn_create/start_ip/"+h.lower()
            if a.get("start_ip") is not None:
                try:value=validate_start_ip(text(a,"start_ip",32))
                except ValueError as ex:raise InputError(str(ex))
                self.settings.setValue(key,value)
            else:
                try:value=validate_start_ip(self.settings.value(key,DEFAULT_START_IP,str))
                except ValueError:value=DEFAULT_START_IP
            return {"host":h,"start_ip":value}
        if method=="create_check":
            h=self.host(a);login=text(a,"login",64)
            if not re.fullmatch(r"[A-Za-z0-9_.-]+",login):raise InputError("Недопустимый VPN-логин.")
            report=self.w.search.search_exact_login(h,creds,login)
            if cancel.is_set():raise InputError("Операция отменена.")
            if report.errors or report.checked!=report.total or not report.total:return {"host":h,"login":login,"status":"unknown"}
            return {"host":h,"login":login,"status":"occupied" if report.matches else "free","suggestion":svc.suggest_next_login(h,creds,login) if report.matches else login}
        if method=="create_suggest":
            h=self.host(a);login=text(a,"login",64)
            if not re.fullmatch(r"[A-Za-z0-9_.-]+",login): raise InputError("Недопустимый VPN-логин.")
            return {"login":svc.suggest_next_login(h,creds,login)}
        if method=="create_preview":
            self.writes_enabled()
            h=self.host(a)
            login=text(a,"login",64)
            if not re.fullmatch(r"[A-Za-z0-9_.-]+",login): raise InputError("Недопустимый VPN-логин.")
            ports=number(a,"ports",1,14);count=number(a,"count",1,20)
            from linkvideo_vpn_helper.services.vpn_address_allocator import DEFAULT_START_IP, validate_start_ip
            key="vpn_create/start_ip/"+h.lower()
            if a.get("start_ip") is None:
                # The desktop creation form does not expose IP tuning. Preserve
                # each server's saved pool start rather than resetting it.
                try: start_ip=validate_start_ip(self.settings.value(key,DEFAULT_START_IP,str))
                except ValueError: start_ip=DEFAULT_START_IP
            else:
                try: start_ip=validate_start_ip(text(a,"start_ip",32))
                except ValueError as ex: raise InputError(str(ex))
                self.settings.setValue(key,start_ip)
            return self.stage("create",(h,login,ports,count,start_ip),f"Создать {count} VPN-учёток, по {ports} портов: {h} / {login}. Начальный IP: {start_ip}.")
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
                try: conflicts=svc.inspect_port_conflicts(h,creds,c)
                except Exception: raise InputError("Проверка конфликтов NAT не завершилась. Изменение заблокировано.")
                if conflicts.get(value): raise InputError("Обнаружен конфликт NAT. Операция заблокирована.")
            if act=="password":
                value=text(a,"password",128)
                if len(value)<8: raise InputError("Пароль должен быть не короче 8 символов.")
            labels={"add":"Добавить порты","enable":"Включить VPN","disable":"Отключить VPN","port_enable":"Включить порт","port_disable":"Отключить порт","disconnect":"Разорвать сессию","remove_port":"Удалить порт","recreate_port":"Пересоздать порт","password":"Сменить пароль"}
            return self.stage("vpn",(h,login,act,value),f"{labels[act]}: {h} / {login}"+(f" / {value}" if type(value) is int else ""),self.client_revision(c))
        if method=="inactive":
            self.admin_only()
            h=self.host(a);days=number(a,"days",1,3650)
            rows=svc.list_lifecycle_clients(h,creds,days,True)
            return [{k:getattr(r,k) for k in ("server","login","last_logged_out","is_enabled","remote_address","lifecycle_state","is_online","lifecycle_source")} for r in rows]
        if method in ("lv_fleet","lv_batch_preview"):
            from linkvideo_vpn_helper.services.vpn_automation_service import VPNAutomationService
            self.admin_only()
            lv=VPNAutomationService();hosts=list(self.w.hosts)
            if method=="lv_fleet":
                results={}
                with ThreadPoolExecutor(max_workers=min(6,max(1,len(hosts)))) as pool:
                    tasks={pool.submit(lv.get_status,h,creds):h for h in hosts}
                    for i,f in enumerate(as_completed(tasks)):
                        if cancel.is_set():
                            for task in tasks: task.cancel()
                            break
                        h=tasks[f]
                        try:
                            s=f.result();results[h]={**asdict(s),"installed":s.installed,"paused":s.paused,"state_text":s.state_text}
                        except Exception: results[h]={"server":h,"error":True}
                        emit("LV "+str(i+1)+"/"+str(len(hosts)),round((i+1)*100/max(1,len(hosts))))
                return {"rows":[results[h] for h in hosts if h in results],"total":len(hosts),"cancelled":cancel.is_set()}
            self.writes_enabled()
            action=text(a,"action",16)
            if action not in ("install","start","pause"): raise InputError("Недопустимое массовое действие LV.")
            selected=a.get("hosts")
            if not isinstance(selected,list) or not 1<=len(selected)<=100 or any(not isinstance(h,str) or h not in self.w.hosts for h in selected) or len(set(selected))!=len(selected):
                raise InputError("Выберите доверенные серверы.")
            label={"install":"Установить/обновить LV","start":"Запустить LV","pause":"Остановить LV"}[action]
            return self.stage("lv_batch",(action,tuple(selected)),label+": "+", ".join(selected)+". FULL backup перед каждым изменением; карантин отдельно не включается.")
        if method in ("lv_status","lv_preview"):
            from linkvideo_vpn_helper.services.vpn_automation_service import VPNAutomationService
            h=self.host(a); self.admin_only()
            if method=="lv_status":
                s=VPNAutomationService().get_status(h,creds)
                return {**asdict(s),"installed":s.installed,"paused":s.paused,"state_text":s.state_text}
            action=text(a,"action",32)
            if action not in ("install","start","pause","seed","quarantine_on","quarantine_off"): raise InputError("Неизвестное LV-действие.")
            labels={"install":"Установить/обновить LV","start":"Запустить LV","pause":"Приостановить LV","seed":"Инициализировать жизненный цикл","quarantine_on":"Включить карантин","quarantine_off":"Отключить карантин"}
            return self.stage("lv",(h,action),labels[action]+": "+h+". Сначала будет проверен FULL backup.")
        if method=="admin_status":
            return self.admin_state()
        if method=="admin_logout":
            self.clear_admin();return self.admin_state()
        if method=="admin_retry":
            creds=self.work_admin_credentials
            target=text(a,"target",12)
            if target not in ("crm","b2o"): raise InputError("Выберите CRM или B2O.")
            if creds is None: raise InputError("Сначала войдите в рабочую учётную запись CRM / B2O.")
            return self.connect_admin(creds.username,creds.password,target)
        if method=="admin_login":
            username=text(a,"username",128);password=a.get("password")
            if not isinstance(password,str) or not password or len(password)>512: raise InputError("Введите пароль.")
            target=text(a,"target",12)
            if target not in ("crm","b2o","both"): raise InputError("Выберите систему.")
            return self.connect_admin(username,password,target)
        if method=="billing_search":
            g=self.crm();self.client=None;self.hits={};self.plan=None;self.catalog_snapshot=self.catalog_scope=None;self.extra.recipients={}
            rows=g.search_clients(text(a,"query",100))
            self.hits={r.id:r for r in rows}
            return [asdict(r) for r in rows]
        if method=="billing_select":
            g=self.crm();hit=self.hits.get(text(a,"id",32))
            if hit is None: raise InputError("Выберите запись из актуального поиска.")
            self.client=None;self.plan=None;self.catalog_snapshot=self.catalog_scope=None;self.extra.recipients={}
            self.client=g.find_client_by_hit(hit)
            return asdict(self.client)
        if method=="billing_refresh":
            g=self.crm();current=self.source_client();self.plan=None;self.extra.recipients={};self.catalog_snapshot=self.catalog_scope=None
            self.client=g.refresh_client(current)
            return asdict(self.client)
        if method=="billing_catalog":
            g=self.crm();self.catalog_snapshot=self.catalog_scope=None
            scope="client" if a.get("scope")=="client" else "all"
            self.catalog_snapshot=g.reference_catalog(self.source_client()) if scope=="client" else g.all_tariffs()
            self.catalog_scope=scope
            return self.catalog_public({})
        if method=="billing_filter":
            self.crm();return self.catalog_public(a)
        if method=="billing_history":
            return list(self.crm().camera_history(self.source_client(),text(a,"camera",32)))
        if method=="billing_camera_image":
            import base64
            from linkvideo_vpn_helper.services.b2o_camera_preview import fetch_camera_preview
            c=self.source_client();camera=text(a,"camera",32)
            service=next((s for s in c.services if s.id==camera and s.camera_id==camera),None)
            if not service or not service.camera_vc_id: raise InputError("ID камеры для превью не подтверждён.")
            raw=fetch_camera_preview(c.country,service.camera_vc_id,self.archives().b2o.token())
            mime="image/jpeg" if raw.startswith(b"\xff\xd8") else "image/png" if raw.startswith(b"\x89PNG\r\n\x1a\n") else "image/webp" if raw[:4]==b"RIFF" and raw[8:12]==b"WEBP" else ""
            if not mime or len(raw)>5*1024*1024: raise InputError("B2O не вернул допустимое изображение.")
            return {"camera":camera,"src":"data:"+mime+";base64,"+base64.b64encode(raw).decode("ascii")}
        if method=="billing_bulk_preview":
            self.writes_enabled();g=self.crm();c=self.source_client()
            from linkvideo_vpn_helper.services.billing_catalog import filter_tariffs_for_client
            from linkvideo_vpn_helper.services.billing_gateway import validate_tariff_change, TariffOption
            ids=a.get("cameras")
            if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids): raise InputError("Выберите от 1 до 100 разных камер.")
            owned={s.id:s for s in c.services if s.id==s.camera_id}
            if not all(x in owned for x in ids): raise InputError("Одна из камер не принадлежит выбранному клиенту.")
            tariff=text(a,"tariff",32);when=date.fromisoformat(text(a,"date",10));comment=text(a,"comment",500,False)
            if when<date.today(): raise InputError("Дата не может быть в прошлом.")
            options=filter_tariffs_for_client(g.reference_catalog(c).tariffs,c)
            opt=next((x for x in options if x.id==tariff),None)
            if opt is None: raise InputError("Тариф недоступен клиенту.")
            changes=tuple(validate_tariff_change(c,owned[x],TariffOption(opt.id,opt.name),when,comment) for x in ids)
            if any((c.id,x.service_id,x.tariff_id,x.effective_date) in self.uncertain for x in changes): raise InputError("Есть неподтверждённое изменение. Сначала проверьте CRM.")
            return self.stage("billing_bulk",(changes,),f"CRM {c.login}: {len(changes)} камер → {opt.name}, {when}. Отдельные PUT, без автоматического отката.",self.billing_revision(c))
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
            else:
                service=next(s for s in c.services if s.id==camera)
                expected={"Активный":0,"Неактивный":1}.get(service.status)
                if expected is None or expected!=value: raise InputError("Статус неизвестен или не требует изменения. Обновите карточку CRM.")
                detail="Включить" if value else "Отключить"
            return self.stage("billing",(act,camera,value,when,comment),f"CRM: {c.login} / камера {camera} / {detail} / {when}",self.billing_revision(c))
        if method in ("archive_discover","archive_diagnose"):
            archive=self.archives();self.discovery=None;self.download_token=None
            cam=text(a,"camera",128);operator=archive.b2o.resolve_operator_id(cam,self.operator(a))
            try: start=datetime.fromisoformat(text(a,"start",25));end=datetime.fromisoformat(text(a,"end",25))
            except ValueError: raise InputError("Введите период.")
            if start.tzinfo or end.tzinfo or not 0<(end-start).total_seconds()<=7*86400: raise InputError("Период от 1 секунды до 7 суток, локальное время камеры.")
            d=archive.discover(cam,operator,start,end,lambda title,_:emit(title),cancel)
            if cancel.is_set(): raise InputError("Операция отменена.")
            out=self.archive_public(d)
            if method=="archive_discover" and d.has_downloadable_archive:
                from desktop_v4.archive_report import archive_filename
                self.discovery=d;self.download_token=secrets.token_urlsafe(24)
                out["download_token"]=self.download_token;out["filename"]=archive_filename(d)
            if method=="archive_diagnose":
                from linkvideo_vpn_helper.services.archive_diagnosis_engine import ArchiveDiagnosisEngine
                ids=a.get("compare",[]);server_ids=a.get("server_compare",[])
                if any(not isinstance(group,list) or len(group)>8 or any(not isinstance(x,str) or not x.strip() or len(x)>128 for x in group) for group in (ids,server_ids)): raise InputError("Не более 8 камер в каждой группе.")
                def canonical(raw):
                    try: return str(archive.b2o.normalize_camera_id(raw,operator)[0])
                    except Exception: return str(raw).strip()
                main_id=canonical(d.camera.camera_id)
                address_ids={canonical(x) for x in ids}
                auto_error=False;debug={"pages":0,"found_total":0,"online_same_server":0,"error_count":0}
                limit=number(a,"compare_limit",1,15) if "compare_limit" in a else 15
                for raw in ids+server_ids:
                    if archive.b2o.resolve_operator_id(raw,operator)!=operator: raise InputError("Для сравнения выберите камеры того же оператора.")
                if not server_ids:
                    emit("Подбираю другие камеры vcore")
                    try:
                        candidates,raw_debug=archive.b2o.cameras_on_server(operator,d.camera.server,min(30,limit+len(address_ids)+1))
                        debug={k:max(0,int(raw_debug.get(k,0) or 0)) for k in ("pages","found_total","online_same_server")};debug["error_count"]=len(raw_debug.get("errors") or [])
                        auto_error=bool(debug["error_count"])
                        server_ids=[str(row.get("id_camera") or "") for row in candidates if isinstance(row,dict)]
                    except Exception:
                        auto_error=True;server_ids=[];debug["error_count"]=1
                def compare(group,fast=False):
                    out=[]
                    clean=list(dict.fromkeys(canonical(x) for x in group if x and canonical(x)!=main_id and (not fast or canonical(x) not in address_ids)))[:limit if fast else 8]
                    with ThreadPoolExecutor(max_workers=6 if fast else 4) as pool:
                        tasks={pool.submit(archive.diagnose_epoch,x,operator,d.requested_start,d.requested_end,fast=fast,cancel_event=cancel):x for x in clean}
                        for task in as_completed(tasks):
                            if cancel.is_set():
                                for f in tasks: f.cancel()
                                break
                            x=tasks[task]
                            try:
                                item=task.result()
                                if fast and archive.b2o.normalize_vcore_host(item.camera.server)!=archive.b2o.normalize_vcore_host(d.camera.server):
                                    raise ValueError("Сервер камеры изменился")
                                out.append((x,item,None))
                            except Exception: out.append((x,None,RuntimeError("Не удалось проверить камеру")))
                    return out
                emit("Сравниваю камеры адреса");addr=compare(ids)
                if cancel.is_set(): raise InputError("Операция отменена.")
                emit("Проверяю массовость на vcore");other=compare(server_ids,True)
                if cancel.is_set(): raise InputError("Операция отменена.")
                dx=ArchiveDiagnosisEngine().analyze(d,addr,other)
                out["diagnosis"]={"title":dx.title,"summary":dx.summary,"confidence":dx.confidence_text,"side":dx.side_text,"cautions":dx.cautions}
                out["compared"]={"address":len(addr),"server":len(other),"failed":sum(row[1] is None for row in addr+other),"auto_error":auto_error}
                out["diagnosis"]["evidence"]=[asdict(x) for x in dx.evidence]
                out["diagnosis"]["gaps"]=[{"start":x.start,"end":x.end,"title":x.title,"summary":x.summary} for x in dx.gaps] if d.slices else []
                from desktop_v4.archive_report import comparison,make_reports
                out["address_compare"]=comparison(d,addr);out["server_compare"]=comparison(d,other);out["comparison_debug"]=debug
                out["reports"]=make_reports(d,dx,out["address_compare"],out["server_compare"],debug)
            return out
        if method=="archive_download":
            archive=self.archives()
            if self.discovery is None or not self.discovery.has_downloadable_archive or not self.download_token or text(a,"token",100)!=self.download_token: raise InputError("Результат поиска устарел. Повторите поиск архива.")
            path=Path(text(a,"output",2048))
            if not path.is_absolute() or path.suffix.lower()!=".mp4" or path.exists(): raise InputError("Выберите новый MP4-файл без перезаписи.")
            mode=text(a,"mode",20)
            if mode not in ("ffmpeg","ffmpeg_no_audio","curl"): raise InputError("Выберите метод загрузки.")
            manual=text(a,"manual",4000,False)
            if manual:
                if mode=="curl": raise InputError("Ручная команда применяется только к FFmpeg.")
                from desktop_v4.archive_command import validate_command
                try: manual=validate_command(manual)
                except ValueError as ex: raise InputError(str(ex))
            archive._lv_archive_download_method=mode
            archive._lv_archive_manual_command=manual
            def progress(p):
                if isinstance(p,dict):
                    val=p.get("value")
                    emit(str(p.get("title") or "Скачивание архива")[:100],val if type(val) in (int,float) else None)
            try: result=archive.download(self.discovery,path,progress,cancel)
            finally: archive._lv_archive_manual_command=""
            return {"output":str(result.output),"downloaded_seconds":result.downloaded_duration,"failed_seconds":result.failed_duration,"partial":bool(result.partial or self.discovery.coverage_percent<99 or (self.discovery.slices and self.discovery.gaps)),"error_count":len(result.errors),"failed_slices":[{"start":s.start,"end":s.end} for s in result.failed_slices]}
        if method=="agent_search":
            from linkvideo_vpn_helper.services.agent_service import AgentService
            self.camera=None;self.agent=None;self.plan=None;self.agent_values={}
            self.agent=AgentService(self.settings,creds)
            self.camera=self.agent.find_camera(text(a,"serial",96))
            return {**asdict(self.camera),"version":self.camera.version_text}
        if method=="agent_settings":
            if self.camera is None: raise InputError("Сначала найдите камеру.")
            action=text(a,"action",24,False) or "all"
            if action not in ("rotation","bitrate","all"): raise InputError("Выберите настройку.")
            result={"serial":self.camera.serial_number,"errors":[]}
            for key in ("rotation","bitrate") if action=="all" else (action,):
                self.agent_values.pop(key,None)
                try:
                    if key=="rotation":
                        value=int(self.agent.get_image_enhance(self.camera)["ImageRotation"])
                        if value not in (0,1,2,3): raise ValueError()
                    else:
                        value=int(self.agent._main_video_encode_info(self.agent.get_video_streams(self.camera))["BitRate"])
                        if not 1<=value<=100000: raise ValueError()
                    result[key]=value;self.agent_values[key]=value
                except Exception:
                    result["errors"].append("Не подтверждено: "+key)
            return result
        if method=="agent_preview":
            self.writes_enabled()
            if self.camera is None: raise InputError("Сначала найдите камеру.")
            action=text(a,"action",24)
            if action=="timezone": value=number(a,"value",-12,14)
            elif action=="rotation": value=number(a,"value",0,3)
            elif action=="bitrate": value=number(a,"value",1,100000)
            else: raise InputError("Неизвестная настройка.")
            if action!="timezone" and action not in self.agent_values: raise InputError("Сначала прочитайте текущую настройку именно этой камеры.")
            old=self.agent_values.get(action)
            label={0:"Обычное изображение",1:"Вертикальное отражение",2:"Горизонтальное отражение",3:"Поворот на 180°"}.get(value) if action=="rotation" else str(value)
            return self.stage("agent",(action,value,old),f"Камера {self.camera.serial_number} / {self.camera.server}: {action} = {label}",(self.camera.serial_number,self.camera.server))
        if method=="templates_migrate":
            from desktop_v4.template_migration import migrate_templates
            path=Path(text(a,"path",2048))
            if not path.is_absolute(): raise InputError("Выберите файл базы старой версии.")
            try: return migrate_templates(path,self.template_store(),cancel)
            except ValueError as ex: raise InputError(str(ex))
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
        if method=="background_preview":
            from desktop_v4 import background
            self.admin_only();self.writes_enabled()
            action=text(a,"action",24)
            if action not in ("enable","disable","run"):raise InputError("Неизвестное действие фоновой задачи.")
            if not background.installed():raise InputError("Установите отдельный Background-компонент 4.0 в Program Files.")
            interval=None
            if action=="enable":
                self.extra.sheets_service();interval=number(a,"interval",6,24)
                if interval not in background.INTERVALS:raise InputError("Выберите 6, 12 или 24 ч.")
                if not self.w.hosts:raise InputError("Нет доверенных серверов.")
            targets=tuple(self.w.hosts) if action=="enable" else ()
            label={"enable":f"Явно включить аварийное расписание Sheets каждые {interval} ч. Серверов: {len(targets)}. Таблица будет содержать PPP-пароли и recovery-снимки. PostgreSQL остаётся основной базой.","disable":"Отключить только фоновое задание 4.0; данные Sheets и 3.x не изменяются.","run":"Запустить существующее аварийное задание 4.0 сейчас. Будет отправлена полная конфигурация в Google Sheets."}[action]
            return self.stage("background",(action,interval,targets),label)
        if method=="background_verify_admin":
            from desktop_v4 import background
            self.admin_only();emit("Запрошена проверка Windows с UAC")
            try:background.elevate("verify")
            except RuntimeError as ex:raise InputError(str(ex)) from None
            return background.status()
        if method=="background_status":
            from desktop_v4 import background
            from linkvideo_vpn_helper.services.vpn_background_service import read_status, read_task_receipt, task_inspection
            self.admin_only()
            status=read_status();receipt=read_task_receipt();inspection=task_inspection()
            # Never serialize task secrets or arbitrary status payload.
            return {"v4":background.status(),"legacy":{"installed":bool(inspection.get("installed")),"verified":bool(inspection.get("verified")),"source":inspection.get("source"),"interval_hours":receipt.get("interval_hours"),"status":str(status.get("state") or status.get("status") or "")[:80],"checked_utc":str(status.get("checked_utc") or "")[:60],"servers":status.get("servers"),"successful":status.get("successful"),"failed":status.get("failed")},"mode":"manual_emergency_by_default","sheets_configured":self.extra.sheets is not None}
        if method=="commit":
            token=text(a,"token",128)
            plan=self.plan;self.plan=None
            if plan is None or not secrets.compare_digest(token,plan[0]) or time.monotonic()>plan[1]:
                raise InputError("Подтверждение устарело. Повторите подготовку.")
            self.writes_enabled()
            _,_,kind,params,revision=plan
            if cancel.is_set(): raise InputError("Операция отменена.")
            try:
                result=self.execute(kind,params,revision,cancel,emit)
            except Exception:
                self.extra.audit(kind,params,False)
                raise
            self.extra.audit(kind,params,True,result)
            return result
        raise InputError("Неизвестная операция.")

    @staticmethod
    def billing_revision(c):
        return (c.id,tuple((s.id,s.tariff_name,s.status) for s in c.services))

    @staticmethod
    def archive_public(d):
        from desktop_v4.archive_report import project
        return project(d)

    def execute(self,kind,p,revision,cancel,emit):
        svc=self.w.service;creds=self.w.session
        if kind in ("delete","restore","sheets","b2o","transfer","cloud_restore"):
            return self.extra.execute(kind,p,revision,cancel,emit)
        if kind=="background":
            from desktop_v4 import background
            self.admin_only();action,interval,targets=p;file=None
            if cancel.is_set():raise InputError("Операция отменена.")
            try:
                if action=="enable":
                    if tuple(self.w.hosts)!=targets:raise InputError("Список серверов изменился. Повторите подготовку.")
                    backend=self.extra.sheets_service()
                    file=background.stage(creds,backend.service_account_info,list(targets),backend.spreadsheet_id,interval)
                if cancel.is_set():raise InputError("Операция отменена.")
                emit("Подтвердите запрос UAC Windows")
                background.elevate(action,file)
                return {"background":background.status(),"action":action,"submitted":True}
            except RuntimeError as ex:raise InputError(str(ex)) from None
            finally:
                if file is not None:file.unlink(missing_ok=True)
        if kind=="inactive_batch":
            self.admin_only();action,targets=p;results=[]
            if action=="delete": self.extra.destructive_enabled()
            for i,(h,login,revision) in enumerate(targets):
                if cancel.is_set(): break
                emit("Обработка "+str(i+1)+"/"+str(len(targets)),round(i*100/len(targets)))
                try:
                    if action=="delete":
                        if self.extra.delete_revision(h,login)!=revision:
                            results.append({"server":h,"login":login,"status":"changed"});break
                        self.extra.confirmed_delete(h,login,revision,cancel,emit)
                        results.append({"server":h,"login":login,"status":"confirmed"})
                        self.extra.audit("delete",(h,login),True)
                        continue
                    current=self.get_client(h,login)
                    if current.is_online or self.client_revision(current)!=revision:
                        results.append({"server":h,"login":login,"status":"changed"});break
                    modified=svc.set_secret_enabled(h,creds,login,action=="enable")
                    confirmed=modified is not None and modified.is_enabled==(action=="enable")
                    results.append({"server":h,"login":login,"status":"confirmed" if confirmed else "unknown"})
                    self.extra.audit("vpn",(h,login,action,None),confirmed)
                    if not confirmed: break
                except Exception:
                    results.append({"server":h,"login":login,"status":"unknown"})
                    if action=="delete": self.extra.audit("delete",(h,login),False)
                    else: self.extra.audit("vpn",(h,login,action,None),False)
                    break
            for h,login,_ in targets[len(results):]: results.append({"server":h,"login":login,"status":"not_attempted"})
            self.inactive_records={}
            return {"items":results}
        if kind=="create":
            h,login,ports,count,start_ip=p
            self.created_batch=set()
            rows=svc.create_clients_batch(h,creds,login,ports,count,progress_callback=lambda done,total,name:emit("Создание VPN "+str(name),round(done*100/max(total,1))),cancel_event=cancel,start_ip=start_ip)
            self.created_batch={(c.server,c.login) for c in rows}
            return {"clients":[public_client(c) for c in rows]}
        if kind=="vpn":
            h,login,act,value=p
            if act not in ("add","disconnect"): self.admin_only()
            current=self.get_client(h,login)
            if self.client_revision(current)!=revision: raise InputError("Данные клиента изменились. Повторите подготовку.")
            if act in ("port_enable","port_disable","remove_port","recreate_port"):
                try: conflicts=svc.inspect_port_conflicts(h,creds,current)
                except Exception: raise InputError("Проверка конфликтов NAT не завершилась. Изменение заблокировано.")
                if conflicts.get(value): raise InputError("Обнаружен конфликт NAT. Операция заблокирована.")
            if act=="add": result=svc.add_ports(h,creds,login,value)
            elif act in ("enable","disable"): result=svc.set_secret_enabled(h,creds,login,act=="enable")
            elif act in ("port_enable","port_disable"): result=svc.set_port_enabled(h,creds,login,value,act=="port_enable")
            elif act=="disconnect": return {"disconnected":svc.disconnect_client_session(h,creds,login)}
            elif act=="password": result=svc.set_password(h,creds,login,value)
            elif act in ("remove_port","recreate_port"):
                if current.port_conflicts.get(value): raise InputError("Обнаружен конфликт NAT.")
                if act=="remove_port": result=svc.remove_port(h,creds,login,value)
                else: result=svc.recreate_port(h,creds,login,value)
            out=public_client(result)
            if act=="add":out["new_ports"]=sorted(set(result.ports)-set(current.ports))
            out["conflicts_verified"]=False
            return out
        if kind=="lv_batch":
            self.admin_only();action,hosts=p;rows=[]
            for i,h in enumerate(hosts):
                if cancel.is_set(): break
                emit("LV "+str(i+1)+"/"+str(len(hosts)),round(i*100/len(hosts)))
                try:
                    result=self.execute("lv",(h,action),None,cancel,emit)
                    rows.append({"server":h,"status":"confirmed","state":result["state_text"]})
                except Exception:
                    rows.append({"server":h,"status":"not_confirmed"})
                    # Do not continue fleet writes after an unknown mutation.
                    break
            for h in hosts[len(rows):]: rows.append({"server":h,"status":"not_attempted"})
            return {"lv_results":rows}
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
            else:
                state=lv.get_status(h,creds)
                if not state.installed or (action=="quarantine_on" and state.initialized<=0): raise InputError("Сначала установите LV и инициализируйте активность.")
                lv.set_quarantine_enabled(h,creds,action=="quarantine_on")
            s=lv.get_status(h,creds)
            return {**asdict(s),"installed":s.installed,"paused":s.paused,"state_text":s.state_text}
        if kind=="billing_bulk":
            from linkvideo_vpn_helper.services.billing_token_transport import BillingWriteOutcomeUnknown
            changes,=p;g=self.crm();c=g.refresh_client(self.source_client())
            if self.billing_revision(c)!=revision: raise InputError("Карточка CRM изменилась. Повторите подготовку.")
            rows=[]
            for i,change in enumerate(changes):
                if cancel.is_set(): break
                emit("Изменение тарифа "+str(i+1)+"/"+str(len(changes)),round(i*100/len(changes)))
                key=(c.id,change.service_id,change.tariff_id,change.effective_date)
                if key in self.uncertain: break
                try:
                    g.change_tariff(change,client=c)
                    rows.append({"camera":change.service_id,"status":"confirmed"})
                except BillingWriteOutcomeUnknown:
                    self.uncertain.add(key);rows.append({"camera":change.service_id,"status":"unknown"});break
                except Exception:
                    rows.append({"camera":change.service_id,"status":"not_confirmed"});break
            for change in changes[len(rows):]: rows.append({"camera":change.service_id,"status":"not_attempted"})
            try: self.client=g.refresh_client(c)
            except Exception: self.client=None
            return {"items":rows,"client":asdict(self.client) if self.client else None}
        if kind=="billing":
            from linkvideo_vpn_helper.services.billing_gateway import TariffChange
            act,camera,value,when,comment=p
            g=self.crm();c=g.refresh_client(self.source_client())
            if self.billing_revision(c)!=revision: raise InputError("Карточка CRM изменилась. Повторите подготовку.")
            if act=="tariff":
                from linkvideo_vpn_helper.services.billing_token_transport import BillingWriteOutcomeUnknown
                key=(c.id,camera,value,when)
                if key in self.uncertain: raise InputError("Предыдущая запись не подтверждена. Проверьте CRM.")
                try: g.change_tariff(TariffChange(c.id,camera,value,when,comment),client=c)
                except BillingWriteOutcomeUnknown:
                    self.uncertain.add(key);raise InputError("Результат PUT неизвестен. Проверьте CRM перед повтором.")
            else:
                from linkvideo_vpn_helper.services.billing_token_transport import BillingWriteOutcomeUnknown
                key=(c.id,camera,"status",value,when)
                if key in self.uncertain: raise InputError("Предыдущая запись не подтверждена. Проверьте CRM.")
                try: g.set_camera_status(c,camera,value,when,comment)
                except BillingWriteOutcomeUnknown:
                    self.uncertain.add(key);raise InputError("Результат изменения статуса неизвестен. Проверьте CRM перед повтором.")
            self.client=g.refresh_client(c)
            return asdict(self.client)
        if kind=="agent":
            action,value,old=p
            if self.camera is None or (self.camera.serial_number,self.camera.server)!=revision: raise InputError("Камера изменилась.")
            camera=self.agent.find_camera(revision[0])
            if camera.server!=revision[1] or camera.serial_number!=revision[0]: raise InputError("Камера сменила сервер. Повторите поиск.")
            if cancel.is_set(): raise InputError("Операция отменена.")
            if action=="timezone":
                # Three writes are non-atomic. Verify the embedded LAPI
                # response as well as HTTP before declaring each stage done.
                import time
                stages=(
                    ("Time","/LAPI/V1.0/System/Time",{"TimeZone":f"GMT{value:+03d}:00","DeviceTime":int(time.time())}),
                    ("NTP","/LAPI/V1.0/System/Time/NTP",{"Num":1,"NTPServerInfos":[{"Enabled":1,"AddressType":2,"DomainName":"ntp.goodline.info","Port":123,"SynchronizeInterval":3600}]}),
                    ("SyncMode","/LAPI/V1.0/System/Time/SyncMode",{"Mode":3}),
                )
                rows=[]
                for name,path,payload in stages:
                    if cancel.is_set(): break
                    emit("Настройка "+name)
                    try:
                        reply=self.agent.proxy_pipe(camera,self.agent._raw_http_put(path,payload))
                        self.agent._proxy_payload(reply)
                        rows.append({"stage":name,"accepted":True,"http":int(reply.status_code)})
                    except Exception:
                        rows.append({"stage":name,"accepted":False})
                        break
                rows.extend({"stage":name,"accepted":None} for name,_,_ in stages[len(rows):])
                return {"action":action,"serial":camera.serial_number,"stages":rows,"confirmed":False,"accepted":all(x["accepted"] is True for x in rows)}
            self.agent_values.pop(action,None)
            if action=="rotation":
                current=self.agent.get_image_enhance(camera)
                if int(current["ImageRotation"])!=old: raise InputError("Ориентация изменилась. Обновите настройку.")
                if cancel.is_set(): raise InputError("Операция отменена.")
                before,after=self.agent.set_image_rotation(camera,current,value)
            else:
                current=self.agent.get_video_streams(camera)
                if int(self.agent._main_video_encode_info(current)["BitRate"])!=old: raise InputError("Битрейт изменился. Обновите настройку.")
                if cancel.is_set(): raise InputError("Операция отменена.")
                before,after=self.agent.set_bitrate(camera,current,value)
            self.agent_values[action]=int(after)
            return {"action":action,"serial":camera.serial_number,"before":before,"after":after,"confirmed":True}
        raise InputError("Неизвестное подтверждение.")
