from __future__ import annotations

import secrets
import shutil
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Dict, List

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIClient, RouterOSAPIError
from linkvideo_vpn_helper.services.errors import OperationCancelled
from linkvideo_vpn_helper.services.vpn_lifecycle import classify_state, compose_lv_comment, parse_lv_comment

PORT_MIN = 10001
PORT_MAX = 13000
DEFAULT_LOCAL_ADDRESS = "172.31.255.254"
DEFAULT_START_IP = "172.16.1.176"
DEFAULT_SERVICE = "l2tp"

# Совместимость со старым main_window.py
MAX_PORTS_PER_SERVER = 900
SERVER_CAPACITY = MAX_PORTS_PER_SERVER

# Operational capacity policy for active L2TP sessions.
VPN_L2TP_SOFT_LIMIT = 450
VPN_L2TP_WARNING_LIMIT = 480
VPN_L2TP_HARD_LIMIT = 500


@dataclass
class SessionCredentials:
    username: str
    password: str
    port: int = 8728
    timeout: float = 6.0


@dataclass
class PortConflict:
    port: int
    rule_id: str = ""
    owner_login: str = ""
    owner_remote_address: str = ""
    owner_comment: str = ""
    disabled: bool = False

    def owner_text(self) -> str:
        if self.owner_login:
            return self.owner_login
        if self.owner_remote_address:
            return f"IP {self.owner_remote_address}"
        if self.owner_comment:
            return self.owner_comment
        return "другое NAT-правило"


@dataclass
class ClientRecord:
    server: str
    login: str
    password: str
    remote_address: str
    ports: List[int] = field(default_factory=list)
    profile_id: str = ""
    secret_id: str = ""
    nat_rule_ids: List[str] = field(default_factory=list)
    is_online: bool = False
    active_ports: List[int] = field(default_factory=list)
    disabled_ports: List[int] = field(default_factory=list)
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_rate: str = ""
    tx_rate: str = ""
    interface_name: str = ""
    is_enabled: bool = True
    last_logged_out: str = ""
    uptime: str = ""
    port_connections: dict[int, int] = field(default_factory=dict)
    port_bytes: dict[int, int] = field(default_factory=dict)
    # Внешний TCP-порт должен принадлежать только одной LinkVideo-учётке на сервере.
    # Ключ — порт клиента, значение — чужие NAT-правила с тем же dst-port.
    port_conflicts: dict[int, list[PortConflict]] = field(default_factory=dict)

    def copy_text(self) -> str:
        ports_text = ", ".join(map(str, self.ports)) if self.ports else "—"
        return (
            f"Имя: LV_VPN\n"
            f"Протокол: L2TP\n"
            f"Адрес сервера: {self.server}\n"
            f"Логин: {self.login}\n"
            f"Пароль: {self.password}\n"
            f"Remote Address: {self.remote_address}\n"
            f"Порты: {ports_text}"
        )

    def create_result_text(self) -> str:
        return self.copy_text()


@dataclass
class InactiveClientRecord:
    server: str
    login: str
    last_logged_out: str
    last_logged_out_dt: datetime | None
    is_enabled: bool = True
    remote_address: str = ""
    lifecycle_state: str = "U"
    is_online: bool = False
    lifecycle_source: str = "routeros"


@dataclass
class ServerAnalysis:
    server: str
    cpu_load: int | None
    total_memory: int | None
    free_memory: int | None
    memory_usage_percent: int | None
    clients_total: int
    clients_online: int
    ports_total: int
    ports_active: int

    @property
    def ports_used(self) -> int:
        # Совместимость со старым UI: ports_used = всего созданных NAT-портов
        return self.ports_total

    @property
    def free_memory_mb(self) -> int | None:
        return round(self.free_memory / 1024 / 1024) if self.free_memory is not None else None

    @property
    def total_memory_mb(self) -> int | None:
        return round(self.total_memory / 1024 / 1024) if self.total_memory is not None else None

    @property
    def load_percent_from_limit(self) -> int:
        return round((self.ports_total / MAX_PORTS_PER_SERVER) * 100) if MAX_PORTS_PER_SERVER else 0


@dataclass
class ClientDiagnostics:
    server: str
    login: str
    remote_address: str
    is_online: bool
    ports: List[int]
    active_ports: List[int]
    rx_bytes: int
    tx_bytes: int
    rx_rate: str
    tx_rate: str
    password: str
    message: str


class VPNService:
    @staticmethod
    def sort_inactive_records(records: List[InactiveClientRecord], mode: str = "old") -> List[InactiveClientRecord]:
        """Stable lifecycle sorting; records without a date never crash the UI."""
        items = list(records or [])
        oldest_missing = datetime.max
        newest_missing = datetime.min
        if mode == "new":
            return sorted(items, key=lambda x: (x.last_logged_out_dt or newest_missing, x.server.lower(), x.login.lower()), reverse=True)
        if mode == "server":
            return sorted(items, key=lambda x: (x.server.lower(), x.last_logged_out_dt or oldest_missing, x.login.lower()))
        if mode == "login":
            return sorted(items, key=lambda x: (x.login.lower(), x.server.lower(), x.last_logged_out_dt or oldest_missing))
        return sorted(items, key=lambda x: (x.last_logged_out_dt or oldest_missing, x.server.lower(), x.login.lower()))

    @staticmethod
    def _api_print_exact(api: RouterOSAPIClient, path: str, field: str, value: str, proplist: str | None = None) -> list[dict]:
        params = {}
        if proplist:
            params[".proplist"] = proplist
        params[f"?{field}="] = str(value)
        try:
            rows = api.print(path, params)
            if rows:
                return rows
        except Exception:
            pass
        rows = api.print(path)
        return [row for row in rows if str(row.get(field, "") or "").strip() == str(value)]

    def fetch_snapshot(self, server: str, creds: SessionCredentials) -> Dict[str, List[dict]]:
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            snapshot = {
                "secrets": api.print("/ppp/secret"),
                "actives": api.print("/ppp/active"),
                "profiles": api.print("/ppp/profile"),
                "nat_rules": api.print("/ip/firewall/nat"),
                "connections": api.print("/ip/firewall/connection"),
                "resources": api.print("/system/resource"),
                "interfaces": [],
                "traffic_monitors": {},
            }
            # Трафик L2TP в MikroTik виден на динамическом интерфейсе вида <l2tp-login>.
            # В /ppp/active часто нет rx/tx byte/rate, поэтому дополнительно читаем /interface.
            try:
                snapshot["interfaces"] = api.print("/interface")
            except Exception:
                snapshot["interfaces"] = []

            # Скорость Tx/Rx считаем в интерфейсе программы по разнице байтов.
            # Так быстрее и не нагружает MikroTik частыми monitor-traffic по всем L2TP-интерфейсам.
            snapshot["traffic_monitors"] = {}
            return snapshot

    def fetch_config_snapshot(self, server: str, creds: SessionCredentials) -> Dict[str, List[dict]]:
        """Лёгкий snapshot для операций конфигурации без тяжёлого conntrack/interfaces."""
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            return {
                "secrets": api.print("/ppp/secret"),
                "actives": api.print("/ppp/active"),
                "profiles": api.print("/ppp/profile"),
                "nat_rules": api.print("/ip/firewall/nat"),
                "connections": [],
                "resources": [],
                "interfaces": [],
                "traffic_monitors": {},
            }

    def analyze_server_quick(self, server: str, creds: SessionCredentials) -> ServerAnalysis:
        """Быстрый анализ сервера: без conntrack и интерфейсов."""
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            resources = api.print("/system/resource")
            secrets = api.print("/ppp/secret", {".proplist": ".id,name"})
            actives = api.print("/ppp/active", {".proplist": ".id,name,service"})
            nat_rules = api.print("/ip/firewall/nat", {".proplist": ".id,dst-port,to-ports"})
        resource = resources[0] if resources else {}
        total_memory = self._parse_optional_int(resource.get("total-memory"))
        free_memory = self._parse_optional_int(resource.get("free-memory"))
        cpu_load = self._parse_optional_int(resource.get("cpu-load"))
        memory_usage_percent = (
            round(((total_memory - free_memory) / total_memory) * 100)
            if total_memory not in (None, 0) and free_memory is not None else None
        )
        ports_total = len(self._collect_used_ports({"nat_rules": nat_rules}))
        return ServerAnalysis(
            server=server,
            cpu_load=cpu_load,
            total_memory=total_memory,
            free_memory=free_memory,
            memory_usage_percent=memory_usage_percent,
            clients_total=len(secrets),
            clients_online=sum(1 for row in actives if str(row.get("service", "") or "").strip().lower() == "l2tp"),
            ports_total=ports_total,
            ports_active=0,
        )

    def pick_best_server_parallel(self, servers: List[str], creds: SessionCredentials, max_workers: int = 6, cancel_event=None) -> str:
        from linkvideo_vpn_helper.services.errors import classify_exception

        if not servers:
            raise ValueError("В списке нет активных VPN-серверов")
        best_server = None
        best_score = None
        failures: list[tuple[str, str]] = []
        workers = min(max(1, int(max_workers)), max(1, len(servers)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vpn-probe") as pool:
            futures = {pool.submit(self.analyze_server_quick, server, creds): server for server in servers}
            for future in as_completed(futures):
                if cancel_event is not None and cancel_event.is_set():
                    for item in futures:
                        item.cancel()
                    raise OperationCancelled("Операция отменена")
                server = futures[future]
                try:
                    stat = future.result()
                except Exception as exc:
                    failures.append((server, classify_exception(exc).message))
                    continue
                # Если RouterOS не отдал CPU/RAM, сервер не считаем самым
                # свободным только из-за подставленного нуля. Такие данные
                # идут после серверов с полноценной статистикой.
                unknown_metrics = int(stat.cpu_load is None) + int(stat.memory_usage_percent is None)
                # Capacity gate: new clients must never be placed on a server
                # that is already close to the operational <500 L2TP limit.
                if stat.clients_online >= VPN_L2TP_SOFT_LIMIT:
                    failures.append((server, f"{stat.clients_online} активных L2TP — достигнут резервный порог {VPN_L2TP_SOFT_LIMIT}"))
                    continue
                score = (
                    stat.clients_online,
                    unknown_metrics,
                    stat.cpu_load if stat.cpu_load is not None else 10_000,
                    stat.memory_usage_percent if stat.memory_usage_percent is not None else 10_000,
                    stat.ports_total,
                    stat.clients_total,
                )
                if best_score is None or score < best_score:
                    best_score = score
                    best_server = stat.server
        if not best_server:
            detail = "; ".join(f"{host}: {reason}" for host, reason in failures[:5])
            if len(failures) > 5:
                detail += f"; ещё {len(failures) - 5}"
            capacity_failures = [reason for _host, reason in failures if "активных L2TP" in reason]
            if capacity_failures and len(capacity_failures) == len(failures):
                raise RuntimeError(
                    f"Нет VPN-сервера ниже безопасного порога {VPN_L2TP_SOFT_LIMIT} активных L2TP. "
                    "Нового клиента автоматически создавать нельзя." + ((" " + detail) if detail else "")
                )
            raise RuntimeError("Не удалось подобрать доступный VPN-сервер" + ((". " + detail) if detail else ""))
        return best_server

    def fetch_client_snapshot(self, server: str, creds: SessionCredentials, login: str) -> Dict[str, List[dict]]:
        """Точечный snapshot одного клиента без тяжёлого conntrack.

        Для карточки клиента нужны PPP Secret/Profile/Active, NAT и общий трафик
        VPN-интерфейса. Трафик по отдельным NAT-портам намеренно не вычисляется.
        """
        login = str(login or "").strip()
        if not login:
            raise ValueError("Логин клиента не указан")

        def exact(api, path: str, field: str, value: str, proplist: str):
            try:
                rows = api.print(path, {".proplist": proplist, f"?{field}=": value})
                if rows:
                    return rows
            except Exception:
                pass
            rows = api.print(path)
            return [x for x in rows if str(x.get(field, "") or "").strip() == value]

        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            secrets = exact(api, "/ppp/secret", "name", login, ".id,name,password,profile,service,disabled,last-logged-out,remote-address")
            if not secrets:
                return {"secrets": [], "actives": [], "profiles": [], "nat_rules": [], "connections": [], "resources": [], "interfaces": [], "traffic_monitors": {}}
            secret = secrets[0]
            profile_name = str(secret.get("profile", "") or login).strip()
            profiles = exact(api, "/ppp/profile", "name", profile_name, ".id,name,local-address,remote-address")
            actives = exact(
                api,
                "/ppp/active",
                "name",
                login,
                ".id,name,address,caller-id,uptime,encoding,service,bytes,packets,rx-byte,tx-byte,rx-bits-per-second,tx-bits-per-second",
            )
            remote = self._get_client_remote_address(secret, profiles[0] if profiles else {})

            nat_rules = []
            try:
                nat_rules = api.print("/ip/firewall/nat", {".proplist": ".id,chain,protocol,dst-port,to-addresses,to-ports,comment,disabled,bytes,packets", "?comment=": login})
            except Exception:
                pass
            if not nat_rules:
                try:
                    rows = api.print("/ip/firewall/nat")
                    nat_rules = [r for r in rows if self._normalize_ip(self._get_rule_remote(r)) == remote or str(r.get("comment", "") or "").strip() == login]
                except Exception:
                    nat_rules = []

            # Трафик по отдельным NAT-портам в 2.0 не используется.
            # /ip/firewall/connection на реальных RouterOS давал нестабильный
            # результат и мог быть очень большим, поэтому при загрузке одного
            # клиента conntrack больше вообще не запрашиваем.
            connections = []

            interfaces = []
            try:
                interfaces = api.print("/interface", {".proplist": ".id,name,type,running,dynamic,rx-byte,tx-byte,rx-bits-per-second,tx-bits-per-second"})
            except Exception:
                try:
                    interfaces = api.print("/interface")
                except Exception:
                    interfaces = []

        return {
            "secrets": secrets,
            "actives": actives,
            "profiles": profiles,
            "nat_rules": nat_rules,
            "connections": connections,
            "resources": [],
            "interfaces": interfaces,
            "traffic_monitors": {},
        }

    def get_client(
        self,
        server: str,
        creds: SessionCredentials,
        login: str,
        include_port_conflicts: bool = False,
    ) -> ClientRecord | None:
        records = self._build_client_records(server, self.fetch_client_snapshot(server, creds, login))
        client = next((x for x in records if x.login == str(login or "").strip()), None)
        if client is not None and include_port_conflicts and client.ports:
            try:
                client.port_conflicts = self.inspect_port_conflicts(server, creds, client)
            except Exception:
                # Проверка конфликтов — дополнительная защита. Недоступность этой
                # проверки не должна делать карточку клиента недоступной.
                client.port_conflicts = {}
        return client

    def inspect_port_conflicts(
        self,
        server: str,
        creds: SessionCredentials,
        client: ClientRecord,
    ) -> dict[int, list[PortConflict]]:
        """Находит чужие TCP dst-nat правила с портами текущего клиента.

        На одном VPN-сервере один внешний TCP-порт должен однозначно указывать
        на одного клиента. Старые ручные настройки RouterOS иногда оставляют два
        правила с одинаковым ``dst-port``. В таком случае одно правило может
        перекрывать другое по порядку firewall. Helper не исправляет это молча,
        а показывает владельцев конфликта сотруднику.
        """
        ports = sorted({int(p) for p in (client.ports or []) if int(p) > 0})
        if not ports:
            return {}

        conflicts: dict[int, list[PortConflict]] = {}
        all_nat_cache: list[dict] | None = None
        remote_owner_cache: dict[str, list[str]] = {}

        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            def rows_for_port(port: int) -> list[dict]:
                nonlocal all_nat_cache
                params = {
                    ".proplist": ".id,chain,protocol,dst-port,to-addresses,to-ports,comment,disabled",
                    "?dst-port=": str(port),
                }
                try:
                    return api.print("/ip/firewall/nat", params)
                except Exception:
                    if all_nat_cache is None:
                        all_nat_cache = api.print("/ip/firewall/nat")
                    return [
                        row for row in all_nat_cache
                        if port in self._parse_ports(self._get_rule_external_port(row))
                    ]

            def logins_for_remote(remote: str) -> list[str]:
                remote = self._normalize_ip(remote)
                if not remote:
                    return []
                if remote in remote_owner_cache:
                    return remote_owner_cache[remote]
                names: list[str] = []
                try:
                    rows = api.print(
                        "/ppp/secret",
                        {".proplist": ".id,name,remote-address,profile", "?remote-address=": remote},
                    )
                except Exception:
                    rows = api.print("/ppp/secret", {".proplist": ".id,name,remote-address,profile"})
                for row in rows:
                    if self._normalize_ip(row.get("remote-address", "")) == remote:
                        name = str(row.get("name", "") or "").strip()
                        if name and name not in names:
                            names.append(name)
                try:
                    profiles = api.print(
                        "/ppp/profile",
                        {".proplist": ".id,name,remote-address", "?remote-address=": remote},
                    )
                except Exception:
                    profiles = api.print("/ppp/profile", {".proplist": ".id,name,remote-address"})
                for profile in profiles:
                    if self._normalize_ip(profile.get("remote-address", "")) != remote:
                        continue
                    profile_name = str(profile.get("name", "") or "").strip()
                    if not profile_name:
                        continue
                    try:
                        secrets = api.print(
                            "/ppp/secret",
                            {".proplist": ".id,name,profile", "?profile=": profile_name},
                        )
                    except Exception:
                        secrets = api.print("/ppp/secret", {".proplist": ".id,name,profile"})
                    for secret in secrets:
                        if str(secret.get("profile", "") or "").strip() != profile_name:
                            continue
                        name = str(secret.get("name", "") or "").strip()
                        if name and name not in names:
                            names.append(name)
                remote_owner_cache[remote] = names
                return names

            own_ids = {str(x or "").strip() for x in (client.nat_rule_ids or []) if str(x or "").strip()}
            own_remote = self._normalize_ip(client.remote_address)
            for port in ports:
                seen_rules: set[str] = set()
                for row in rows_for_port(port):
                    if port not in self._parse_ports(self._get_rule_external_port(row)):
                        continue
                    protocol = str(row.get("protocol", "tcp") or "tcp").strip().lower()
                    if protocol and protocol != "tcp":
                        continue
                    chain = str(row.get("chain", "dstnat") or "dstnat").strip().lower()
                    if chain and chain != "dstnat":
                        continue
                    rid = str(row.get(".id", "") or "").strip()
                    comment = str(row.get("comment", "") or "").strip()
                    remote = self._normalize_ip(self._get_rule_remote(row))

                    # Собственные правила клиента конфликтом не являются.
                    if rid and rid in own_ids:
                        continue
                    if comment == client.login:
                        continue
                    if own_remote and remote == own_remote:
                        continue

                    key = rid or f"{port}:{remote}:{comment}"
                    if key in seen_rules:
                        continue
                    seen_rules.add(key)

                    owners: list[str] = []
                    if comment:
                        # LinkVideo создаёт NAT с comment=login. Если это старое
                        # пользовательское описание, ниже всё равно покажем IP.
                        owners.append(comment)
                    if remote:
                        resolved = logins_for_remote(remote)
                        for name in resolved:
                            if name not in owners:
                                owners.append(name)
                    if client.login in owners:
                        owners = [x for x in owners if x != client.login]
                    if not owners:
                        owners = [""]
                    for owner in owners:
                        conflicts.setdefault(port, []).append(PortConflict(
                            port=port,
                            rule_id=rid,
                            owner_login=owner,
                            owner_remote_address=remote,
                            owner_comment=comment,
                            disabled=self._is_rule_disabled(row),
                        ))
        return conflicts

    def list_clients(self, server: str, creds: SessionCredentials) -> List[ClientRecord]:
        return self._build_client_records(server, self.fetch_snapshot(server, creds))

    def search_clients(self, server: str, creds: SessionCredentials, query: str) -> List[ClientRecord]:
        q = query.strip()
        clients = self.list_clients(server, creds)
        if not q:
            return clients

        port_query = self._parse_int(q)
        if q.isdigit() and 1 <= len(q) <= 5 and port_query > 0:
            port_matches = [client for client in clients if port_query in client.ports]
            if port_matches:
                return port_matches

        q_lower = q.lower()
        return [client for client in clients if q_lower in client.login.lower() or client.login.lower().startswith(q_lower)]

    def find_client_across_servers(self, servers: List[str], creds: SessionCredentials, query: str) -> ClientRecord | None:
        query = query.strip()
        if not query:
            return None
        partial = None
        for server in servers:
            try:
                clients = self.list_clients(server, creds)
            except Exception:
                continue
            for client in clients:
                if client.login == query:
                    return client
                if partial is None and query.lower() in client.login.lower():
                    partial = client
        return partial

    @staticmethod
    def parse_router_datetime(value: str) -> datetime | None:
        """Разбирает RouterOS last-logged-out без подстановки фиктивной даты."""
        raw = str(value or "").strip()
        if not raw or raw.lower() in {"never", "none", "—", "jan/01/1970 00:00:00", "1970-01-01 00:00:00"}:
            return None
        months = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        m = re.match(r"^([A-Za-z]{3})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})$", raw)
        if m and m.group(1).lower() in months:
            try:
                return datetime(
                    int(m.group(3)), months[m.group(1).lower()], int(m.group(2)),
                    int(m.group(4)), int(m.group(5)), int(m.group(6)),
                )
            except Exception:
                return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(raw[:19], fmt)
            except Exception:
                pass
        return None

    def list_inactive_clients(self, server: str, creds: SessionCredentials, cutoff: datetime) -> List[InactiveClientRecord]:
        """Возвращает только доказанно неактивные учётки старше cutoff.

        Учётка без корректного last-logged-out не попадает в архив: Helper не
        должен угадывать её возраст. Активные PPP-сессии также исключаются.
        """
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            try:
                secrets = api.print(
                    "/ppp/secret",
                    {".proplist": ".id,name,disabled,last-logged-out,remote-address"},
                )
            except Exception:
                secrets = api.print("/ppp/secret")
            try:
                active_rows = api.print("/ppp/active", {".proplist": ".id,name"})
            except Exception:
                active_rows = api.print("/ppp/active")
        active = {str(row.get("name", "") or "").strip() for row in active_rows}
        result: List[InactiveClientRecord] = []
        for secret in secrets:
            login = str(secret.get("name", "") or "").strip()
            if not login or login in active:
                continue
            raw_last = str(secret.get("last-logged-out", "") or secret.get("last_logged_out", "") or "").strip()
            last_dt = self.parse_router_datetime(raw_last)
            if last_dt is None or last_dt > cutoff:
                continue
            disabled_flag = str(secret.get("disabled", "no") or "no").strip().lower()
            result.append(InactiveClientRecord(
                server=server,
                login=login,
                last_logged_out=raw_last,
                last_logged_out_dt=last_dt,
                is_enabled=disabled_flag not in ("yes", "true", "1", "disabled", "disable", "on"),
                remote_address=self._normalize_ip(secret.get("remote-address", "")),
            ))
        result.sort(key=lambda x: (x.last_logged_out_dt, x.login))
        return result

    def list_lifecycle_clients(
        self,
        server: str,
        creds: SessionCredentials,
        min_age_days: int = 30,
        include_unknown: bool = True,
    ) -> List[InactiveClientRecord]:
        """Return lifecycle-oriented VPN records for the redesigned client page.

        LV metadata is authoritative when present. Existing RouterOS
        last-logged-out is used only as a compatibility seed so the UI remains
        useful before LV-Activity has observed every account. No state is
        changed by this read method.
        """
        import time
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            try:
                secrets = api.print(
                    "/ppp/secret",
                    {".proplist": ".id,name,disabled,last-logged-out,remote-address,comment"},
                )
            except Exception:
                secrets = api.print("/ppp/secret")
            try:
                active_rows = api.print("/ppp/active", {".proplist": ".id,name,service"})
            except Exception:
                active_rows = api.print("/ppp/active")
        active = {str(row.get("name", "") or "").strip() for row in active_rows}
        now_ns = time.time_ns()
        result: List[InactiveClientRecord] = []
        for secret in secrets:
            login = str(secret.get("name", "") or "").strip()
            if not login:
                continue
            disabled_flag = str(secret.get("disabled", "no") or "no").strip().lower()
            is_enabled = disabled_flag not in ("yes", "true", "1", "disabled", "disable", "on")
            is_online = login in active
            comment = str(secret.get("comment", "") or "")
            meta = parse_lv_comment(comment)
            has_lv = "|LV1|" in comment
            raw_last = str(secret.get("last-logged-out", "") or secret.get("last_logged_out", "") or "").strip()
            fallback_dt = self.parse_router_datetime(raw_last)
            fallback_ns = int(fallback_dt.timestamp() * 1_000_000_000) if fallback_dt else 0
            last_ns = meta.last_ns if meta.last_ns > 0 else fallback_ns
            if is_online:
                state = "A"
                last_ns = max(last_ns, now_ns)
            elif has_lv:
                state = meta.state or "U"
            else:
                state = classify_state(last_ns, not is_enabled, False)
            last_dt = datetime.fromtimestamp(last_ns / 1_000_000_000) if last_ns > 0 else None
            age_days = max(0, int((now_ns - last_ns) // 86_400_000_000_000)) if last_ns > 0 else None
            visible = state in {"S", "Q", "R", "M"}
            if state == "U" and include_unknown:
                visible = True
            if state == "A" and age_days is not None and age_days >= int(min_age_days):
                visible = True
            if not visible:
                continue
            result.append(InactiveClientRecord(
                server=server,
                login=login,
                last_logged_out=raw_last,
                last_logged_out_dt=last_dt,
                is_enabled=is_enabled,
                remote_address=self._normalize_ip(secret.get("remote-address", "")),
                lifecycle_state=state,
                is_online=is_online,
                lifecycle_source="lv" if has_lv else "routeros",
            ))
        return self.sort_inactive_records(result, "old")

    def get_client_diagnostics(self, server: str, creds: SessionCredentials, login: str) -> ClientDiagnostics:
        client = next((c for c in self.list_clients(server, creds) if c.login == login), None)
        if not client:
            raise ValueError("Клиент не найден")

        if not client.is_online:
            message = "VPN не поднят"
        elif client.ports:
            message = "VPN онлайн, NAT-порты настроены"
        else:
            message = "Клиент найден, NAT-порты не настроены"

        return ClientDiagnostics(
            server=client.server,
            login=client.login,
            remote_address=client.remote_address,
            is_online=client.is_online,
            ports=client.ports,
            active_ports=client.active_ports,
            rx_bytes=client.rx_bytes,
            tx_bytes=client.tx_bytes,
            rx_rate=client.rx_rate,
            tx_rate=client.tx_rate,
            password=client.password,
            message=message,
        )

    def suggest_next_login(self, server: str, creds: SessionCredentials, base_login: str) -> str:
        base_login = base_login.strip()
        snapshot = self.fetch_config_snapshot(server, creds)
        existing = {str(x.get("name", "")).strip() for x in snapshot.get("secrets", [])}
        if base_login not in existing:
            return base_login
        idx = 1
        while True:
            candidate = f"{base_login}_{idx}"
            if candidate not in existing:
                return candidate
            idx += 1

    def analyze_server(self, server: str, creds: SessionCredentials) -> ServerAnalysis:
        snapshot = self.fetch_snapshot(server, creds)
        clients = self._build_client_records(server, snapshot)
        resource = snapshot["resources"][0] if snapshot["resources"] else {}
        total_memory = self._parse_optional_int(resource.get("total-memory"))
        free_memory = self._parse_optional_int(resource.get("free-memory"))
        cpu_load = self._parse_optional_int(resource.get("cpu-load"))
        memory_usage_percent = (
            round(((total_memory - free_memory) / total_memory) * 100)
            if total_memory not in (None, 0) and free_memory is not None else None
        )

        ports_total = len(self._collect_used_ports(snapshot))
        active_port_set = set()
        for client in clients:
            active_port_set.update(client.active_ports)

        return ServerAnalysis(
            server=server,
            cpu_load=cpu_load,
            total_memory=total_memory,
            free_memory=free_memory,
            memory_usage_percent=memory_usage_percent,
            clients_total=len(clients),
            clients_online=sum(1 for c in clients if c.is_online),
            ports_total=ports_total,
            ports_active=len(active_port_set),
        )

    def pick_best_server(self, servers: List[str], creds: SessionCredentials) -> str:
        return self.pick_best_server_parallel(servers, creds)

    def create_client(self, server: str, creds: SessionCredentials, login: str, port_count: int) -> ClientRecord:
        records = self.create_clients_batch(server, creds, login, port_count, 1)
        if not records:
            raise RuntimeError("Клиент не был создан")
        return records[0]

    def create_clients_batch(self, server: str, creds: SessionCredentials, base_login: str, ports_per_client: int, accounts_count: int, progress_callback=None, cancel_event=None) -> List[ClientRecord]:
        base_login = base_login.strip()
        if not base_login:
            raise ValueError("Логин не может быть пустым")
        if accounts_count < 1 or accounts_count > 20:
            raise ValueError("Количество учеток должно быть от 1 до 20")
        if ports_per_client < 1 or ports_per_client > 14:
            raise ValueError("Количество портов должно быть от 1 до 14")

        def check_cancel():
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelled("Операция отменена пользователем")

        check_cancel()
        # Один лёгкий snapshot без conntrack/interfaces. Это заметно быстрее старой схемы.
        snapshot = self.fetch_config_snapshot(server, creds)
        check_cancel()
        existing_logins = {str(x.get("name", "")).strip() for x in snapshot.get("secrets", []) if str(x.get("name", "")).strip()}
        used_ips = self._collect_used_remote_addresses(snapshot)
        used_ports = self._collect_used_ports(snapshot)

        planned: List[tuple[str, str, List[int], str]] = []
        for idx in range(accounts_count):
            check_cancel()
            desired = base_login if idx == 0 else f"{base_login}_{idx}"
            login = desired
            if login in existing_logins:
                suffix = 1
                while f"{base_login}_{suffix}" in existing_logins:
                    suffix += 1
                login = f"{base_login}_{suffix}"
            existing_logins.add(login)

            remote_address = self._find_next_ip(used_ips)
            used_ips.add(remote_address)
            ports = self._find_free_ports(used_ports, ports_per_client)
            if len(ports) < ports_per_client:
                raise ValueError("Недостаточно свободных портов на сервере")
            used_ports.update(ports)
            planned.append((login, remote_address, ports, self._generate_password(8)))

        created: List[ClientRecord] = []
        # Ledger is registered immediately after each successful RouterOS add.
        # This guarantees rollback even if the next step fails or the user cancels.
        created_ids: List[dict[str, object]] = []
        try:
            # Одна API-сессия на весь batch вместо нового TCP/login на каждую учётку.
            with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
                total_planned = max(1, len(planned))
                for create_index, (login, remote_address, ports, password) in enumerate(planned, 1):
                    check_cancel()
                    if progress_callback:
                        try:
                            progress_callback(create_index - 1, total_planned, login)
                        except Exception:
                            pass
                    check_cancel()
                    profile_id = api.add("/ppp/profile", {
                        "name": login,
                        "local-address": DEFAULT_LOCAL_ADDRESS,
                        "remote-address": remote_address,
                        "use-upnp": "no",
                        "change-tcp-mss": "no",
                        "use-ipv6": "no",
                        "use-mpls": "no",
                        "use-compression": "no",
                        "use-encryption": "default",
                        "only-one": "no",
                    })
                    nat_ids: List[str] = []
                    ledger = {"profile_id": profile_id, "secret_id": "", "nat_ids": nat_ids}
                    created_ids.append(ledger)
                    check_cancel()
                    secret_id = api.add("/ppp/secret", {
                        "name": login,
                        "password": password,
                        "profile": login,
                        "service": DEFAULT_SERVICE,
                        "comment": login,
                        "disabled": "no",
                    })
                    ledger["secret_id"] = secret_id
                    for external_port in ports:
                        check_cancel()
                        # Точечная проверка вместо повторного полного snapshot.
                        try:
                            rows = api.print("/ip/firewall/nat", {
                                ".proplist": ".id,dst-port,to-ports",
                                "?dst-port=": str(external_port),
                            })
                            if any(external_port in self._parse_ports(self._get_rule_external_port(x)) for x in rows):
                                raise ValueError(f"Порт {external_port} уже занят. Повторите создание.")
                        except RouterOSAPIError:
                            raise
                        except ValueError:
                            raise
                        except Exception:
                            # Старый RouterOS: initial snapshot всё равно защищает от большинства конфликтов.
                            pass
                        nat_ids.append(api.add("/ip/firewall/nat", {
                            "chain": "dstnat",
                            "protocol": "tcp",
                            "dst-port": str(external_port),
                            "action": "dst-nat",
                            "to-addresses": remote_address,
                            "to-ports": str(external_port),
                            "comment": login,
                            "disabled": "no",
                        }))
                    check_cancel()
                    created.append(ClientRecord(
                        server=server,
                        login=login,
                        password=password,
                        remote_address=remote_address,
                        ports=list(ports),
                        profile_id=profile_id,
                        secret_id=secret_id,
                        nat_rule_ids=list(nat_ids),
                        is_enabled=True,
                    ))
                    if progress_callback:
                        try:
                            progress_callback(create_index, total_planned, login)
                        except Exception:
                            pass
        except Exception:
            # Rollback всего batch, включая объект, на котором ошибка произошла
            # между созданием Profile/Secret/NAT.
            for item in reversed(created_ids):
                self._rollback_create(
                    server,
                    creds,
                    str(item.get("profile_id", "") or ""),
                    str(item.get("secret_id", "") or ""),
                    list(item.get("nat_ids", []) or []),
                )
            raise

        return created

    def add_ports(self, server: str, creds: SessionCredentials, login: str, count: int) -> ClientRecord:
        if count < 1 or count > 14:
            raise ValueError("Количество портов должно быть от 1 до 14")
        client = self.get_client(server, creds, login)
        if not client:
            raise ValueError("Клиент не найден")
        if not client.remote_address:
            raise ValueError("У клиента не определён Remote Address")

        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            # Для выбора свободных внешних портов нужны только NAT-правила, а не
            # полный snapshot всех PPP/интерфейсов/conntrack.
            nat_rules = api.print("/ip/firewall/nat", {".proplist": ".id,dst-port,to-addresses,to-ports,comment,disabled"})
            used_ports = self._collect_used_ports({"nat_rules": nat_rules})
            new_ports = self._find_free_ports(used_ports, count)
            if len(new_ports) < count:
                raise ValueError("Недостаточно свободных портов")
            added_ids: List[str] = []
            try:
                for external_port in new_ports:
                    # Повторная точечная проверка непосредственно перед add.
                    rows = self._api_print_exact(api, "/ip/firewall/nat", "dst-port", str(external_port), ".id,dst-port")
                    if any(external_port in self._parse_ports(self._get_rule_external_port(x)) for x in rows):
                        raise ValueError(f"Порт {external_port} уже занят. Повторите операцию.")
                    added_ids.append(api.add("/ip/firewall/nat", {
                        "chain": "dstnat", "protocol": "tcp",
                        "dst-port": str(external_port), "action": "dst-nat",
                        "to-addresses": client.remote_address, "to-ports": str(external_port),
                        "comment": login, "disabled": "no",
                    }))
            except Exception:
                # Операция добавления нескольких портов атомарна для Helper:
                # если один add не прошёл, удаляем всё, что добавили в этой операции.
                for rid in reversed(added_ids):
                    try:
                        if rid:
                            api.remove("/ip/firewall/nat", rid)
                    except Exception:
                        pass
                raise

        refreshed = self.get_client(server, creds, login)
        if not refreshed:
            raise ValueError("Не удалось обновить данные клиента после добавления портов")
        return refreshed

    def remove_port(self, server: str, creds: SessionCredentials, login: str, port: int) -> ClientRecord:
        snapshot = self.fetch_client_snapshot(server, creds, login)
        client = next((c for c in self._build_client_records(server, snapshot) if c.login == login), None)
        if not client:
            raise ValueError("Клиент не найден")

        target_ids = []
        for rule in snapshot["nat_rules"]:
            if self._normalize_ip(self._get_rule_remote(rule)) != client.remote_address:
                continue
            if port in self._parse_ports(self._get_rule_external_port(rule)):
                rid = str(rule.get(".id", "")).strip()
                if rid:
                    target_ids.append(rid)
        if not target_ids:
            raise ValueError("NAT правило для порта не найдено")

        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            for rid in target_ids:
                api.remove("/ip/firewall/nat", rid)
        refreshed = self.get_client(server, creds, login)
        if not refreshed:
            raise ValueError("Не удалось обновить данные клиента после удаления порта")
        return refreshed

    def set_password(self, server: str, creds: SessionCredentials, login: str, new_password: str) -> ClientRecord:
        password = str(new_password or "").strip()
        if not password:
            raise ValueError("Новый пароль не может быть пустым")
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            rows = self._api_print_exact(api, "/ppp/secret", "name", login, ".id,name")
            if not rows:
                raise ValueError("PPP Secret не найден")
            item_id = str(rows[0].get(".id", "") or "").strip()
            if not item_id:
                raise ValueError("RouterOS не вернул ID PPP Secret")
            api.set("/ppp/secret", item_id, {"password": password})
        refreshed = self.get_client(server, creds, login)
        if not refreshed:
            raise ValueError("Не удалось обновить данные клиента после смены пароля")
        return refreshed

    def set_secret_enabled(self, server: str, creds: SessionCredentials, login: str, enabled: bool) -> ClientRecord:
        """Manual enable/disable from Helper is marked separately from quarantine.

        A manually disabled account gets LV state=M so server-side AutoRestore
        can never silently undo an operator decision. Manual enable returns it
        to A and records the current moment as a safe last-seen baseline.
        """
        import time
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            rows = self._api_print_exact(api, "/ppp/secret", "name", login, ".id,name,disabled,comment")
            if not rows:
                raise ValueError("PPP Secret не найден")
            item_id = str(rows[0].get(".id", "") or "").strip()
            if not item_id:
                raise ValueError("RouterOS не вернул ID PPP Secret")
            meta = parse_lv_comment(str(rows[0].get("comment", "") or ""))
            state = "A" if enabled else "M"
            last_ns = time.time_ns() if enabled else meta.last_ns
            api.set("/ppp/secret", item_id, {
                "disabled": "no" if enabled else "yes",
                "comment": compose_lv_comment(meta.base_comment, state, last_ns),
            })
        refreshed = self.get_client(server, creds, login)
        if not refreshed:
            raise ValueError("Не удалось обновить данные клиента")
        return refreshed

    def set_port_enabled(self, server: str, creds: SessionCredentials, login: str, port: int, enabled: bool) -> ClientRecord:
        snapshot = self.fetch_client_snapshot(server, creds, login)
        client = next((c for c in self._build_client_records(server, snapshot) if c.login == login), None)
        if not client:
            raise ValueError("Клиент не найден")

        rule_ids = []
        for rule in snapshot["nat_rules"]:
            if self._normalize_ip(self._get_rule_remote(rule)) != client.remote_address:
                continue
            if port in self._parse_ports(self._get_rule_external_port(rule)):
                rid = str(rule.get(".id", "")).strip()
                if rid:
                    rule_ids.append(rid)
        if not rule_ids:
            raise ValueError("Порт не найден у клиента")

        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            for rid in rule_ids:
                api.set("/ip/firewall/nat", rid, {"disabled": "no" if enabled else "yes"})
        refreshed = self.get_client(server, creds, login)
        if not refreshed:
            raise ValueError("Не удалось обновить данные клиента")
        return refreshed

    def recreate_port(self, server: str, creds: SessionCredentials, login: str, port: int) -> ClientRecord:
        """Пересоздаёт одно dstnat-правило клиента с тем же внешним портом."""
        snapshot = self.fetch_config_snapshot(server, creds)
        client = next((c for c in self._build_client_records(server, snapshot) if c.login == login), None)
        if not client:
            raise ValueError("Клиент не найден")
        if not client.remote_address:
            raise ValueError("У клиента не определён Remote Address")
        target_ids = []
        for rule in snapshot.get("nat_rules", []):
            if self._normalize_ip(self._get_rule_remote(rule)) != client.remote_address:
                continue
            if int(port) in self._parse_ports(self._get_rule_external_port(rule)):
                rid = str(rule.get(".id", "") or "").strip()
                if rid:
                    target_ids.append(rid)
        # Не допускаем захват чужого порта.
        for rule in snapshot.get("nat_rules", []):
            if int(port) not in self._parse_ports(self._get_rule_external_port(rule)):
                continue
            if self._normalize_ip(self._get_rule_remote(rule)) != client.remote_address:
                raise ValueError(f"Порт {port} занят другим NAT-правилом")
        target_rules = [
            rule for rule in snapshot.get("nat_rules", [])
            if str(rule.get(".id", "") or "").strip() in set(target_ids)
        ]
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            removed_payloads: List[dict] = []
            try:
                for rule in target_rules:
                    rid = str(rule.get(".id", "") or "").strip()
                    payload = self._nat_restore_payload(rule, login, client.remote_address, int(port))
                    api.remove("/ip/firewall/nat", rid)
                    removed_payloads.append(payload)
                api.add("/ip/firewall/nat", {
                    "chain": "dstnat",
                    "protocol": "tcp",
                    "dst-port": str(int(port)),
                    "action": "dst-nat",
                    "to-addresses": client.remote_address,
                    "to-ports": str(int(port)),
                    "comment": login,
                    "disabled": "no",
                })
            except Exception as exc:
                restore_errors: List[str] = []
                for payload in removed_payloads:
                    try:
                        api.add("/ip/firewall/nat", payload)
                    except Exception as restore_exc:
                        restore_errors.append(str(restore_exc))
                if restore_errors:
                    raise RuntimeError(
                        f"Не удалось пересоздать порт {port}; восстановление старого NAT также завершилось ошибкой: "
                        + "; ".join(restore_errors)
                    ) from exc
                raise
        refreshed = self.get_client(server, creds, login)
        if not refreshed:
            raise ValueError("Не удалось обновить данные после пересоздания порта")
        return refreshed

    def delete_client(self, server: str, creds: SessionCredentials, login: str) -> None:
        snapshot = self.fetch_client_snapshot(server, creds, login)
        secret = next((x for x in snapshot.get("secrets", []) if str(x.get("name", "") or "").strip() == login), None)
        if not secret:
            raise ValueError("Клиент не найден")
        profile_name = str(secret.get("profile", "") or login).strip()
        profile = next((x for x in snapshot.get("profiles", []) if str(x.get("name", "") or "").strip() == profile_name), None)
        nat_ids = [str(x.get(".id", "") or "").strip() for x in snapshot.get("nat_rules", []) if str(x.get(".id", "") or "").strip()]
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            for rid in nat_ids:
                api.remove("/ip/firewall/nat", rid)
            secret_id = str(secret.get(".id", "") or "").strip()
            if secret_id:
                api.remove("/ppp/secret", secret_id)
            if profile:
                profile_id = str(profile.get(".id", "") or "").strip()
                if profile_id:
                    api.remove("/ppp/profile", profile_id)

    def disconnect_client_session(self, server: str, creds: SessionCredentials, login: str) -> bool:
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            rows = self._api_print_exact(api, "/ppp/active", "name", login, ".id,name")
            if not rows:
                return False
            item_id = str(rows[0].get(".id", "") or "").strip()
            if not item_id:
                return False
            api.remove("/ppp/active", item_id)
        return True

    def open_in_winbox(self, server: str, username: str, password: str) -> None:
        candidate_paths = [
            shutil.which("winbox64.exe"),
            shutil.which("winbox.exe"),
            r"C:\Program Files\Winbox\winbox64.exe",
            r"C:\Program Files (x86)\Winbox\winbox.exe",
            r"C:\Winbox\winbox64.exe",
        ]
        executable = next((p for p in candidate_paths if p and (shutil.which(p) or self._file_exists(p))), None)
        if not executable:
            raise FileNotFoundError("WinBox не найден. Установи WinBox или добавь его в PATH.")
        subprocess.Popen([executable, server, username, password])

    def _connection_port_stats(self, connections: List[dict], client_ports: List[int]) -> tuple[dict[int, int], dict[int, int]]:
        wanted = set(int(x) for x in client_ports or [])
        counts = {p: 0 for p in wanted}
        bytes_map = {p: 0 for p in wanted}
        if not wanted:
            return counts, bytes_map
        for connection in connections or []:
            # При fallback RouterOS может вернуть весь conntrack. Для статуса
            # проброшенного порта учитываем только соединения, прошедшие DST-NAT,
            # если такой признак присутствует в записи.
            dstnat_value = str(connection.get("dstnat", "") or "").strip().lower()
            if dstnat_value and dstnat_value not in ("yes", "true", "1"):
                continue
            matched = set()
            for key in ("dst-port", "reply-dst-port", "src-port", "reply-src-port", "dst_port", "reply_dst_port", "src_port", "reply_src_port"):
                for p in self._parse_ports(str(connection.get(key, "") or "")):
                    if p in wanted:
                        matched.add(p)
            if not matched:
                continue
            total_bytes = 0
            for key in ("orig-bytes", "repl-bytes", "bytes", "orig_bytes", "repl_bytes"):
                total_bytes += max(0, self._parse_int(connection.get(key, 0)))
            for p in matched:
                counts[p] = counts.get(p, 0) + 1
                bytes_map[p] = bytes_map.get(p, 0) + total_bytes
        return counts, bytes_map

    def _build_client_records(self, server: str, snapshot: Dict[str, List[dict]]) -> List[ClientRecord]:
        active_by_name = {str(item.get("name", "")).strip(): item for item in snapshot["actives"] if str(item.get("name", "")).strip()}
        profiles_by_name = {str(item.get("name", "")).strip(): item for item in snapshot["profiles"] if str(item.get("name", "")).strip()}
        nat_by_remote: Dict[str, List[dict]] = {}
        for rule in snapshot["nat_rules"]:
            remote = self._normalize_ip(self._get_rule_remote(rule))
            if remote:
                nat_by_remote.setdefault(remote, []).append(rule)

        # Активность портов считаем только по /ip/firewall/connection.
        # NAT counters не используем: они накопительные и дают ложный зелёный статус.
        connection_active_ports = self._build_connection_active_port_map(snapshot["connections"])

        records: List[ClientRecord] = []
        for secret in snapshot["secrets"]:
            login = str(secret.get("name", "")).strip()
            if not login:
                continue
            profile = profiles_by_name.get(login, {})
            remote_address = self._get_client_remote_address(secret, profile)
            nat_rules = nat_by_remote.get(remote_address, [])

            ports: List[int] = []
            nat_rule_ids: List[str] = []
            disabled_ports_set = set()
            for rule in nat_rules:
                rule_ports = self._parse_ports(self._get_rule_external_port(rule))
                ports.extend(rule_ports)
                rid = str(rule.get(".id", "")).strip()
                if rid:
                    nat_rule_ids.append(rid)
                if self._is_rule_disabled(rule):
                    disabled_ports_set.update(rule_ports)

            ports = sorted(set(ports))

            active = active_by_name.get(login)
            # Если VPN-сессии нет в /ppp/active, порт не считаем активным,
            # даже если в conntrack остались старые соединения.
            active_ports = sorted(p for p in ports if active and p in connection_active_ports and p not in disabled_ports_set)
            port_connections, port_bytes = self._connection_port_stats(snapshot.get("connections", []), ports)

            traffic_interface = self._find_client_interface(login, remote_address, snapshot.get("interfaces", []))
            interface_name = str((traffic_interface or {}).get("name", "") or "").strip()
            monitor_source = (snapshot.get("traffic_monitors", {}) or {}).get(interface_name, {})
            traffic_source = traffic_interface or active
            rx_bytes, tx_bytes = self._extract_bytes(traffic_source)
            rx_rate = self._extract_rate(monitor_source or traffic_source, "rx")
            tx_rate = self._extract_rate(monitor_source or traffic_source, "tx")
            disabled_flag = str(secret.get("disabled", "no")).strip().lower()
            records.append(ClientRecord(
                server=server,
                login=login,
                password=str(secret.get("password", "")).strip(),
                remote_address=remote_address,
                ports=ports,
                profile_id=str(profile.get(".id", "")).strip(),
                secret_id=str(secret.get(".id", "")).strip(),
                nat_rule_ids=nat_rule_ids,
                is_online=bool(active),
                active_ports=active_ports,
                disabled_ports=sorted(disabled_ports_set),
                rx_bytes=rx_bytes,
                tx_bytes=tx_bytes,
                rx_rate=rx_rate,
                tx_rate=tx_rate,
                interface_name=interface_name,
                is_enabled=(str(disabled_flag).strip().lower() not in ("yes", "true", "1", "disabled", "disable", "on")),
                last_logged_out=str(secret.get("last-logged-out", "") or secret.get("last_logged_out", "")).strip(),
                uptime=str((active or {}).get("uptime", "") or "").strip(),
                port_connections=port_connections,
                port_bytes=port_bytes,
            ))
        records.sort(key=lambda x: x.login)
        return records

    def _is_rule_disabled(self, rule: dict) -> bool:
        return str(rule.get("disabled", "no")).strip().lower() in ("yes", "true", "1")

    def _rule_has_any_traffic(self, rule: dict) -> bool:
        for key in ("bytes", "packets", "packet-count", "packet-bytes", "orig-bytes", "repl-bytes"):
            if self._parse_int(rule.get(key, 0)) > 0:
                return True
        return False

    def _collect_used_remote_addresses(self, snapshot: Dict[str, List[dict]]) -> set[str]:
        used = set()
        for s in snapshot["secrets"]:
            remote = self._normalize_ip(s.get("remote-address", ""))
            if remote:
                used.add(remote)
        for p in snapshot["profiles"]:
            remote = self._normalize_ip(p.get("remote-address", ""))
            if remote:
                used.add(remote)
        return used

    def _collect_used_ports(self, snapshot: Dict[str, List[dict]]) -> set[int]:
        nat_rules = snapshot.get("nat_rules", [])
        used = set()
        for rule in nat_rules:
            for p in self._parse_ports(self._get_rule_external_port(rule)):
                if p:
                    used.add(p)
        return used

    def _get_client_remote_address(self, secret: dict | None, profile: dict | None) -> str:
        secret_remote = self._normalize_ip((secret or {}).get("remote-address", ""))
        return secret_remote or self._normalize_ip((profile or {}).get("remote-address", ""))

    def _get_rule_remote(self, rule: dict) -> str:
        for key in ("to-addresses", "to-address", "to_addresses", "to"):
            value = rule.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return ""

    def _get_rule_external_port(self, rule: dict) -> str:
        for key in ("dst-port", "dst_port"):
            value = rule.get(key)
            if value not in (None, ""):
                return str(value).strip()
        for key in ("to-ports", "to_ports"):
            value = rule.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return ""

    def _build_connection_active_port_map(self, connections: List[dict]) -> set[int]:
        active = set()
        for connection in connections:
            for key in ("dst-port", "reply-dst-port", "src-port", "reply-src-port", "dst_port", "reply_dst_port", "src_port", "reply_src_port"):
                raw = connection.get(key, "")
                if not raw:
                    continue
                for p in self._parse_ports(str(raw)):
                    active.add(p)
        return active

    def _monitor_interface_traffic(self, api: RouterOSAPIClient, interface_name: str) -> dict:
        """Возвращает текущую скорость интерфейса через /interface/monitor-traffic once.
        В разных версиях клиента параметры могут передаваться по-разному, поэтому пробуем несколько вариантов.
        """
        params = {"interface": interface_name, "once": ""}
        attempts = (
            lambda: api.print("/interface/monitor-traffic", params),
            lambda: api.print("/interface/monitor-traffic", {"=interface": interface_name, "=once": ""}),
        )
        for attempt in attempts:
            try:
                result = attempt()
                if isinstance(result, list) and result:
                    return result[0] or {}
                if isinstance(result, dict):
                    return result
            except TypeError:
                continue
            except Exception:
                continue
        return {}

    def _find_client_interface(self, login: str, remote_address: str, interfaces: List[dict]) -> dict | None:
        login_lower = str(login or "").strip().lower()
        remote = self._normalize_ip(remote_address)
        candidates = []
        for iface in interfaces or []:
            name = str(iface.get("name", "") or "").strip()
            name_lower = name.lower()
            if not name:
                continue
            if login_lower and login_lower in name_lower:
                candidates.append(iface)
                continue
            # Иногда у динамического интерфейса может быть адрес клиента в полях actual/remote address.
            for key in ("remote-address", "remote_address", "client-address", "client_address", "address"):
                value = self._normalize_ip(iface.get(key, ""))
                if remote and value == remote:
                    candidates.append(iface)
                    break
        if not candidates:
            return None
        # Предпочитаем динамический running-интерфейс L2TP.
        for iface in candidates:
            name = str(iface.get("name", "") or "").lower()
            if "l2tp" in name and self._is_truthy(iface.get("running", "")):
                return iface
        for iface in candidates:
            if self._is_truthy(iface.get("running", "")):
                return iface
        return candidates[0]

    def _is_truthy(self, value: object) -> bool:
        return str(value or "").strip().lower() in ("true", "yes", "1", "running")

    def _extract_bytes(self, active: dict | None) -> tuple[int, int]:
        if not active:
            return 0, 0
        # В интерфейсах RouterOS поля обычно называются rx-byte/tx-byte.
        rx = self._parse_int(active.get("rx-byte", active.get("rx_bytes", active.get("rx-byte-count", 0))))
        tx = self._parse_int(active.get("tx-byte", active.get("tx_bytes", active.get("tx-byte-count", 0))))
        if rx or tx:
            return rx, tx
        combined = str(active.get("bytes", "0/0"))
        if "/" in combined:
            left, right = combined.split("/", 1)
            return self._parse_int(left), self._parse_int(right)
        return 0, 0

    def _extract_rate(self, active: dict | None, direction: str) -> str:
        if not active:
            return ""
        keys = (
            f"{direction}-rate",
            f"{direction}_rate",
            f"{direction}-bitrate",
            f"{direction}_bitrate",
            f"{direction}-bits-per-second",
            f"{direction}_bits_per_second",
            f"{direction}-bps",
            f"{direction}_bps",
        )
        for key in keys:
            value = active.get(key)
            if value not in (None, ""):
                return self._format_rate_value(value)
        return ""

    def _format_rate_value(self, value: object) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        # Если RouterOS уже вернул строку с единицами, оставляем её как есть.
        if any(ch.isalpha() for ch in raw):
            return raw
        bps = self._parse_int(raw)
        if bps <= 0:
            return "0 bps"
        if bps >= 1_000_000:
            return f"{bps / 1_000_000:.1f} Mbps"
        if bps >= 1_000:
            return f"{bps / 1_000:.1f} kbps"
        return f"{bps} bps"

    @staticmethod
    def _nat_restore_payload(rule: dict, login: str, remote_address: str, port: int) -> dict:
        """Build a writable RouterOS NAT payload from a printed rule for rollback."""
        allowed = (
            "chain", "protocol", "src-address", "dst-address", "src-port", "dst-port",
            "in-interface", "in-interface-list", "out-interface", "out-interface-list",
            "src-address-list", "dst-address-list", "action", "to-addresses", "to-ports",
            "comment", "disabled", "log", "log-prefix",
        )
        payload = {key: str(rule.get(key)) for key in allowed if rule.get(key) not in (None, "")}
        payload.setdefault("chain", "dstnat")
        payload.setdefault("protocol", "tcp")
        payload.setdefault("dst-port", str(int(port)))
        payload.setdefault("action", "dst-nat")
        payload.setdefault("to-addresses", remote_address)
        payload.setdefault("to-ports", str(int(port)))
        payload.setdefault("comment", login)
        payload.setdefault("disabled", "no")
        return payload

    def _rollback_create(self, server: str, creds: SessionCredentials, profile_id: str, secret_id: str, nat_ids: List[str]) -> None:
        try:
            with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
                for rid in nat_ids:
                    if rid:
                        api.remove("/ip/firewall/nat", rid)
                if secret_id:
                    api.remove("/ppp/secret", secret_id)
                if profile_id:
                    api.remove("/ppp/profile", profile_id)
        except Exception:
            pass

    def _find_next_ip(self, used_ips: set[str]) -> str:
        ip = DEFAULT_START_IP
        for _ in range(5000):
            if ip not in used_ips:
                return ip
            ip = self._next_ip(ip)
        raise ValueError("Не удалось подобрать свободный IP")

    def _next_ip(self, current_ip: str) -> str:
        parts = current_ip.split(".")
        third = int(parts[2])
        fourth = int(parts[3]) + 1
        if fourth > 254:
            fourth = 1
            third += 1
        return f"172.16.{third}.{fourth}"

    def _find_free_ports(self, used_ports: set[int], count: int) -> List[int]:
        free = []
        for port in range(PORT_MIN, PORT_MAX + 1):
            if port in used_ports:
                continue
            free.append(port)
            if len(free) >= count:
                break
        return free

    def _generate_password(self, length: int) -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
        return "".join(secrets.choice(alphabet) for _ in range(length))

    def _normalize_ip(self, value: object) -> str:
        raw = str(value or "").strip()
        if "/" in raw:
            raw = raw.split("/", 1)[0].strip()
        return raw

    def _parse_ports(self, raw_value: object) -> List[int]:
        raw = str(raw_value or "").strip()
        if not raw:
            return []
        result = []
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "-" in chunk:
                left, right = chunk.split("-", 1)
                start = self._parse_int(left)
                end = self._parse_int(right)
                if start and end and end >= start:
                    result.extend(range(start, end + 1))
                continue
            port = self._parse_int(chunk)
            if port:
                result.append(port)
        return result

    def _parse_optional_int(self, value: object) -> int | None:
        if value is None or str(value).strip() == "":
            return None
        try:
            return int(float(str(value).strip()))
        except Exception:
            return None

    def _parse_int(self, value: object) -> int:
        try:
            return int(str(value).strip())
        except Exception:
            return 0

    def _file_exists(self, path: str) -> bool:
        try:
            with open(path, "rb"):
                return True
        except OSError:
            return False
