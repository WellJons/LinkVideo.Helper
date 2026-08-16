from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from linkvideo_vpn_helper.services.google_key_discovery_compat import (
    discover_service_account_file,
    install_google_key_discovery,
)
from linkvideo_vpn_helper.services.vpn_sheets_sync import GoogleSheetsBackend, VPNSheetsSyncService
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials, VPNService


DEFAULT_SERVERS = [
    *(f"vpn{i:02d}.linkvideo.ru" for i in range(1, 11)),
    "rb-vpn01.linkvideo.ru",
    "kz-vpn01.linkvideo.ru",
]

_STOP = False


def _stop(*_args):
    global _STOP
    _STOP = True


def _required(name: str) -> str:
    value = str(os.getenv(name, "") or "").strip()
    if not value:
        raise SystemExit(f"Не задана переменная окружения {name}")
    return value


def _load_backend() -> GoogleSheetsBackend:
    """Use the same key discovery rules as desktop Helper.

    ``LINKVIDEO_SHEETS_SERVICE_ACCOUNT_FILE`` remains supported for a server
    deployment, but it is no longer mandatory when a valid service-account JSON
    already exists in a standard LinkVideo data directory.
    """
    install_google_key_discovery()
    backend = GoogleSheetsBackend.from_settings(None)
    if backend is not None:
        path = getattr(backend, "source_path", "") or discover_service_account_file(None)
        if path:
            print(f"Google Sheets key: {path}", flush=True)
        return backend

    searched = ", ".join(str(path) for path in (
        Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "LinkVideo" / "Helper",
        Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "LinkVideo.Helper",
        Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "LinkVideo.Helper" / "Helper",
    ))
    raise SystemExit(
        "Google service account JSON не найден. Укажите LINKVIDEO_SHEETS_SERVICE_ACCOUNT_FILE "
        f"или положите валидный service-account JSON в одну из папок: {searched}"
    )


def main() -> int:
    user = _required("LINKVIDEO_ROUTEROS_USER")
    password = _required("LINKVIDEO_ROUTEROS_PASSWORD")
    interval = max(60, int(os.getenv("LINKVIDEO_SYNC_INTERVAL_SECONDS", "300") or "300"))
    servers_env = str(os.getenv("LINKVIDEO_VPN_SERVERS", "") or "").strip()
    servers = [x.strip() for x in servers_env.split(",") if x.strip()] if servers_env else list(DEFAULT_SERVERS)

    backend = _load_backend()
    vpn = VPNService()
    sync = VPNSheetsSyncService(vpn, backend)
    creds = SessionCredentials(user, password, 8728, 4.5)

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)

    print(f"LinkVideo.VPNSync: серверов {len(servers)}, интервал {interval} сек.")
    while not _STOP:
        started = time.monotonic()
        for server in servers:
            if _STOP:
                break
            try:
                result = sync.sync_server(
                    server,
                    creds,
                    source="LinkVideo.VPNSync",
                    initiator="central-agent",
                )
                print(
                    f"{server}: OK clients={result.clients} added={result.added} "
                    f"changed={result.changed} deleted={result.deleted} restored={result.restored}",
                    flush=True,
                )
            except Exception as exc:
                # Ошибка одного VPN-сервера не превращается в удаление его клиентов
                # и не останавливает сверку остальных серверов.
                print(f"{server}: ERROR {exc}", file=sys.stderr, flush=True)

        elapsed = time.monotonic() - started
        wait = max(1.0, interval - elapsed)
        deadline = time.monotonic() + wait
        while not _STOP and time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
