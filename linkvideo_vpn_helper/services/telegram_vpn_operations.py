from __future__ import annotations

"""Telegram VPN provisioning through the same VPNService as desktop Helper.

This module may change MikroTik configuration. It is reachable only through
a confirmed, one-use Telegram action from an invited private-chat account.
No MikroTik credentials are embedded in application resources.
"""

from dataclasses import dataclass
import re
import secrets
import sqlite3
import threading
import time

from linkvideo_vpn_helper.services.server_registry import ServerRegistry
from linkvideo_vpn_helper.services.telegram_invites import state_database_path
from linkvideo_vpn_helper.services.telegram_service_runtime import (
    service_vpn_credentials,
)
from linkvideo_vpn_helper.services.vpn_service import VPNService

_LOGIN = re.compile(r"[A-Za-z0-9_.-]{3,64}\Z")
_COUNTRIES = ("Россия", "Казахстан", "Беларусь")
_LOCK = threading.Lock()


@dataclass(frozen=True)
class VPNProposal:
    request_id: str
    login: str
    expected_login: str
    server: str
    country: str
    ports: int
    issued_at: float


def _audit_db():
    path = state_database_path().parent / "vpn_operations.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=8)
    db.execute(
        "CREATE TABLE IF NOT EXISTS vpn_requests ("
        "id TEXT PRIMARY KEY, actor INTEGER NOT NULL, "
        "login TEXT NOT NULL, server TEXT NOT NULL,"
        "state TEXT NOT NULL, created_at INTEGER NOT NULL)"
    )
    return db


class TelegramVPNOperations:
    def __init__(self, service=None, registry=None, credentials_provider=None):
        self.service = service or VPNService()
        # Defaults follow the desktop ServerRegistry (including regions).
        # Service does not read another Windows user's QSettings.
        if registry is None:
            from linkvideo_vpn_helper.services.telegram_b2o_session import VolatileSettings
            registry = ServerRegistry(VolatileSettings())
        self.registry = registry
        self.credentials_provider = credentials_provider or service_vpn_credentials

    @staticmethod
    def validate_login(value: str) -> str:
        value = str(value or "").strip()
        if not _LOGIN.fullmatch(value):
            raise ValueError("Логин VPN: 3–64 символа (цифры, латиница, _ . -).")
        return value

    def prepare(self, login: str, country: str = "Россия",
                ports: int = 3) -> VPNProposal:
        login = self.validate_login(login)
        if country not in _COUNTRIES or not isinstance(ports, int) or not 1 <= ports <= 14:
            raise ValueError("Неизвестная страна или количество портов.")
        hosts = [
            s.host for s in self.registry.all(include_disabled=False)
            if s.country == country
        ]
        if not hosts:
            raise RuntimeError("Нет доступных VPN-серверов этой страны.")
        creds = self.credentials_provider()
        # Desktop path: pick_best_server_parallel already checks capacity,
        # current L2TP load, CPU/RAM and availability of the VPN servers.
        server = self.service.pick_best_server_parallel(hosts, creds)
        # Desktop path: suggest_next_login examines existing PPP secrets.
        candidate = self.service.suggest_next_login(server, creds, login)
        if not candidate:
            raise RuntimeError("Не удалось подобрать логин VPN.")
        return VPNProposal(
            request_id=secrets.token_hex(12),
            login=login, expected_login=candidate,
            server=server, country=country, ports=ports,
            issued_at=time.monotonic(),
        )

    def execute(self, actor: int, proposal: VPNProposal):
        if type(actor) is not int or actor <= 0:
            raise ValueError("Неверный Telegram ID.")
        if time.monotonic() - proposal.issued_at > 300:
            raise ValueError("Подтверждение устарело — начни создание заново.")
        if not re.fullmatch(r"[0-9a-f]{24}", proposal.request_id):
            raise ValueError("Неверный идентификатор операции.")
        if proposal.country not in _COUNTRIES or not 1 <= proposal.ports <= 14:
            raise ValueError("Недопустимый набор параметров VPN.")
        if proposal.server not in [
            s.host for s in self.registry.all(include_disabled=False)
            if s.country == proposal.country
        ]:
            raise ValueError("Сервер больше не доступен для этой страны.")
        with _LOCK:
            with _audit_db() as db:
                existing = db.execute(
                    "SELECT 1 FROM vpn_requests WHERE id=?", (proposal.request_id,)
                ).fetchone()
                if existing:
                    raise RuntimeError("Заявка уже была обработана. Повторное создание запрещено.")
                db.execute(
                    "INSERT INTO vpn_requests(id,actor,login,server,state,created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (proposal.request_id, actor, proposal.expected_login,
                     proposal.server, "started", int(time.time())),
                )
                db.commit()
            try:
                creds = self.credentials_provider()
                # Desktop creates profiles, L2TP secrets and NAT rules with
                # available IP/port checks and a rollback on partial errors.
                records = self.service.create_clients_batch(
                    proposal.server, creds, proposal.expected_login,
                    proposal.ports, 1,
                )
                if not records:
                    raise RuntimeError("MikroTik не вернул созданного клиента.")
                with _audit_db() as db:
                    db.execute(
                        "UPDATE vpn_requests SET state='created' WHERE id=?",
                        (proposal.request_id,),
                    )
                    db.commit()
                return records[0]
            except Exception:
                with _audit_db() as db:
                    db.execute(
                        "UPDATE vpn_requests SET state='failed' WHERE id=?",
                        (proposal.request_id,),
                    )
                    db.commit()
                raise
