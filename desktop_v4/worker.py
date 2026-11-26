"""Private stdio RPC; no HTTP listener. Mutations are explicitly gated."""
from __future__ import annotations
import json
import sys
from dataclasses import asdict
from threading import Thread, Event, Lock
from desktop_v4.operations import Operations, InputError

# Keep the wire separate from library stdout.
WIRE = sys.stdout
sys.stdout = sys.stderr

class Rejected(Exception):
    pass

class Workspace:
    def __init__(self, registry, verify, service, search, credentials, employee):
        self.registry_type=registry
        self.hosts = {row.host: row for row in registry.defaults()}
        self.registry=None
        self.trusted_custom=set()
        self.verify, self.service, self.search = verify, service, search
        self.credentials, self.employee = credentials, employee
        self.session = None
        self.identity = None
        self.ops = Operations(self)

    def refresh_hosts(self):
        if self.registry is None:
            self.hosts={r.host:r for r in self.registry_type.defaults()}
        else:
            self.hosts={r.host:r for r in self.registry.all(False) if r.builtin or r.host in self.trusted_custom}

    def dispatch(self, method, args, cancel=None, emit=None):
        if method == "status":
            return {"authenticated": self.session is not None, "version": "4.0.0-alpha.13"}
        if method == "login":
            self.ops.close()
            self.session = self.identity = None
            username, password = args.get("username"), args.get("password")
            if not isinstance(username, str) or not username.strip() or len(username)>128 or not isinstance(password,str) or not password or len(password)>512:
                raise Rejected("Введите корректный логин и пароль.")
            self.trusted_custom.clear()
            user = self.verify(username.strip(), password, [row.host for row in self.registry_type.defaults()])
            self.refresh_hosts()
            self.session = self.credentials(user.username, password, 8728, 4.5)
            import os
            self.identity = {"username": user.username, "role": "employee" if self.employee(user.username) else "sysadmin", "writes": os.environ.get("LV4_ENABLE_WRITES")=="1", "destructive": os.environ.get("LV4_ENABLE_WRITES")=="1" and os.environ.get("LV4_ENABLE_DESTRUCTIVE")=="1" and not self.employee(user.username)}
            return dict(self.identity)
        if method not in {"servers", "search", "analyze"} | Operations.METHODS:
            raise Rejected("Неизвестная операция.")
        if self.session is None:
            raise Rejected("Сессия не активна. Войдите снова.")
        if method in Operations.METHODS:
            return self.ops.dispatch(method,args,cancel,emit)
        if method == "servers":
            return [{"host": r.host, "country": r.country} for r in self.hosts.values()]
        if method == "analyze":
            host = args.get("host")
            if not isinstance(host,str) or host not in self.hosts:
                raise Rejected("Сервер отсутствует в доверенном списке.")
            if self.identity["role"]!="sysadmin": raise Rejected("Раздел доступен только системному администратору.")
            return asdict(self.service.analyze_server_quick(host, self.session))
        query, mode = args.get("query"), args.get("mode")
        if not isinstance(query,str) or not query.strip() or len(query)>128:
            raise Rejected("Введите логин или порт.")
        query = query.strip()
        if mode == "login":
            report = self.search.search_login_all(list(self.hosts), self.session, query, cancel_event=cancel)
        elif mode == "port" and query.isascii() and query.isdecimal() and 1<=int(query)<=65535:
            report = self.search.search_port_all(list(self.hosts), self.session, int(query), cancel_event=cancel)
        else:
            raise Rejected("Введите порт от 1 до 65535 или выберите поиск по логину.")
        deleted,archive_error=[],None
        if mode=="login" and not report.matches and not (cancel and cancel.is_set()):
            if emit: emit("Проверяю архив PostgreSQL")
            deleted,archive_error=self.ops.extra.search_cloud_archive(query)
        # Explicit public projection: do not send passwords, raw snapshots,
        # credentials, internal IDs or raw exception details to Electron.
        fields = ("server", "login", "remote_address", "ports", "active_ports", "disabled_ports", "is_online", "is_enabled", "uptime", "last_logged_out")
        return {"matches": [{key: getattr(row,key) for key in fields} for row in report.matches],
                "checked": report.checked, "total": report.total,
                "errors": [{"server": err.server} for err in report.errors], "deleted":deleted, "archive_error":archive_error}

def build():
    from linkvideo_vpn_helper.services.server_registry import ServerRegistry
    from linkvideo_vpn_helper.services.routeros_login_verification import verify_routeros_user
    from linkvideo_vpn_helper.services.vpn_service import VPNService, SessionCredentials
    from linkvideo_vpn_helper.services.search_service import FastSearchService
    from linkvideo_vpn_helper.services.access_policy import is_employee_username
    from linkvideo_vpn_helper.services.access_policy import install_service_access_policy
    from linkvideo_vpn_helper.services.runtime_hardening import install_service_runtime_hardening
    from linkvideo_vpn_helper.services.routeros_search_compat import install_routeros_search_compat
    from linkvideo_vpn_helper.services.vpn_automation_resilience import install_vpn_automation_resilience
    from linkvideo_vpn_helper.services.vpn_retention_policy import install_retention_policy
    from linkvideo_vpn_helper.services.vpn_retention_seed_guard import install_retention_seed_guard
    install_service_runtime_hardening()
    install_routeros_search_compat()
    # Match release 3.0.27: install service role guards AFTER NAT compatibility,
    # so its internal rollback can undo newly added rules even for employees.
    install_service_access_policy()
    install_vpn_automation_resilience()
    install_retention_policy()
    install_retention_seed_guard()
    from linkvideo_vpn_helper.services.vpn_sheets_retention_compat import install_vpn_sheets_retention_compat
    from linkvideo_vpn_helper.services.vpn_sheets_resilience import install_vpn_sheets_resilience
    from linkvideo_vpn_helper.services.vpn_sheets_operator_view import install_vpn_sheets_operator_view
    install_vpn_sheets_retention_compat()
    install_vpn_sheets_resilience()
    install_vpn_sheets_operator_view()
    from linkvideo_vpn_helper.services.vpn_restore_compact_ports import install_vpn_restore_compact_ports
    install_vpn_restore_compact_ports()
    service = VPNService()
    workspace=Workspace(ServerRegistry, verify_routeros_user, service, FastSearchService(service), SessionCredentials, is_employee_username)
    import os
    from pathlib import Path
    from desktop_v4.settings_store import ProfileSettings
    profile=Path(os.environ.get('LV4_DATA_DIR') or (Path.home()/'.linkvideo-helper-v4'))
    profile.mkdir(parents=True,exist_ok=True)
    workspace.registry=ServerRegistry(ProfileSettings(profile/'servers.json', legacy=profile/'servers.ini'))
    return workspace

def main():
    from linkvideo_vpn_helper.services.routeros_login_verification import LoginRejected, LoginUnavailable
    workspace=build()
    lock=Lock()
    output=Lock()
    active={}
    def send(data):
        with output:
            WIRE.write(json.dumps(data,ensure_ascii=False,default=str)+"\n")
            WIRE.flush()
    def run(ident,method,args,cancel):
        try:
            def emit(label,value=None):
                send({"id":ident,"event":"progress","label":str(label)[:100],"value":value})
            result=workspace.dispatch(method,args,cancel,emit)
            response={"id":ident,"ok":True,"result":result}
        except (Rejected, InputError, LoginRejected, LoginUnavailable) as exc:
            response={"id":ident,"ok":False,"error":str(exc)}
        except Exception:
            response={"id":ident,"ok":False,"error":"Операция не подтверждена. Проверьте состояние на сервере перед повтором."}
        with lock:
            active.clear()
            send(response)
    for line in sys.stdin:
        try:
            req=json.loads(line)
            ident=req["id"]
            if type(ident) is not int: continue
            if req.get("method")=="cancel":
                with lock:
                    if active.get("id")==ident: active["event"].set()
                continue
            if not isinstance(req.get("args"),dict): continue
            with lock:
                if active:
                    send({"id":ident,"ok":False,"error":"Операция уже выполняется."})
                    continue
                event=Event()
                active.update(id=ident,event=event)
                Thread(target=run,args=(ident,req.get("method"),req["args"],event),daemon=True).start()
        except (ValueError, TypeError, KeyError):
            continue
    with lock:
        if active: active["event"].set()

if __name__=="__main__":
    from desktop_v4.background import dispatch_cli
    code=dispatch_cli(sys.argv[1:])
    if code is not None: sys.exit(code)
    main()
