from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIClient
from linkvideo_vpn_helper.services.errors import OperationCancelled
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials


@dataclass(slots=True)
class VPNServerBackupResult:
    server: str
    json_path: Path | None = None
    clients_csv: Path | None = None
    nat_csv: Path | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.json_path is not None and self.json_path.exists()


@dataclass(slots=True)
class VPNBackupBatchResult:
    folder: Path
    servers: list[VPNServerBackupResult]

    @property
    def success_count(self) -> int:
        return sum(1 for x in self.servers if x.ok)

    @property
    def failure_count(self) -> int:
        return len(self.servers) - self.success_count


class VPNBackupService:
    """Полный аварийный снимок конфигурации LinkVideo VPN-сервера.

    FULL JSON намеренно содержит sensitive-поля RouterOS (включая пароли
    /ppp secret), потому что назначение этой выгрузки — восстановление после
    неудачной автоматизации. UI всегда предупреждает об этом до запуска.
    """

    SECTION_PATHS = (
        ("identity", "/system/identity"),
        ("resources", "/system/resource"),
        ("ppp_secrets", "/ppp/secret"),
        ("ppp_profiles", "/ppp/profile"),
        ("ppp_active_snapshot", "/ppp/active"),
        ("ppp_aaa", "/ppp/aaa"),
        ("ip_pools", "/ip/pool"),
        ("ip_addresses", "/ip/address"),
        ("routes", "/ip/route"),
        ("firewall_nat", "/ip/firewall/nat"),
        ("firewall_filter", "/ip/firewall/filter"),
        ("firewall_mangle", "/ip/firewall/mangle"),
        ("firewall_raw", "/ip/firewall/raw"),
        ("firewall_address_list", "/ip/firewall/address-list"),
        ("ip_services", "/ip/service"),
        ("l2tp_server", "/interface/l2tp-server/server"),
        ("system_scripts", "/system/script"),
        ("system_scheduler", "/system/scheduler"),
        ("system_logging", "/system/logging"),
    )

    @staticmethod
    def default_root() -> Path:
        return Path.home() / "Documents" / "LinkVideo.Helper" / "VPN_Backups"

    @staticmethod
    def _safe_name(server: str) -> str:
        value = re.sub(r"[^A-Za-z0-9._-]+", "_", str(server or "server").strip())
        return value[:120] or "server"

    @staticmethod
    def _write_csv(path: Path, rows: list[dict]) -> None:
        fields: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for key in row.keys():
                key = str(key)
                if key not in seen:
                    seen.add(key)
                    fields.append(key)
        if not fields:
            fields = ["empty"]
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fields})

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def collect_server(self, server: str, creds: SessionCredentials, cancel_event=None) -> tuple[dict, list[str]]:
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("Операция отменена пользователем")
        sections: dict[str, list[dict]] = {}
        errors: list[str] = []
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            for key, path in self.SECTION_PATHS:
                if cancel_event is not None and cancel_event.is_set():
                    raise OperationCancelled("Операция отменена пользователем")
                try:
                    sections[key] = api.print(path)
                except Exception as exc:
                    # Разные версии RouterOS могут не иметь отдельного меню. Один
                    # неподдерживаемый раздел не должен уничтожать весь backup.
                    sections[key] = []
                    errors.append(f"{path}: {exc}")
        payload = {
            "format": "LinkVideo.Helper VPN FULL backup",
            "format_version": 1,
            "sensitive": True,
            "warning": "Файл содержит чувствительные параметры RouterOS, включая PPP passwords.",
            "server": server,
            "exported_at_utc": datetime.now(timezone.utc).isoformat(),
            "sections": sections,
            "collection_errors": errors,
        }
        return payload, errors

    def write_server(self, server: str, payload: dict, folder: Path, errors: list[str] | None = None) -> VPNServerBackupResult:
        folder.mkdir(parents=True, exist_ok=True)
        base = self._safe_name(server)
        json_path = folder / f"{base}_FULL.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        clients = list((payload.get("sections") or {}).get("ppp_secrets") or [])
        nat = list((payload.get("sections") or {}).get("firewall_nat") or [])
        clients_csv = folder / f"{base}_PPP_Secrets.csv"
        nat_csv = folder / f"{base}_NAT.csv"
        self._write_csv(clients_csv, clients)
        self._write_csv(nat_csv, nat)
        return VPNServerBackupResult(server, json_path, clients_csv, nat_csv, list(errors or []))

    def backup_one(self, server: str, creds: SessionCredentials, prefix: str = "AUTO_BEFORE_LV") -> VPNServerBackupResult:
        """Create one full server snapshot before a potentially mutating action."""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = self.default_root() / f"{prefix}_{self._safe_name(server)}_{stamp}"
        folder.mkdir(parents=True, exist_ok=True)
        payload, errors = self.collect_server(server, creds)
        result = self.write_server(server, payload, folder, errors)
        manifest_files = []
        for path in (result.json_path, result.clients_csv, result.nat_csv):
            if path and path.exists():
                manifest_files.append({
                    "name": path.name,
                    "size": path.stat().st_size,
                    "sha256": self._sha256(path),
                })
        manifest = {
            "format": "LinkVideo.Helper VPN backup manifest",
            "format_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "sensitive": True,
            "reason": prefix,
            "servers_requested": [server],
            "servers_ok": [server] if result.ok else [],
            "servers_failed": [] if result.ok else [{"server": server, "errors": result.errors}],
            "files": manifest_files,
        }
        (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result

    def backup_all(
        self,
        servers: list[str],
        creds: SessionCredentials,
        progress: Callable[[int, int, str], None] | None = None,
        cancel_event=None,
    ) -> VPNBackupBatchResult:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = self.default_root() / f"FULL_{stamp}"
        folder.mkdir(parents=True, exist_ok=True)
        results: list[VPNServerBackupResult] = []
        total = len(servers)
        for index, server in enumerate(servers, 1):
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelled("Операция отменена пользователем")
            if progress:
                progress(index - 1, total, server)
            try:
                payload, errors = self.collect_server(server, creds, cancel_event)
                results.append(self.write_server(server, payload, folder, errors))
            except OperationCancelled:
                raise
            except Exception as exc:
                results.append(VPNServerBackupResult(server=server, errors=[str(exc)]))
            if progress:
                progress(index, total, server)

        manifest_files = []
        for result in results:
            for path in (result.json_path, result.clients_csv, result.nat_csv):
                if path and path.exists():
                    manifest_files.append({
                        "name": path.name,
                        "size": path.stat().st_size,
                        "sha256": self._sha256(path),
                    })
        manifest = {
            "format": "LinkVideo.Helper VPN backup manifest",
            "format_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "sensitive": True,
            "servers_requested": servers,
            "servers_ok": [x.server for x in results if x.ok],
            "servers_failed": [{"server": x.server, "errors": x.errors} for x in results if not x.ok],
            "files": manifest_files,
        }
        (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return VPNBackupBatchResult(folder, results)
