from __future__ import annotations

"""Read-only RouterOS fleet monitoring -> Google Sheets, without PPP secrets.

This agent is independent from the desktop Qt event loop. It is intended to
run as a dedicated scheduled task on a controlled always-on host. It does NOT
install, enable, disable or remove RouterOS accounts or LV schedulers.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from linkvideo_vpn_helper.services.vpn_service import VPNService, SessionCredentials
from linkvideo_vpn_helper.services.vpn_automation_service import VPNAutomationService
from linkvideo_vpn_helper.services.vpn_sheets_sync import GoogleSheetsBackend, SPREADSHEET_ID

HEALTH_SHEET = "LV Мониторинг"
HEADER = (
    "VPN-сервер", "Проверка (UTC)", "RouterOS API", "L2TP онлайн",
    "Учётных записей", "NAT портов онлайн", "NAT портов всего",
    "CPU %", "RAM %", "LV-автоматика", "Версия LV",
    "Запуски Activity", "Запуски Restore", "Запуски Aging",
)
DEFAULT_SERVERS = [
    *(f"vpn{i:02d}.linkvideo.ru" for i in range(1, 11)),
    "rb-vpn01.linkvideo.ru", "kz-vpn01.linkvideo.ru",
]
MAX_ROWS = 100
STOP = False


def _stop(_signal, _frame):
    global STOP
    STOP = True


def load_config(path: Path) -> dict:
    if not Path(path).is_file():
        raise ValueError(f"Файл настроек мониторинга не найден: {path}")
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("Ожидается JSON-объект")
    user = str(payload.get("routeros_username") or "").strip()
    password = str(payload.get("routeros_password") or "")
    if not user or not password:
        raise ValueError("Отсутствует учётная запись RouterOS")
    raw_hosts = payload.get("servers", DEFAULT_SERVERS)
    if not isinstance(raw_hosts, list) or not raw_hosts:
        raise ValueError("Список VPN-серверов должен быть непустым массивом")
    hosts = []
    for raw in raw_hosts:
        host = str(raw).strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,252}", host) or ".." in host or host.endswith("."):
            raise ValueError("Некорректный DNS-адрес VPN-сервера")
        if host not in hosts:
            hosts.append(host)
    if len(hosts) > MAX_ROWS - 1:
        raise ValueError(f"Допускается не более {MAX_ROWS-1} серверов")
    payload["servers"] = hosts
    payload["routeros_username"] = user
    payload["routeros_password"] = password
    payload["interval_seconds"] = max(60, int(payload.get("interval_seconds", 300)))
    payload["routeros_timeout"] = max(2, min(30, float(payload.get("routeros_timeout", 6))))
    payload["routeros_port"] = int(payload.get("routeros_port", 8728))
    if not 1 <= payload["routeros_port"] <= 65535:
        raise ValueError("Некорректный порт RouterOS API")
    return payload


def collect_server(host: str, credentials: SessionCredentials, vpn, automation) -> list:
    checked = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    try:
        stats = vpn.analyze_server_quick(host, credentials)
    except Exception as exc:
        # Never print RouterOS request details, accounts, secrets or tokens.
        return [host, checked, "Ошибка: " + type(exc).__name__] + [""] * (len(HEADER) - 3)
    try:
        auto = automation.get_status(host, credentials)
        status = auto.state_text
        version = auto.version
        counts = [auto.activity_run_count, auto.restore_run_count, auto.aging_run_count]
    except Exception:
        status, version, counts = "Не удалось проверить LV", "", ["", "", ""]
    return [
        host, checked, "OK", int(stats.clients_online), int(stats.clients_total),
        int(stats.ports_active), int(stats.ports_total),
        stats.cpu_load if stats.cpu_load is not None else "",
        stats.memory_usage_percent if stats.memory_usage_percent is not None else "",
        status, version, *counts,
    ]


def collect_fleet(servers, credentials, vpn=None, automation=None, max_workers=4) -> list[list]:
    vpn = vpn or VPNService()
    automation = automation or VPNAutomationService()
    result = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(servers))) as pool:
        pending = {pool.submit(collect_server, host, credentials, vpn, automation): host for host in servers}
        for future in as_completed(pending):
            host = pending[future]
            try:
                result[host] = future.result()
            except Exception as exc:
                result[host] = [
                    host, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
                    "Ошибка: " + type(exc).__name__,
                ] + [""] * (len(HEADER)-3)
    return [result[host] for host in servers]


def prepare_backend(config: dict) -> GoogleSheetsBackend:
    key_file = Path(str(config.get("service_account_file") or ""))
    if not key_file.is_file():
        raise ValueError("Не найден JSON сервисного аккаунта Google")
    info = json.loads(key_file.read_text(encoding="utf-8-sig"))
    if info.get("type") != "service_account":
        raise ValueError("Ожидается Google service-account JSON")
    return GoogleSheetsBackend(info, spreadsheet_id=str(config.get("spreadsheet_id") or SPREADSHEET_ID))


def publish_health(backend: GoogleSheetsBackend, records: list[list]) -> None:
    """Single atomic update to ONLY the dedicated non-secret monitoring sheet."""
    if any(len(record) != len(HEADER) for record in records):
        raise ValueError("Некорректная схема отчёта")
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{backend.spreadsheet_id}"
    metadata = backend._request("GET", base, params={"fields": "sheets(properties(title))"})
    titles = {
        str((item.get("properties") or {}).get("title", ""))
        for item in list(metadata.get("sheets") or [])
    }
    if HEALTH_SHEET not in titles:
        backend._request("POST", base + ":batchUpdate", payload={
            "requests": [{"addSheet": {"properties": {
                "title": HEALTH_SHEET,
                "gridProperties": {"rowCount": MAX_ROWS + 1, "columnCount": len(HEADER)},
            }}}],
        })
    rows = [list(HEADER)] + records
    rows += [[""] * len(HEADER) for _ in range(MAX_ROWS - len(rows))]
    backend._request("POST", base + "/values:batchUpdate", payload={
        "valueInputOption": "RAW",
        "data": [{
            "range": f"'{HEALTH_SHEET}'!A1:N{MAX_ROWS}",
            "majorDimension": "ROWS",
            "values": rows,
        }],
    })


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only background LV automation / VPN fleet report")
    parser.add_argument("--config", type=Path, required=True,
                        help="Admin-protected JSON config; never pass credentials in CLI")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="Read RouterOS only; DO NOT contact Google Sheets")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    logger = logging.getLogger("lv-fleet-monitor")
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler(sys.stdout))
    log_file = Path(str(config.get("log_file") or args.config.parent / "vpn-health.log"))
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.addHandler(RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"))
    backend = None if args.dry_run else prepare_backend(config)
    creds = SessionCredentials(
        config["routeros_username"], config["routeros_password"],
        config["routeros_port"], config["routeros_timeout"],
    )
    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)
    while not STOP:
        start = time.monotonic()
        records = collect_fleet(config["servers"], creds)
        failed = sum(1 for row in records if row[2] != "OK")
        logger.info("Read-only fleet snapshot: servers=%d online=%d unavailable=%d",
                    len(records), len(records)-failed, failed)
        if backend is not None:
            try:
                publish_health(backend, records)
                logger.info("Google Sheets monitoring published successfully")
            except Exception as exc:
                logger.error("Google Sheets sync failed: %s", type(exc).__name__)
        if args.once:
            if args.dry_run and failed:
                logger.error("Read-only preflight failed: %d server(s) unavailable", failed)
                return 2
            break
        while not STOP and time.monotonic() - start < config["interval_seconds"]:
            time.sleep(min(1.0, config["interval_seconds"] - (time.monotonic() - start)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
