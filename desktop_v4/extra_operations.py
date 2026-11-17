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

METHODS=frozenset(("copy_vpn","copy_vpn_batch","backup","delete_preview","backup_status","backup_inventory","backup_verify","cloud_deleted_search","sheets_load","deleted_search","restore_preview","sheets_preview","cloud_connect","cloud_status","cloud_disconnect","cloud_restore_preview","activity","camera_inspect","camera_live","camera_image","agent_image","b2o_read","b2o_create_preview","billing_recipient","billing_transfer_preview","templates_import","templates_attach","templates_reply","templates_images","templates_detach","winbox","registry_list","registry_update"))
class Extended:
    def __init__(self,ops):
        self.o=ops;self.cloud=None;self.sheets=None;self.deleted={};self.cloud_deleted={};self.recipients={};self.camera_report=None;self.camera_operator=None
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
    def delete_state(self,h,login,data=None,check_narrow=True):
        """Match the exact owned, inactive objects; counters are not configuration."""
        from desktop_v4.operations import InputError
        svc=self.o.w.service
        if data is None: data=svc.fetch_config_snapshot(h,self.o.w.session)
        if not isinstance(data,dict) or any(not isinstance(data.get(k),list) for k in ("secrets","profiles","nat_rules","actives")):
            raise InputError("Полный снимок конфигурации не подтверждён.")
        secrets=data["secrets"];profiles=data["profiles"];rules=data["nat_rules"]
        target=[r for r in secrets if r.get("name")==login]
        if len(target)!=1: raise InputError("Не удалось однозначно определить PPP-учётку.")
        target=target[0];name=target.get("profile")
        if name!=login or not target.get(".id") or not target.get("password"):
            raise InputError("Профиль не выделен этой учётке или пароль недоступен для восстановления.")
        matches=[r for r in profiles if r.get("name")==name]
        if len(matches)!=1 or not matches[0].get(".id"): raise InputError("Профиль не подтверждён.")
        remote=svc._get_client_remote_address(target,matches[0])
        if not remote: raise InputError("VPN IP не определён.")
        for other in secrets:
            if other.get("name")==login: continue
            profile=next((r for r in profiles if r.get("name")==other.get("profile")),None)
            if other.get("profile")==name or svc._get_client_remote_address(other,profile)==remote:
                raise InputError("Общий профиль или IP. Автоматическое удаление запрещено.")
        for profile in profiles:
            if profile.get("name")!=name and svc._normalize_ip(profile.get("remote-address"))==remote:
                raise InputError("Этот VPN IP указан в другом PPP-профиле.")
        if any(r.get("name")==login or svc._normalize_ip(r.get("address"))==remote for r in data["actives"]):
            raise InputError("Клиент/IP в сети. Сначала проверьте активную сессию.")
        own=[r for r in rules if svc._normalize_ip(svc._get_rule_remote(r))==remote]
        if any(r.get("comment")!=login or not r.get(".id") or r.get("chain")!="dstnat" or r.get("action")!="dst-nat" for r in own):
            raise InputError("Неоднозначные NAT-правила. Автоматическое удаление запрещено.")
        if any(r.get("comment")==login and r not in own for r in rules):
            raise InputError("Комментарий VPN используется в другом NAT-правиле.")
        ids=lambda rows: sorted(str(x.get(".id","")) for x in rows)
        nat_ids=ids(own)
        if len(set(nat_ids))!=len(nat_ids): raise InputError("Обнаружены повторяющиеся NAT ID.")
        if check_narrow:
            narrow=svc.fetch_client_snapshot(h,self.o.w.session,login)
            if ids(narrow.get("nat_rules",[]))!=nat_ids or ids(narrow.get("secrets",[]))!=[target[".id"]] or ids(narrow.get("profiles",[]))!=[matches[0][".id"]] or narrow.get("actives"):
                raise InputError("Снимки конфигурации расходятся. Повторите проверку.")
        # Exclude volatile NAT counters, not configuration, and keep password in
        # the private digest only. Never send it, a snapshot or IDs to Electron.
        volatile={"bytes","packets","packet-count","packet-bytes","orig-bytes","repl-bytes","orig-packets","repl-packets"}
        stable=[target,matches[0],[{k:v for k,v in r.items() if k not in volatile} for r in sorted(own,key=lambda r:r[".id"])]]
        return {"digest":hashlib.sha256(json.dumps(stable,sort_keys=True,default=str).encode()).hexdigest(),"secret":target[".id"],"profile":matches[0][".id"],"nat":nat_ids}
    def delete_revision(self,h,login):
        return self.delete_state(h,login)["digest"]
    def confirmed_delete(self,h,login,revision,cancel,emit):
        from desktop_v4.operations import InputError
        import uuid
        svc=self.o.w.service;creds=self.o.w.session
        if cancel.is_set(): raise InputError("Операция отменена.")
        before=self.delete_state(h,login)
        if before["digest"]!=revision: raise InputError("Конфигурация изменилась. Повторите проверку.")
        emit("FULL backup перед удалением")
        backup=self.backup_service().backup_one(h,creds,prefix="BEFORE_DELETE_"+uuid.uuid4().hex[:12])
        if not backup.ok or cancel.is_set(): raise InputError("FULL backup не подтверждён. Удаление не начато.")
        try:
            payload=json.loads(backup.json_path.read_text(encoding="utf-8"))
            if payload.get("server")!=h or payload.get("format_version")!=1: raise ValueError()
            sections=payload["sections"]
            snapshot={k:sections[v] for k,v in {"secrets":"ppp_secrets","profiles":"ppp_profiles","actives":"ppp_active_snapshot","nat_rules":"firewall_nat"}.items()}
            saved=self.delete_state(h,login,snapshot,check_narrow=False)
            manifest=json.loads((backup.json_path.parent/"manifest.json").read_text(encoding="utf-8"))
            if h not in manifest.get("servers_ok",[]) or not any(f.get("name")==backup.json_path.name and f.get("sha256")==hashlib.sha256(backup.json_path.read_bytes()).hexdigest() for f in manifest.get("files",[])):
                raise ValueError()
        except Exception as ex:
            raise InputError("Резервная копия не содержит проверяемой конфигурации клиента. Ничего не удалено.") from ex
        if saved["digest"]!=revision or self.delete_revision(h,login)!=revision:
            raise InputError("Конфигурация изменилась между проверкой и резервной копией. Ничего не удалено.")
        if cancel.is_set(): raise InputError("Операция отменена.")
        # Reuse the stable service; its RouterOS operations are not atomic.
        # Do not retry if any response or read-back is unknown.
        svc.delete_client(h,creds,login)
        after=svc.fetch_config_snapshot(h,creds)
        if any(not isinstance(after.get(k),list) for k in ("secrets","profiles","nat_rules")):
            raise InputError("Проверка после удаления не завершена.")
        if any(r.get("name")==login or r.get(".id")==before["secret"] for r in after["secrets"]) or any(r.get("name")==login or r.get(".id")==before["profile"] for r in after["profiles"]) or any(r.get(".id") in before["nat"] or r.get("comment")==login for r in after["nat_rules"]):
            raise InputError("Удаление всех объектов не подтверждено. Проверьте MikroTik вручную.")
        return {"deleted":True,"server":h,"login":login}
    def search_cloud_archive(self,query):
        self.cloud_deleted={}
        if self.cloud is None:
            return [],"VPNSync не подключён. Настройте отдельное подключение в разделе VPN."
        try:
            rows=self.cloud.request("GET","/v1/deleted/search",params={"q":query,"limit":50})
            clean=[]
            for row in rows or []:
                if not isinstance(row,dict): continue
                host=str(row.get("server") or "");login=str(row.get("login") or "")
                if host not in self.o.w.hosts or not login: continue
                ports=[]
                for rule in row.get("ports") or []:
                    if not isinstance(rule,dict): continue
                    try: ext=int(rule.get("external_port") or 0);internal=int(rule.get("internal_port") or ext)
                    except (ValueError,TypeError): continue
                    if 1<=ext<=65535 and 1<=internal<=65535:
                        ports.append(str(ext)+" → "+str(internal))
                record={"server":host,"login":login,"password_saved":bool(row.get("password_saved")),
                        "remote_address":str(row.get("remote_address") or ""),"deleted_at":str(row.get("deleted_at") or ""),"ports":ports}
                self.cloud_deleted[(host,login)]=record;clean.append(record)
            return clean,None
        except Exception:
            return [],"Архив VPNSync недоступен. Резерв Google Sheets не использован автоматически."
    def cloud_preflight(self,h,login):
        from desktop_v4.operations import InputError
        if self.cloud is None: raise InputError("VPNSync не подключён.")
        result=self.cloud.request("GET","/v1/archive/restore/preflight",params={"server":h,"login":login},timeout=20.0)
        if not isinstance(result,dict): raise InputError("Проверка VPNSync не подтверждена.")
        conflicts=result.get("conflicts") or []
        if result.get("ok") is not True or conflicts:
            labels={"login":"Занят PPP-логин","profile":"Занят PPP-профиль","remote-address-secret":"IP занят PPP-учёткой","remote-address-profile":"IP занят PPP-профилем","nat-port":"Занят внешний NAT-порт"}
            reasons=[]
            for item in conflicts[:12] if isinstance(conflicts,list) else []:
                if not isinstance(item,dict): continue
                kind=item.get("type");label=labels.get(kind,"Конфликт RouterOS");value=str(item.get("value") or "")
                if kind in ("remote-address-secret","remote-address-profile","nat-port") and re.fullmatch(r"[0-9.:-]{1,64}",value):label+=": "+value
                reasons.append(label)
            raise InputError("Восстановление заблокировано. "+("; ".join(reasons) or "Проверка конфликтов не подтверждена."))
        ports=result.get("ports")
        if not isinstance(ports,list):raise InputError("VPNSync не подтвердил список исходных портов.")
        try:
            if any(type(x) not in (int,str) or not str(x).isascii() or not str(x).isdigit() or not 1<=int(x)<=65535 for x in ports):raise ValueError()
            result={**result,"ports":sorted(set(map(int,ports)))}
        except (ValueError,TypeError):raise InputError("Некорректные порты в preflight VPNSync.") from None
        return result
    def audit(self,kind,params,success,result=None):
        if self.cloud is None or kind not in ("create","vpn","delete"): return
        from threading import Thread
        from linkvideo_vpn_helper.services.cloud_vpnsync import CloudVPNSyncClient
        config=self.cloud.config
        employee=str(self.o.w.identity.get("username") or "")
        h,login=params[:2]
        action="client.delete" if kind=="delete" else "client.create"
        details={"employee":employee}
        if kind=="vpn":
            act,value=params[2:]
            action={"add":"nat.add_ports","enable":"client.enabled_change","disable":"client.enabled_change","port_enable":"nat.enabled_change","port_disable":"nat.enabled_change","disconnect":"client.disconnect","remove_port":"nat.remove_port","recreate_port":"nat.recreate","password":"client.password_change"}[act]
            if type(value) is int: details["count" if act=="add" else "port"]=value
            if act in ("enable","disable","port_enable","port_disable"): details["enabled"]=act in ("enable","port_enable")
            if act=="password": details["password_changed"]=True
        if kind=="create": details.update(ports_per_client=params[2],accounts_count=params[3])
        records=(result or {}).get("clients",[]) if success and kind=="create" else []
        targets=[str(x.get("login") or login) for x in records] or [login]
        def send():
            try:
                client=CloudVPNSyncClient(self.o.settings);client.set_config(config,save=False)
                for target in targets: client.record_activity(action,server=h,login=target,success=success,details=details)
            except Exception:
                # Never repeat the actual RouterOS mutation after an audit error.
                pass
        Thread(target=send,daemon=True,name="lv4-vpnsync-audit").start()
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
        if m=="copy_vpn_batch":
            targets=a.get("targets")
            if not isinstance(targets,list) or not 1<=len(targets)<=20:raise InputError("Выберите созданные учётки.")
            keys=[]
            for target in targets:
                if not isinstance(target,dict):raise InputError("Некорректная учётка.")
                key=(o.host(target),text(target,"login"))
                if key not in o.created_batch or key in keys:raise InputError("Учётка не относится к последнему созданному набору.")
                keys.append(key)
            result=[]
            for h,login in keys:
                if cancel.is_set():raise InputError("Копирование отменено.")
                result.append(o.get_client(h,login).copy_text())
            return "\n\n".join(result)
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
        if m in ("backup_inventory","backup_verify"):
            from desktop_v4.backup_inventory import inventory,verify
            o.admin_only();root=self.backup_service().default_root()
            return inventory(root) if m=="backup_inventory" else verify(root,text(a,"id",240),cancel,emit)
        if m=="cloud_deleted_search":
            o.admin_only()
            rows,error=self.search_cloud_archive(text(a,"query",128))
            return {"rows":rows,"error":error}
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
            rev=hashlib.sha256(json.dumps(row.row,sort_keys=True,default=str).encode()).hexdigest()
            return o.stage("restore",(h,login),f"Восстановить VPN {h}/{login} после FULL backup. Аварийный режим Sheets: занятые внешние порты могут быть заменены на свободные с сохранением внутренних портов.",rev)
        if m=="sheets_preview":
            o.admin_only();self.sheets_service();hosts=tuple(o.w.hosts) if a.get("all") is True else (o.host(a),)
            if not hosts: raise InputError("Нет доверенных серверов.")
            return o.stage("sheets",(hosts,),f"Аварийная выгрузка конфигурации в Google Sheets: {len(hosts)} серверов. Это ручная резервная копия, не основной PostgreSQL.")
        if m=="cloud_restore_preview":
            o.admin_only();o.writes_enabled();self.destructive_enabled();h=o.host(a);login=text(a,"login")
            row=self.cloud_deleted.get((h,login))
            if not row or not row["password_saved"]: raise InputError("Найдите удалённую учётку с сохранённым паролем в PostgreSQL.")
            preflight=self.cloud_preflight(h,login)
            ports=[]
            for p in preflight.get("ports") or []:
                try:
                    n=int(p)
                    if 1<=n<=65535: ports.append(str(n))
                except (TypeError,ValueError): pass
            return o.stage("cloud_restore",(h,login),f"Восстановить {h}/{login} из PostgreSQL без замены занятых портов. VPN IP: {row['remote_address']}. Порты: "+(", ".join(ports) or "нет"),tuple(preflight["ports"]))
        if m=="cloud_status":
            if self.cloud is None: return {"connected":False}
            config=self.cloud.config
            return {"connected":True,"host":config.host,"port":config.port,"username":config.username,"tls":config.use_tls}
        if m=="cloud_disconnect":
            o.plan=None
            if self.cloud is not None:
                try: self.cloud._session.close()
                except Exception: pass
            self.cloud=None;self.cloud_deleted={}
            return {"connected":False}
        if m=="cloud_connect":
            o.plan=None
            from linkvideo_vpn_helper.services.cloud_vpnsync import CloudVPNSyncClient,CloudServerConfig
            host=text(a,"host",253)
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}",host): raise InputError("Укажите только DNS-имя или IPv4 облачного сервера.")
            password=a.get("password")
            if not isinstance(password,str) or not password or len(password)>512: raise InputError("Введите пароль облачного аккаунта.")
            config=CloudServerConfig(host=host,port=number(a,"port",1,65535),username=text(a,"username"),password=password,use_tls=a.get("tls") is True,remember=False)
            if not config.use_tls and a.get("allow_http") is not True: raise InputError("HTTP не шифруется. Используйте HTTPS или явно разрешите HTTP внутри доверенной сети.")
            if self.cloud is not None:
                try: self.cloud._session.close()
                except Exception: pass
            self.cloud=None;self.cloud_deleted={}
            client=CloudVPNSyncClient(o.settings);client.set_config(config,save=False)
            try: client.test_connection()
            except Exception as exc:
                client._session.close()
                if "Неверный логин или пароль" in str(exc): raise InputError("Неверный логин или пароль VPNSync. Это отдельная учётная запись, не SSH и не MikroTik.") from None
                raise InputError("Подключение VPNSync не подтверждено. Проверьте адрес, протокол, доступность и учётную запись API.") from None
            self.cloud=client
            return {"connected":True,"host":config.host,"port":config.port,"username":config.username,"tls":config.use_tls}
        if m=="activity":
            o.admin_only()
            if self.cloud is None: raise InputError("Подключите отдельный облачный аккаунт.")
            h=o.host(a);login=text(a,"login",128)
            # History is scoped to a live client, never a global employee log.
            o.get_client(h,login)
            rows=self.cloud.activity(200,login=login,source=text(a,"source",80,False))
            result=[]
            for row in rows:
                if not isinstance(row,dict) or str(row.get("login") or "")!=login or str(row.get("server") or "")!=h: continue
                out={k:str(row.get(k) or "")[:160] for k in ("created_at","timestamp","action","server","login","source","username","actor","success")}
                detail=row.get("details") or {}
                if not isinstance(detail,dict): detail={}
                if out["source"]=="desktop" and isinstance(detail.get("employee"),str): out["actor"]=detail["employee"][:128]
                # Only allowlisted numeric/boolean parameters; never expose arbitrary payloads.
                limit={k:v for k,v in detail.items() if k in ("count","port","enabled","ports_per_client","accounts_count","clients","active","added","changed","deleted","restored_objects","session_removed","active_sessions") and type(v) in (int,bool)}
                out["details"]=limit
                result.append(out)
            return result
        if m=="camera_inspect":
            from linkvideo_vpn_helper.services.camera_diagnostics import inspect_camera
            from linkvideo_vpn_helper.services.camera_stream_format import camera_address,evaluate_transcode,load_transcode_rules,readable_audio_codec,readable_video_codec
            b2o=o.archives().b2o
            self.camera_report=None;self.camera_operator=None
            query=text(a,"camera");operator=o.operator(a)
            c=inspect_camera(b2o,query,operator,with_preview=False,with_network=True)
            # A serial search can resolve to another operator; bind the API
            # country/returned ID, not just the user's original selection.
            resolved={"Россия":241,"Казахстан":1721,"Беларусь":1741}.get(c.operator,operator)
            self.camera_report=c;self.camera_operator=b2o.resolve_operator_id(c.camera_id,resolved)
            metadata=c.metadata if isinstance(c.metadata,dict) else {}
            transcode,check=evaluate_transcode(metadata,load_transcode_rules(o.settings))
            bandwidth=str(metadata.get("nimble_main_bandwidth") or "")
            return {**{k:getattr(c,k) for k in ("camera_id","server","ip","rtsp_port","ping_text","tcp_text","operator","name","status","serial","account_id","archive_depth","model","port_source","rtsp_host","last_stream")},
                "address":camera_address(metadata),"transcode":transcode,"transcode_check":check,
                "b2o_resolution":str(metadata.get("nimble_main_resolution") or "")[:80],
                "b2o_video_codec":readable_video_codec(metadata.get("nimble_main_video_codec")),
                "b2o_audio_codec":readable_audio_codec(metadata.get("nimble_main_audio_codec")),
                "b2o_bitrate":f"{int(bandwidth)/1000000:.2f} Мбит/с" if bandwidth.isascii() and bandwidth.isdigit() and len(bandwidth)<15 else ""}
        if m in ("camera_live","camera_image"):
            archive=o.archives();b2o=archive.b2o;c=self.camera_report
            if c is None or self.camera_operator is None or text(a,"id",128)!=c.camera_id:raise InputError("Карточка камеры изменилась. Повторите диагностику.")
            if cancel.is_set():raise InputError("Операция отменена.")
            if m=="camera_image":
                from linkvideo_vpn_helper.services.b2o_camera_preview import fetch_camera_preview
                number,_=b2o.normalize_camera_id(c.camera_id,self.camera_operator)
                try:raw=fetch_camera_preview(c.operator,number,b2o.token())
                except Exception:raise InputError("Последнее изображение B2O недоступно.") from None
                mime="image/jpeg" if raw.startswith(b"\xff\xd8") else "image/png" if raw.startswith(b"\x89PNG\r\n\x1a\n") else "image/webp" if raw[:4]==b"RIFF" and raw[8:12]==b"WEBP" else ""
                if not mime or len(raw)>5*1024*1024:raise InputError("B2O не вернул поддерживаемое изображение.")
                return {"id":c.camera_id,"src":"data:"+mime+";base64,"+base64.b64encode(raw).decode("ascii")}
            from dataclasses import replace
            from linkvideo_vpn_helper.services.b2o_rtsp_probe import fetch_camera_stream_with_status
            from linkvideo_vpn_helper.services.camera_live_probe import probe_live_camera,_cached_ffmpeg
            stream,status=fetch_camera_stream_with_status(b2o,c.camera_id,self.camera_operator)
            sample=replace(c,rtsp_url=stream.url,rtsp_host=stream.host,rtsp_port=stream.port,port_source="camera-streams-urls") if stream else c
            if sample.rtsp_url and not _cached_ffmpeg():
                emit("Подготовка FFmpeg для RTSP-замера")
                try:archive.ensure_ffmpeg(cancel_event=cancel,progress=lambda *_:emit("Подготовка проверенного FFmpeg"))
                except Exception:emit("FFmpeg недоступен. Проверка доступного архива.")
            if cancel.is_set():raise InputError("Операция отменена.")
            result=probe_live_camera(sample)
            if cancel.is_set():raise InputError("Операция отменена.")
            return {"id":c.camera_id,**{k:getattr(result,k) for k in ("bitrate_mbps","last_stream","connectivity","video_codec","audio_codec","resolution","fps","source")},"detail":(status+"\n" if status else "")+result.detail}
        if m=="agent_image":
            from linkvideo_vpn_helper.services.agent_b2o_preview import load_b2o_preview_for_serial
            if o.camera is None: raise InputError("Сначала найдите камеру агента.")
            b2o=o.archives().b2o
            result=load_b2o_preview_for_serial(o.camera.serial_number,b2o.token(),b2o)
            if not result: raise InputError("Превью B2O не найдено для этого серийного номера.")
            country,camera,raw=result
            mime="image/jpeg" if raw.startswith(b"\xff\xd8") else "image/png" if raw.startswith(b"\x89PNG\r\n\x1a\n") else "image/webp" if raw[:4]==b"RIFF" and raw[8:12]==b"WEBP" else ""
            if not mime or len(raw)>5*1024*1024: raise InputError("Неподдерживаемый формат превью.")
            return {"country":country,"camera":camera,"src":"data:"+mime+";base64,"+base64.b64encode(raw).decode("ascii")}
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
        if m=="templates_reply":
            from PySide6.QtCore import QByteArray,QBuffer,QIODevice,Qt
            from PySide6.QtGui import QImageReader
            store=o.template_store();tid=number(a,"id",1,2**31-1)
            item=store.get(tid)
            if item is None: raise InputError("Шаблон не найден.")
            selected=a.get("image")
            if selected is not None and (type(selected) is not int or selected<1): raise InputError("Некорректное изображение.")
            rows=store.images(tid)
            if selected is not None:
                rows=[(i,p) for i,p in rows if i==selected]
                if not rows: raise InputError("Изображение не принадлежит шаблону.")
            images=[];skipped=0;total=0
            for i,p in rows:
                if len(images)>=12 or cancel.is_set(): skipped+=1;continue
                try:
                    if not p.is_file() or p.stat().st_size>20*1024*1024: raise ValueError()
                    reader=QImageReader(str(p));reader.setAutoTransform(True)
                    size=reader.size()
                    if not size.isValid(): raise ValueError()
                    if max(size.width(),size.height())>1600:
                        size.scale(1600,1600,Qt.AspectRatioMode.KeepAspectRatio);reader.setScaledSize(size)
                    image=reader.read()
                    if image.isNull(): raise ValueError()
                    if max(image.width(),image.height())>1600: image=image.scaled(1600,1600,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
                    raw=QByteArray();buf=QBuffer(raw)
                    if not buf.open(QIODevice.OpenModeFlag.WriteOnly): raise ValueError()
                    saved=image.save(buf,"PNG");buf.close()
                    if not saved or total+len(raw)>18*1024*1024: raise ValueError()
                    total+=len(raw);images.append("data:image/png;base64,"+base64.b64encode(bytes(raw)).decode("ascii"))
                except Exception: skipped+=1
            if cancel.is_set(): raise InputError("Копирование отменено.")
            if selected is not None and not images: raise InputError("Изображение недоступно.")
            return {"text":item.body if selected is None else "","images":images,"skipped":skipped}
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
        if kind=="cloud_restore":
            o.admin_only();self.destructive_enabled();h,login=p
            current=self.cloud_preflight(h,login)
            if tuple(current["ports"])!=revision:raise InputError("Порты в архиве изменились. Повторите подготовку.")
            if svc.get_client(h,creds,login) is not None:raise InputError("Учётка уже существует на MikroTik.")
            if cancel.is_set(): raise InputError("Операция отменена.")
            result=self.cloud.request("POST","/v1/archive/restore",json={"server":h,"login":login},timeout=45.0)
            if not isinstance(result,dict) or result.get("ok") is not True: raise InputError("Восстановление не подтверждено. Проверьте MikroTik.")
            fresh=svc.get_client(h,creds,login)
            if fresh is None or set(fresh.ports)!=set(revision): raise InputError("VPNSync принял восстановление, но живой MikroTik не подтвердил учётку и исходные порты. Проверьте состояние перед повтором.")
            self.cloud_deleted.pop((h,login),None)
            from desktop_v4.operations import public_client
            return {"restored":True,"server":h,"login":login,"client":public_client(fresh),"restored_objects":result.get("restored_objects") if type(result.get("restored_objects")) is int else None}
        if kind=="delete":
            o.admin_only();self.destructive_enabled();h,login=p
            return self.confirmed_delete(h,login,revision,cancel,emit)
        if kind=="restore":
            o.admin_only();self.destructive_enabled();h,login=p
            emit("Проверка FULL backup")
            result=self.backup_service().backup_one(h,creds,prefix="BEFORE_"+kind.upper())
            if not result.ok or cancel.is_set(): raise InputError("FULL backup не подтверждён. Операция не начата.")
            if svc.get_client(h,creds,login) is not None: raise InputError("Клиент уже существует на MikroTik.")
            restore=self.restore_service()
            row=restore.backend.find_deleted_row(h,login)
            if row is None or hashlib.sha256(json.dumps(row,sort_keys=True,default=str).encode()).hexdigest()!=revision: raise InputError("Резервная запись изменилась. Повторите поиск.")
            # Bind the exact approved row and defer deleting archive metadata
            # until live readback succeeds. A stale archive is safer than loss.
            from copy import deepcopy
            backend=restore.backend
            class PinnedRecovery:
                def find_deleted_row(self,server,account):
                    if (server,account)!=(h,login):raise ValueError("Unexpected restore target")
                    return deepcopy(row)
                def remove_deleted_rows(self,*args):
                    return None
            restore.backend=PinnedRecovery()
            if cancel.is_set():raise InputError("Операция отменена.")
            result=restore.restore(h,creds,login)
            fresh=svc.get_client(h,creds,login)
            if fresh is None or set(result.ports)!=set(fresh.ports): raise InputError("Восстановление не подтверждено повторным чтением. Проверьте MikroTik перед повтором.")
            cleaned=False
            try:
                current=backend.find_deleted_row(h,login)
                if current is not None and hashlib.sha256(json.dumps(current,sort_keys=True,default=str).encode()).hexdigest()==revision:
                    backend.remove_deleted_rows(h,[login]);cleaned=True
            except Exception:pass
            self.deleted.pop((h,login),None)
            from desktop_v4.operations import public_client
            return {**asdict(result),"restored":True,"client":public_client(fresh),"archive_cleanup_confirmed":cleaned}
        if kind=="sheets":
            o.admin_only()
            from linkvideo_vpn_helper.services.vpn_sheets_sync import VPNSheetsSyncService
            hosts,=p
            sync=VPNSheetsSyncService(svc,self.sheets_service());rows=[]
            for index,h in enumerate(hosts):
                if cancel.is_set(): break
                emit("Аварийный экспорт "+str(index+1)+"/"+str(len(hosts)),round(index*100/len(hosts)))
                try:
                    result=sync.sync_server(h,creds,source="Helper 4 explicit emergency export")
                    rows.append({"server":h,"success":bool(result.success),"clients":result.clients,"added":result.added,"changed":result.changed,"deleted":result.deleted})
                except Exception:
                    rows.append({"server":h,"success":False})
            return {"sheets_results":rows,"total":len(hosts),"cancelled":cancel.is_set()}
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
