from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIClient
from linkvideo_vpn_helper.services.app_logging import event
from linkvideo_vpn_helper.services.vpn_retention_policy import (
    compose_extended_comment,
    parse_extended_comment,
)
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials, VPNService
from linkvideo_vpn_helper.services.vpn_sheets_sync import GoogleSheetsBackend


@dataclass(slots=True)
class DeletedVPNClient:
    server: str
    login: str
    password_saved: bool
    remote_address: str = ""
    profile: str = ""
    deleted_at: str = ""
    ports: str = ""
    row: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RestoreResult:
    server: str
    login: str
    remote_address: str
    ports: list[int]
    profile_created: bool
    nat_created: int
    port_replacements: dict[int, int] = field(default_factory=dict)


class VPNRestoreService:
    """Restore a deleted LinkVideo VPN client from the Sheets recovery mirror.

    Restoration is intentionally fail-closed for login/IP/profile conflicts and
    missing recovery data. Old external ports are reused only while they are free;
    if another client has occupied one after deletion, Helper allocates a new free
    external port and keeps the original internal to-ports target. If a later
    create step fails, every object created by this attempt is rolled back.
    """

    PROFILE_WRITABLE = (
        "name",
        "local-address",
        "remote-address",
        "bridge",
        "use-compression",
        "use-encryption",
        "use-ipv6",
        "use-mpls",
        "use-upnp",
        "only-one",
        "change-tcp-mss",
        "dns-server",
        "wins-server",
        "rate-limit",
        "session-timeout",
        "idle-timeout",
        "address-list",
        "interface-list",
        "parent-queue",
        "queue-type",
        "on-up",
        "on-down",
    )
    SECRET_WRITABLE = (
        "name",
        "password",
        "service",
        "profile",
        "local-address",
        "remote-address",
        "routes",
        "caller-id",
        "limit-bytes-in",
        "limit-bytes-out",
        "comment",
        "disabled",
    )
    NAT_WRITABLE = (
        "chain",
        "protocol",
        "src-address",
        "dst-address",
        "src-port",
        "dst-port",
        "in-interface",
        "in-interface-list",
        "out-interface",
        "out-interface-list",
        "src-address-list",
        "dst-address-list",
        "action",
        "to-addresses",
        "to-ports",
        "comment",
        "disabled",
        "log",
        "log-prefix",
    )

    def __init__(self, vpn_service: VPNService, backend: GoogleSheetsBackend):
        self.vpn_service = vpn_service
        self.backend = backend

    @staticmethod
    def _yes(value: Any) -> bool:
        return str(value or "").strip().lower() in {"yes", "true", "1", "on", "да"}

    @staticmethod
    def _clean_payload(source: dict[str, Any], allowed: tuple[str, ...]) -> dict[str, str]:
        result: dict[str, str] = {}
        for key in allowed:
            value = source.get(key)
            if value not in (None, ""):
                result[key] = str(value)
        return result

    @staticmethod
    def _snapshot(row: dict[str, str]) -> dict[str, Any]:
        raw = str(row.get("RouterOS snapshot", "") or "").strip()
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _record_from_row(self, row: dict[str, str], fallback_server: str = "") -> DeletedVPNClient | None:
        login = str(row.get("Логин", "") or "").strip()
        if not login:
            return None
        server = str(row.get("VPN-сервер", "") or fallback_server or "").strip()
        snapshot = self._snapshot(row)
        secret = snapshot.get("secret") if isinstance(snapshot.get("secret"), dict) else {}
        password = str(row.get("Пароль", "") or secret.get("password", "") or "")
        return DeletedVPNClient(
            server=server,
            login=login,
            password_saved=bool(password),
            remote_address=str(row.get("Remote Address", "") or ""),
            profile=str(row.get("Profile", "") or ""),
            deleted_at=str(row.get("Удалена в", "") or ""),
            ports=str(row.get("NAT / Порты", "") or ""),
            row=dict(row),
        )

    def list_deleted(self, servers: list[str]) -> list[DeletedVPNClient]:
        wanted = {str(server or "").strip().lower() for server in servers if str(server or "").strip()}
        result: list[DeletedVPNClient] = []
        for row in self.backend.read_deleted_rows(""):
            record = self._record_from_row(row)
            if record is None:
                continue
            if wanted and record.server.lower() not in wanted:
                continue
            result.append(record)
        result.sort(key=lambda item: (item.server.lower(), item.login.lower()))
        return result

    def search_deleted(self, query: str, servers: list[str] | None = None) -> list[DeletedVPNClient]:
        wanted = str(query or "").strip().lower()
        if not wanted:
            return []
        allowed = {
            str(server or "").strip().lower()
            for server in list(servers or [])
            if str(server or "").strip()
        }
        result: list[DeletedVPNClient] = []
        for row in self.backend.search_deleted_rows(wanted):
            record = self._record_from_row(row)
            if record is None:
                continue
            if allowed and record.server.lower() not in allowed:
                continue
            result.append(record)
        result.sort(key=lambda item: (item.server.lower(), item.login.lower()))
        return result

    def _fallback_nat(self, row: dict[str, str], login: str, remote: str) -> list[dict[str, str]]:
        text = str(row.get("NAT / Порты", "") or "")
        rules: list[dict[str, str]] = []
        pattern = re.compile(r"(?P<proto>[a-z0-9]+)\s+(?P<ext>\d+)\s*→\s*(?P<to>\d+)(?P<off>\s*\[off\])?", re.I)
        for match in pattern.finditer(text):
            rules.append(
                {
                    "chain": "dstnat",
                    "protocol": match.group("proto").lower(),
                    "dst-port": match.group("ext"),
                    "action": "dst-nat",
                    "to-addresses": remote,
                    "to-ports": match.group("to"),
                    "comment": login,
                    "disabled": "yes" if match.group("off") else "no",
                }
            )
        return rules

    def _nat_payloads(
        self,
        row: dict[str, str],
        snapshot: dict[str, Any],
        login: str,
        remote: str,
    ) -> list[dict[str, str]]:
        source_rules = snapshot.get("nat_rules")
        if not isinstance(source_rules, list) or not source_rules:
            return self._fallback_nat(row, login, remote)

        result: list[dict[str, str]] = []
        for source in source_rules:
            if not isinstance(source, dict):
                continue
            payload = self._clean_payload(source, self.NAT_WRITABLE)
            payload.setdefault("chain", "dstnat")
            payload.setdefault("protocol", "tcp")
            payload.setdefault("action", "dst-nat")
            if remote:
                payload["to-addresses"] = remote
            payload["comment"] = login
            payload.setdefault("disabled", "no")
            if payload.get("dst-port"):
                payload.setdefault("to-ports", payload["dst-port"])
                result.append(payload)
        return result

    def _intended_ports(self, nat_payloads: list[dict[str, str]]) -> set[int]:
        ports: set[int] = set()
        for rule in nat_payloads:
            ports.update(self.vpn_service._parse_ports(rule.get("dst-port", "")))
        return ports

    def _remap_occupied_ports(
        self,
        nat_payloads: list[dict[str, str]],
        occupied_ports: set[int],
    ) -> tuple[list[tuple[int | None, dict[str, str]]], dict[int, int]]:
        """Plan NAT restoration without ever taking a port from another client.

        LinkVideo-created NAT rules contain one external port per rule. Free old
        ports are preserved. Occupied old ports are replaced with the next free
        port from the normal LinkVideo pool, while to-ports is intentionally left
        untouched so the service on the client keeps listening on its old internal
        port.
        """
        reserved = set(int(port) for port in occupied_ports)
        replacements: dict[int, int] = {}
        planned: list[tuple[int | None, dict[str, str]]] = []

        for source in nat_payloads:
            payload = dict(source)
            ports = sorted(set(self.vpn_service._parse_ports(payload.get("dst-port", ""))))
            if not ports:
                continue
            if len(ports) != 1:
                conflicts = sorted(set(ports) & reserved)
                if conflicts:
                    raise ValueError(
                        "Нельзя автоматически восстановить нестандартное NAT-правило: "
                        "часть внешних портов уже занята: "
                        + ", ".join(str(port) for port in conflicts)
                    )
                reserved.update(ports)
                planned.append((None, payload))
                continue

            original_port = ports[0]
            selected_port = original_port
            if selected_port in reserved:
                free = self.vpn_service._find_free_ports(reserved, 1)
                if not free:
                    raise ValueError("Недостаточно свободных внешних портов для восстановления")
                selected_port = int(free[0])
                payload["dst-port"] = str(selected_port)
                replacements[original_port] = selected_port

            reserved.add(selected_port)
            planned.append((original_port, payload))

        return planned, replacements

    def restore(self, server: str, creds: SessionCredentials, login: str) -> RestoreResult:
        row = self.backend.find_deleted_row(server, login)
        if row is None:
            raise ValueError("Удалённая запись не найдена в Google Sheets")

        snapshot = self._snapshot(row)
        secret_source = snapshot.get("secret") if isinstance(snapshot.get("secret"), dict) else {}
        profile_source = snapshot.get("profile") if isinstance(snapshot.get("profile"), dict) else {}

        login = str(row.get("Логин", "") or login).strip()
        password = str(row.get("Пароль", "") or secret_source.get("password", "") or "")
        if not password:
            raise ValueError(
                "В резервной записи нет пароля. Автоматическое восстановление заблокировано, "
                "чтобы не создать клиенту другой пароль."
            )

        profile_name = str(row.get("Profile", "") or secret_source.get("profile", "") or login).strip()
        remote = self.vpn_service._normalize_ip(
            row.get("Remote Address", "")
            or secret_source.get("remote-address", "")
            or profile_source.get("remote-address", "")
        )
        local = self.vpn_service._normalize_ip(
            row.get("Local Address", "") or profile_source.get("local-address", "")
        )
        service = str(row.get("Service", "") or secret_source.get("service", "") or "l2tp").strip()

        nat_payloads = self._nat_payloads(row, snapshot, login, remote)

        created_profile_id = ""
        created_secret_id = ""
        created_nat_ids: list[str] = []
        profile_created = False

        with RouterOSAPIClient(
            server,
            creds.username,
            creds.password,
            port=creds.port,
            timeout=creds.timeout,
        ) as api:
            secrets = api.print("/ppp/secret", {".proplist": ".id,name,profile,remote-address"})
            if any(str(item.get("name", "") or "").strip() == login for item in secrets):
                raise ValueError(f"PPP Secret {login} уже существует на {server}")

            profiles = api.print("/ppp/profile", {".proplist": ".id,name,local-address,remote-address"})
            existing_profile = next(
                (item for item in profiles if str(item.get("name", "") or "").strip() == profile_name),
                None,
            )

            # Do not recreate into an address currently owned by another account/profile.
            if remote:
                for item in secrets:
                    item_remote = self.vpn_service._normalize_ip(item.get("remote-address", ""))
                    if item_remote and item_remote == remote:
                        raise ValueError(f"Remote Address {remote} уже используется другим PPP Secret")
                for item in profiles:
                    if existing_profile is not None and item is existing_profile:
                        continue
                    item_remote = self.vpn_service._normalize_ip(item.get("remote-address", ""))
                    if item_remote and item_remote == remote:
                        raise ValueError(f"Remote Address {remote} уже используется профилем {item.get('name', '')}")

            if existing_profile is not None:
                expected_remote = remote
                actual_remote = self.vpn_service._normalize_ip(existing_profile.get("remote-address", ""))
                expected_local = local
                actual_local = self.vpn_service._normalize_ip(existing_profile.get("local-address", ""))
                if expected_remote and actual_remote and expected_remote != actual_remote:
                    raise ValueError(f"Профиль {profile_name} уже существует с другим Remote Address")
                if expected_local and actual_local and expected_local != actual_local:
                    raise ValueError(f"Профиль {profile_name} уже существует с другим Local Address")

            nat_existing = api.print(
                "/ip/firewall/nat",
                {".proplist": ".id,dst-port,to-addresses,comment"},
            )
            occupied_ports: set[int] = set()
            for rule in nat_existing:
                occupied_ports.update(self.vpn_service._parse_ports(rule.get("dst-port", "")))
            planned_nat, port_replacements = self._remap_occupied_ports(
                nat_payloads,
                occupied_ports,
            )
            restored_ports: list[int] = []

            try:
                if profile_name and profile_name not in {"default", "default-encryption"} and existing_profile is None:
                    profile_payload = self._clean_payload(profile_source, self.PROFILE_WRITABLE)
                    profile_payload["name"] = profile_name
                    if local:
                        profile_payload["local-address"] = local
                    if remote:
                        profile_payload["remote-address"] = remote
                    profile_payload.setdefault("use-upnp", "no")
                    profile_payload.setdefault("change-tcp-mss", "no")
                    profile_payload.setdefault("use-ipv6", "no")
                    profile_payload.setdefault("use-mpls", "no")
                    profile_payload.setdefault("use-compression", "no")
                    profile_payload.setdefault("use-encryption", "default")
                    profile_payload.setdefault("only-one", "no")
                    created_profile_id = api.add("/ppp/profile", profile_payload)
                    profile_created = True

                now_ns = time.time_ns()
                old_comment = str(
                    secret_source.get("comment", "")
                    or row.get("Комментарий RouterOS", "")
                    or login
                )
                base_comment = parse_extended_comment(old_comment).base_comment or login
                secret_payload = self._clean_payload(secret_source, self.SECRET_WRITABLE)
                secret_payload.update(
                    {
                        "name": login,
                        "password": password,
                        "service": service or "l2tp",
                        "profile": profile_name or "default-encryption",
                        "comment": compose_extended_comment(
                            base_comment,
                            "A",
                            now_ns,
                            now_ns,
                            "manual_enabled",
                        ),
                        "disabled": "no",
                    }
                )
                # Most LinkVideo accounts store the address in the per-client profile.
                # Preserve a direct secret address only if it existed in the snapshot.
                if secret_source.get("remote-address") and remote:
                    secret_payload["remote-address"] = remote
                if secret_source.get("local-address") and local:
                    secret_payload["local-address"] = local

                created_secret_id = api.add("/ppp/secret", secret_payload)

                for original_port, source_payload in planned_nat:
                    payload = dict(source_payload)
                    selected = self.vpn_service._parse_ports(payload.get("dst-port", ""))
                    if len(selected) == 1:
                        selected_port = int(selected[0])
                        # Protect against a race: another operator may create a NAT
                        # rule after the initial snapshot but before our add.
                        rows = self.vpn_service._api_print_exact(
                            api,
                            "/ip/firewall/nat",
                            "dst-port",
                            str(selected_port),
                            ".id,dst-port",
                        )
                        if any(
                            selected_port in self.vpn_service._parse_ports(
                                self.vpn_service._get_rule_external_port(item)
                            )
                            for item in rows
                        ):
                            current_rules = api.print(
                                "/ip/firewall/nat",
                                {".proplist": ".id,dst-port"},
                            )
                            current_used: set[int] = set()
                            for item in current_rules:
                                current_used.update(
                                    self.vpn_service._parse_ports(
                                        self.vpn_service._get_rule_external_port(item)
                                    )
                                )
                            free = self.vpn_service._find_free_ports(current_used, 1)
                            if not free:
                                raise ValueError(
                                    "Недостаточно свободных внешних портов для восстановления"
                                )
                            selected_port = int(free[0])
                            payload["dst-port"] = str(selected_port)
                            if original_port is not None:
                                port_replacements[int(original_port)] = selected_port

                        restored_ports.append(selected_port)
                    else:
                        restored_ports.extend(int(port) for port in selected)

                    created_nat_ids.append(api.add("/ip/firewall/nat", payload))

            except Exception as operation_error:
                rollback_errors: list[str] = []
                for item_id in reversed(created_nat_ids):
                    if not item_id:
                        continue
                    try:
                        api.remove("/ip/firewall/nat", item_id)
                    except Exception as exc:
                        rollback_errors.append(f"NAT {item_id}: {exc}")
                if created_secret_id:
                    try:
                        api.remove("/ppp/secret", created_secret_id)
                    except Exception as exc:
                        rollback_errors.append(f"Secret {created_secret_id}: {exc}")
                if profile_created and created_profile_id:
                    try:
                        api.remove("/ppp/profile", created_profile_id)
                    except Exception as exc:
                        rollback_errors.append(f"Profile {created_profile_id}: {exc}")
                if rollback_errors:
                    raise RuntimeError(
                        f"{operation_error}. Откат восстановления выполнен не полностью: "
                        + "; ".join(rollback_errors)
                    ) from operation_error
                raise

        try:
            self.backend.remove_deleted_rows(server, [login])
        except Exception as exc:
            # RouterOS restoration already succeeded. A stale archive row is safer
            # than rolling back a working VPN client because Google is unavailable.
            event(
                "SHEETS",
                "Не удалось убрать восстановленного клиента из архива",
                f"{server} · {login} · {exc}",
                level=30,
            )

        remap_text = ""
        if port_replacements:
            remap_text = " · замены портов: " + ", ".join(
                f"{old}→{new}" for old, new in sorted(port_replacements.items())
            )
        event(
            "VPN",
            "Клиент восстановлен из Google Sheets",
            f"{server} · {login} · {remote or 'без Remote Address'} · "
            f"портов {len(restored_ports)}{remap_text}",
        )
        return RestoreResult(
            server=server,
            login=login,
            remote_address=remote,
            ports=sorted(restored_ports),
            profile_created=profile_created,
            nat_created=len(created_nat_ids),
            port_replacements=dict(sorted(port_replacements.items())),
        )
