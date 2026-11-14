"""Private stdio RPC for the Electron preview. No HTTP listener or writes."""
from __future__ import annotations
import json
import sys
from dataclasses import asdict

# Keep the wire separate from library stdout.
WIRE = sys.stdout
sys.stdout = sys.stderr

class Rejected(Exception):
    pass

class Workspace:
    def __init__(self, registry, verify, service, search, credentials, employee):
        self.hosts = {row.host: row for row in registry.defaults()}
        self.verify, self.service, self.search = verify, service, search
        self.credentials, self.employee = credentials, employee
        self.session = None
        self.identity = None

    def dispatch(self, method, args):
        if method == "status":
            return {"authenticated": self.session is not None, "version": "4.0.0-alpha.1"}
        if method == "login":
            self.session = self.identity = None
            username, password = args.get("username"), args.get("password")
            if not isinstance(username, str) or not username.strip() or len(username)>128 or not isinstance(password,str) or not password or len(password)>512:
                raise Rejected("Введите корректный логин и пароль.")
            user = self.verify(username.strip(), password, list(self.hosts))
            self.session = self.credentials(user.username, password, 8728, 4.5)
            self.identity = {"username": user.username, "role": "employee" if self.employee(user.username) else "sysadmin"}
            return dict(self.identity)
        if method not in {"servers", "search", "analyze"}:
            raise Rejected("Неизвестная операция.")
        if self.session is None:
            raise Rejected("Сессия не активна. Войдите снова.")
        if method == "servers":
            return [{"host": r.host, "country": r.country} for r in self.hosts.values()]
        if method == "analyze":
            host = args.get("host")
            if not isinstance(host,str) or host not in self.hosts:
                raise Rejected("Сервер отсутствует в доверенном списке.")
            return asdict(self.service.analyze_server_quick(host, self.session))
        query, mode = args.get("query"), args.get("mode")
        if not isinstance(query,str) or not query.strip() or len(query)>128:
            raise Rejected("Введите логин или порт.")
        query = query.strip()
        if mode == "login":
            report = self.search.search_login_all(list(self.hosts), self.session, query)
        elif mode == "port" and query.isascii() and query.isdecimal() and 1<=int(query)<=65535:
            report = self.search.search_port_all(list(self.hosts), self.session, int(query))
        else:
            raise Rejected("Введите порт от 1 до 65535 или выберите поиск по логину.")
        # Explicit public projection: do not send passwords, raw snapshots,
        # credentials, internal IDs or raw exception details to Electron.
        fields = ("server", "login", "remote_address", "ports", "active_ports", "disabled_ports", "is_online", "is_enabled", "uptime", "last_logged_out")
        return {"matches": [{key: getattr(row,key) for key in fields} for row in report.matches],
                "checked": report.checked, "total": report.total,
                "errors": [{"server": err.server} for err in report.errors]}

def build():
    from linkvideo_vpn_helper.services.server_registry import ServerRegistry
    from linkvideo_vpn_helper.services.routeros_login_verification import verify_routeros_user
    from linkvideo_vpn_helper.services.vpn_service import VPNService, SessionCredentials
    from linkvideo_vpn_helper.services.search_service import FastSearchService
    from linkvideo_vpn_helper.services.access_policy import is_employee_username
    service = VPNService()
    return Workspace(ServerRegistry, verify_routeros_user, service, FastSearchService(service), SessionCredentials, is_employee_username)

def main():
    from linkvideo_vpn_helper.services.routeros_login_verification import LoginRejected, LoginUnavailable
    workspace = build()
    for line in sys.stdin:
        ident = None
        try:
            req = json.loads(line)
            ident = req["id"]
            if type(ident) is not int or not isinstance(req.get("args"),dict):
                raise Rejected("Некорректный запрос.")
            result = workspace.dispatch(req.get("method"),req["args"])
            response = {"id":ident,"ok":True,"result":result}
        except (Rejected, LoginRejected, LoginUnavailable) as exc:
            response = {"id":ident,"ok":False,"error":str(exc)}
        except Exception:
            response = {"id":ident,"ok":False,"error":"Операция не выполнена. Проверьте доступность сервера и повторите."}
        WIRE.write(json.dumps(response,ensure_ascii=False)+"\n")
        WIRE.flush()

if __name__=="__main__":
    main()
