from __future__ import annotations

import ipaddress
import secrets
import string
import threading
from typing import Any

from .monitor import load_targets
from .routeros import RouterOSClient


PORT_MIN = 10001
PORT_MAX = 13000
DEFAULT_LOCAL_ADDRESS = "172.31.255.254"
DEFAULT_START_IP = ipaddress.IPv4Address("172.16.1.176")
VPN_L2TP_SOFT_LIMIT = 450


class VPNSyncOperations:
    """Server-owned RouterOS mutations followed by immediate DB reconciliation."""

    def __init__(self, db, settings, monitor) -> None:
        self.db = db
        self.settings = settings
        self.monitor = monitor
        self.targets = {target.host: target for target in load_targets(settings)}
        self._write_locks: dict[str, threading.Lock] = {}
        self._write_locks_guard = threading.Lock()

    def _target(self, host: str):
        key = str(host or "").strip().lower()
        target = self.targets.get(key)
        if target is None:
            raise ValueError(f"VPN-сервер {host!r} не настроен в VPNSync")
        return target

    def _write_lock(self, host: str) -> threading.Lock:
        with self._write_locks_guard:
            return self._write_locks.setdefault(host, threading.Lock())

    @staticmethod
    def _add(api: RouterOSClient, path: str, params: dict[str, Any]) -> str:
        replies = api.talk(f"{path}/add", params)
        for row in replies:
            value = str(row.get("ret") or row.get(".id") or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _parse_ports(value: Any) -> set[int]:
        result: set[int] = set()
        text = str(value or "").strip()
        if not text:
            return result
        for token in text.split(","):
            part = token.strip()
            if not part:
                continue
            if "-" in part:
                left, right = part.split("-", 1)
                if left.strip().isdigit() and right.strip().isdigit():
                    start, end = int(left), int(right)
                    if start > end:
                        start, end = end, start
                    result.update(port for port in range(max(1, start), min(65535, end) + 1))
            elif part.isdigit():
                port = int(part)
                if 1 <= port <= 65535:
                    result.add(port)
        return result

    @classmethod
    def _used_ports(cls, nat_rows: list[dict[str, str]]) -> set[int]:
        used: set[int] = set()
        for row in nat_rows:
            used.update(cls._parse_ports(row.get("dst-port")))
        return used

    @staticmethod
    def _free_ports(used: set[int], count: int) -> list[int]:
        result: list[int] = []
        for port in range(PORT_MIN, PORT_MAX + 1):
            if port not in used:
                result.append(port)
                if len(result) >= count:
                    break
        return result

    @staticmethod
    def _password(length: int = 8) -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
        return "".join(secrets.choice(alphabet) for _ in range(max(8, int(length))))

    @staticmethod
    def _used_remote_addresses(secrets_rows: list[dict[str, str]], profile_rows: list[dict[str, str]]) -> set[str]:
        result: set[str] = set()
        for row in [*secrets_rows, *profile_rows]:
            raw = str(row.get("remote-address") or "").strip()
            try:
                result.add(str(ipaddress.IPv4Address(raw)))
            except Exception:
                continue
        return result

    @staticmethod
    def _next_ip(used: set[str]) -> str:
        value = DEFAULT_START_IP
        last = ipaddress.IPv4Address("172.31.254.254")
        while value <= last:
            text = str(value)
            if text not in used and int(str(value).split(".")[-1]) not in {0, 255}:
                return text
            value += 1
        raise RuntimeError("Не осталось свободных адресов в VPN-пуле")

    @staticmethod
    def _exact(rows: list[dict[str, str]], field: str, value: str) -> list[dict[str, str]]:
        wanted = str(value or "").strip()
        return [row for row in rows if str(row.get(field) or "").strip() == wanted]

    def _sync(self, target, action: str) -> dict[str, int]:
        return self.monitor.syncer.sync(
            target,
            event_path="api",
            event_payload={"action": action},
        )

    def _client_state(self, host: str, login: str) -> dict[str, Any] | None:
        with self.db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, s.hostname AS server, c.login, c.remote_address,
                       c.profile, c.service, c.disabled, c.lifecycle_state,
                       CASE WHEN c.password_enc IS NULL THEN ''
                            ELSE pgp_sym_decrypt(c.password_enc, %s) END AS password,
                       COALESCE((
                           SELECT jsonb_agg(jsonb_build_object(
                               'external_port', p.external_port,
                               'internal_port', p.internal_port,
                               'protocol', p.protocol,
                               'disabled', p.disabled,
                               'routeros_rule_id', p.routeros_rule_id
                           ) ORDER BY p.external_port)
                             FROM vpn_nat_ports p
                            WHERE p.client_id=c.id AND p.deleted=FALSE
                       ), '[]'::jsonb) AS ports
                  FROM vpn_clients c
                  JOIN vpn_servers s ON s.id=c.server_id
                 WHERE lower(s.hostname)=lower(%s) AND c.login=%s
                 LIMIT 1
                """,
                (self.db.encryption_key, str(host), str(login)),
            )
            row = cur.fetchone()
            if not row:
                return None
            data = dict(row)
            ports = list(data.pop("ports") or [])
            data["remote_address"] = str(data.get("remote_address") or "")
            data["ports"] = [int(item.get("external_port") or 0) for item in ports if int(item.get("external_port") or 0) > 0]
            data["active_ports"] = [
                int(item.get("external_port") or 0)
                for item in ports
                if int(item.get("external_port") or 0) > 0 and not bool(item.get("disabled"))
            ]
            data["disabled_ports"] = [
                int(item.get("external_port") or 0)
                for item in ports
                if int(item.get("external_port") or 0) > 0 and bool(item.get("disabled"))
            ]
            data["is_enabled"] = not bool(data.pop("disabled", False))
            return data

    def create_clients(self, server: str, base_login: str, ports_per_client: int, accounts_count: int) -> list[dict[str, Any]]:
        target = self._target(server)
        base_login = str(base_login or "").strip()
        ports_per_client = int(ports_per_client)
        accounts_count = int(accounts_count)
        if not base_login:
            raise ValueError("Логин не может быть пустым")
        if not 1 <= ports_per_client <= 14:
            raise ValueError("Количество портов должно быть от 1 до 14")
        if not 1 <= accounts_count <= 20:
            raise ValueError("Количество учёток должно быть от 1 до 20")

        created: list[dict[str, Any]] = []
        ledger: list[dict[str, Any]] = []
        with self._write_lock(target.host), self.monitor.syncer._lock(target.host):
            with RouterOSClient(target.host, target.username, target.password, port=target.port, timeout=target.timeout) as api:
                secret_rows = api.print("/ppp/secret")
                active_rows = api.print("/ppp/active")
                profile_rows = api.print("/ppp/profile")
                nat_rows = api.print("/ip/firewall/nat")
                active_l2tp = sum(1 for row in active_rows if str(row.get("service") or "").strip().lower() in {"", "l2tp"})
                if active_l2tp >= VPN_L2TP_SOFT_LIMIT:
                    raise RuntimeError(
                        f"На {target.host} уже {active_l2tp} активных L2TP; создание запрещено выше безопасного порога {VPN_L2TP_SOFT_LIMIT}"
                    )
                existing = {str(row.get("name") or "").strip() for row in secret_rows}
                used_ips = self._used_remote_addresses(secret_rows, profile_rows)
                used_ports = self._used_ports(nat_rows)

                planned: list[tuple[str, str, list[int], str]] = []
                for index in range(accounts_count):
                    desired = base_login if index == 0 else f"{base_login}_{index}"
                    login = desired
                    suffix = 1
                    while login in existing:
                        login = f"{base_login}_{suffix}"
                        suffix += 1
                    existing.add(login)
                    remote = self._next_ip(used_ips)
                    used_ips.add(remote)
                    ports = self._free_ports(used_ports, ports_per_client)
                    if len(ports) != ports_per_client:
                        raise RuntimeError("Недостаточно свободных NAT-портов")
                    used_ports.update(ports)
                    planned.append((login, remote, ports, self._password()))

                try:
                    for login, remote, ports, password in planned:
                        item = {"profile_id": "", "secret_id": "", "nat_ids": []}
                        ledger.append(item)
                        item["profile_id"] = self._add(api, "/ppp/profile", {
                            "name": login,
                            "local-address": DEFAULT_LOCAL_ADDRESS,
                            "remote-address": remote,
                            "use-upnp": "no",
                            "change-tcp-mss": "no",
                            "use-ipv6": "no",
                            "use-mpls": "no",
                            "use-compression": "no",
                            "use-encryption": "default",
                            "only-one": "no",
                        })
                        item["secret_id"] = self._add(api, "/ppp/secret", {
                            "name": login,
                            "password": password,
                            "profile": login,
                            "service": "l2tp",
                            "comment": login,
                            "disabled": "no",
                        })
                        for port in ports:
                            rule_id = self._add(api, "/ip/firewall/nat", {
                                "chain": "dstnat",
                                "protocol": "tcp",
                                "dst-port": str(port),
                                "action": "dst-nat",
                                "to-addresses": remote,
                                "to-ports": str(port),
                                "comment": login,
                                "disabled": "no",
                            })
                            item["nat_ids"].append(rule_id)
                        created.append({
                            "server": target.host,
                            "login": login,
                            "password": password,
                            "remote_address": remote,
                            "ports": ports,
                            "profile_id": item["profile_id"],
                            "secret_id": item["secret_id"],
                            "nat_rule_ids": list(item["nat_ids"]),
                            "is_enabled": True,
                        })
                except Exception:
                    for item in reversed(ledger):
                        for rule_id in reversed(list(item.get("nat_ids") or [])):
                            if rule_id:
                                try: api.remove("/ip/firewall/nat", rule_id)
                                except Exception: pass
                        if item.get("secret_id"):
                            try: api.remove("/ppp/secret", str(item["secret_id"]))
                            except Exception: pass
                        if item.get("profile_id"):
                            try: api.remove("/ppp/profile", str(item["profile_id"]))
                            except Exception: pass
                    raise

        self._sync(target, "client.create")
        return created

    def add_ports(self, server: str, login: str, count: int) -> dict[str, Any]:
        target = self._target(server)
        count = int(count)
        if not 1 <= count <= 14:
            raise ValueError("Количество портов должно быть от 1 до 14")
        current = self._client_state(target.host, login)
        if not current:
            raise ValueError("Клиент не найден в PostgreSQL")
        remote = str(current.get("remote_address") or "")
        if not remote:
            raise ValueError("У клиента не определён Remote Address")
        added: list[str] = []
        with self._write_lock(target.host), self.monitor.syncer._lock(target.host):
            with RouterOSClient(target.host, target.username, target.password, port=target.port, timeout=target.timeout) as api:
                nat_rows = api.print("/ip/firewall/nat")
                ports = self._free_ports(self._used_ports(nat_rows), count)
                if len(ports) != count:
                    raise RuntimeError("Недостаточно свободных NAT-портов")
                try:
                    for port in ports:
                        added.append(self._add(api, "/ip/firewall/nat", {
                            "chain": "dstnat", "protocol": "tcp", "dst-port": str(port),
                            "action": "dst-nat", "to-addresses": remote, "to-ports": str(port),
                            "comment": str(login), "disabled": "no",
                        }))
                except Exception:
                    for rule_id in reversed(added):
                        if rule_id:
                            try: api.remove("/ip/firewall/nat", rule_id)
                            except Exception: pass
                    raise
        self._sync(target, "nat.add_ports")
        return self._client_state(target.host, login) or current

    def _port_rule_ids(self, host: str, login: str, port: int) -> list[str]:
        with self.db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.routeros_rule_id
                  FROM vpn_nat_ports p
                  JOIN vpn_clients c ON c.id=p.client_id
                  JOIN vpn_servers s ON s.id=p.server_id
                 WHERE lower(s.hostname)=lower(%s) AND c.login=%s
                   AND p.external_port=%s AND p.deleted=FALSE
                   AND p.routeros_rule_id<>''
                """,
                (host, login, int(port)),
            )
            return [str(row["routeros_rule_id"]) for row in cur.fetchall()]

    def remove_port(self, server: str, login: str, port: int) -> dict[str, Any]:
        target = self._target(server)
        rule_ids = self._port_rule_ids(target.host, login, int(port))
        if not rule_ids:
            raise ValueError("NAT правило для порта не найдено")
        with self._write_lock(target.host), self.monitor.syncer._lock(target.host):
            with RouterOSClient(target.host, target.username, target.password, port=target.port, timeout=target.timeout) as api:
                live = {str(row.get(".id") or "") for row in api.print("/ip/firewall/nat")}
                for rule_id in rule_ids:
                    if rule_id in live:
                        api.remove("/ip/firewall/nat", rule_id)
        self._sync(target, "nat.remove_port")
        state = self._client_state(target.host, login)
        if not state:
            raise ValueError("Клиент не найден после операции")
        return state

    def set_password(self, server: str, login: str, password: str) -> dict[str, Any]:
        target = self._target(server)
        password = str(password or "").strip()
        if not password:
            raise ValueError("Новый пароль не может быть пустым")
        with self._write_lock(target.host), self.monitor.syncer._lock(target.host):
            with RouterOSClient(target.host, target.username, target.password, port=target.port, timeout=target.timeout) as api:
                rows = self._exact(api.print("/ppp/secret"), "name", login)
                if not rows:
                    raise ValueError("PPP Secret не найден")
                for row in rows:
                    item_id = str(row.get(".id") or "")
                    if item_id:
                        api.set("/ppp/secret", item_id, {"password": password})
        self._sync(target, "client.password_change")
        state = self._client_state(target.host, login)
        if not state:
            raise ValueError("Клиент не найден после операции")
        return state

    def set_secret_enabled(self, server: str, login: str, enabled: bool) -> dict[str, Any]:
        target = self._target(server)
        with self._write_lock(target.host), self.monitor.syncer._lock(target.host):
            with RouterOSClient(target.host, target.username, target.password, port=target.port, timeout=target.timeout) as api:
                rows = self._exact(api.print("/ppp/secret"), "name", login)
                if not rows:
                    raise ValueError("PPP Secret не найден")
                for row in rows:
                    item_id = str(row.get(".id") or "")
                    if not item_id:
                        continue
                    if enabled:
                        api.enable("/ppp/secret", item_id)
                    else:
                        api.disable("/ppp/secret", item_id)
        self._sync(target, "client.enabled_change")
        state = self._client_state(target.host, login)
        if not state:
            raise ValueError("Клиент не найден после операции")
        return state

    def set_port_enabled(self, server: str, login: str, port: int, enabled: bool) -> dict[str, Any]:
        target = self._target(server)
        rule_ids = self._port_rule_ids(target.host, login, int(port))
        if not rule_ids:
            raise ValueError("Порт не найден у клиента")
        with self._write_lock(target.host), self.monitor.syncer._lock(target.host):
            with RouterOSClient(target.host, target.username, target.password, port=target.port, timeout=target.timeout) as api:
                live = {str(row.get(".id") or "") for row in api.print("/ip/firewall/nat")}
                for rule_id in rule_ids:
                    if rule_id in live:
                        if enabled: api.enable("/ip/firewall/nat", rule_id)
                        else: api.disable("/ip/firewall/nat", rule_id)
        self._sync(target, "nat.enabled_change")
        state = self._client_state(target.host, login)
        if not state:
            raise ValueError("Клиент не найден после операции")
        return state

    def recreate_port(self, server: str, login: str, port: int) -> dict[str, Any]:
        target = self._target(server)
        current = self._client_state(target.host, login)
        if not current:
            raise ValueError("Клиент не найден")
        remote = str(current.get("remote_address") or "")
        rule_ids = self._port_rule_ids(target.host, login, int(port))
        with self._write_lock(target.host), self.monitor.syncer._lock(target.host):
            with RouterOSClient(target.host, target.username, target.password, port=target.port, timeout=target.timeout) as api:
                nat_rows = api.print("/ip/firewall/nat")
                for row in nat_rows:
                    if int(port) in self._parse_ports(row.get("dst-port")) and str(row.get(".id") or "") not in set(rule_ids):
                        raise ValueError(f"Порт {port} занят другим NAT-правилом")
                backups = [row for row in nat_rows if str(row.get(".id") or "") in set(rule_ids)]
                removed: list[dict[str, str]] = []
                try:
                    for row in backups:
                        rid = str(row.get(".id") or "")
                        if rid:
                            api.remove("/ip/firewall/nat", rid)
                            removed.append(row)
                    self._add(api, "/ip/firewall/nat", {
                        "chain": "dstnat", "protocol": "tcp", "dst-port": str(int(port)),
                        "action": "dst-nat", "to-addresses": remote, "to-ports": str(int(port)),
                        "comment": str(login), "disabled": "no",
                    })
                except Exception:
                    for row in removed:
                        payload = {
                            key: row[key]
                            for key in ("chain", "protocol", "dst-port", "action", "to-addresses", "to-ports", "comment", "disabled")
                            if row.get(key) not in (None, "")
                        }
                        try: self._add(api, "/ip/firewall/nat", payload)
                        except Exception: pass
                    raise
        self._sync(target, "nat.recreate")
        return self._client_state(target.host, login) or current

    def delete_client(self, server: str, login: str, *, actor: str) -> None:
        target = self._target(server)
        # Fresh snapshot first: password/recovery/NAT IDs must be in PostgreSQL
        # before any destructive RouterOS command.
        self._sync(target, "client.delete.preflight")
        with self.db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.server_id, c.recovery_snapshot_enc IS NOT NULL AS snapshot_saved,
                       c.profile
                  FROM vpn_clients c JOIN vpn_servers s ON s.id=c.server_id
                 WHERE lower(s.hostname)=lower(%s) AND c.login=%s
                """,
                (target.host, login),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Клиент не найден")
            if not bool(row.get("snapshot_saved")):
                raise RuntimeError("Удаление заблокировано: нет зашифрованного recovery snapshot")
            client_id = int(row["id"])
            server_id = int(row["server_id"])
            profile_name = str(row.get("profile") or "")
        rule_ids = self._port_rule_ids(target.host, login, 0) if False else []
        with self.db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT routeros_rule_id FROM vpn_nat_ports WHERE client_id=%s AND deleted=FALSE AND routeros_rule_id<>''",
                (client_id,),
            )
            rule_ids = [str(item["routeros_rule_id"]) for item in cur.fetchall()]

        with self._write_lock(target.host), self.monitor.syncer._lock(target.host):
            with RouterOSClient(target.host, target.username, target.password, port=target.port, timeout=target.timeout) as api:
                nat_rows = api.print("/ip/firewall/nat")
                live_nat = {str(item.get(".id") or "") for item in nat_rows}
                for rule_id in rule_ids:
                    if rule_id in live_nat:
                        api.remove("/ip/firewall/nat", rule_id)
                secrets_rows = api.print("/ppp/secret")
                secrets = self._exact(secrets_rows, "name", login)
                secret_ids = {str(item.get(".id") or "") for item in secrets if str(item.get(".id") or "")}
                for secret_id in secret_ids:
                    api.remove("/ppp/secret", secret_id)
                if profile_name and profile_name not in {"default", "default-encryption"}:
                    remaining = api.print("/ppp/secret")
                    if not any(str(item.get("profile") or "").strip() == profile_name for item in remaining):
                        for profile in self._exact(api.print("/ppp/profile"), "name", profile_name):
                            profile_id = str(profile.get(".id") or "")
                            if profile_id:
                                api.remove("/ppp/profile", profile_id)

            archived = self.db.archive_client(
                server_id,
                login,
                deleted_reason="Удалено сотрудником через LinkVideo.Helper",
                deleted_by=str(actor or "LinkVideo.Helper"),
                source="desktop",
            )
            if not archived:
                raise RuntimeError("RouterOS удалён, но активная запись PostgreSQL не была перемещена в архив")
            self.db.append_change(
                server_id=server_id,
                client_id=None,
                login=login,
                event_type="deleted",
                summary="VPN-клиент удалён сотрудником",
                source="desktop",
                actor=str(actor or "LinkVideo.Helper"),
            )

    def disconnect_client(self, server: str, login: str) -> bool:
        target = self._target(server)
        changed = False
        with self._write_lock(target.host), self.monitor.syncer._lock(target.host):
            with RouterOSClient(target.host, target.username, target.password, port=target.port, timeout=target.timeout) as api:
                rows = self._exact(api.print("/ppp/active"), "name", login)
                for row in rows:
                    item_id = str(row.get(".id") or "")
                    if item_id:
                        api.remove("/ppp/active", item_id)
                        changed = True
        if changed:
            self._sync(target, "client.disconnect")
        return changed
