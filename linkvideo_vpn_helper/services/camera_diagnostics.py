from __future__ import annotations

"""Read-only information about an authorized B2O camera.

Camera information must not require archive-only signature, DVR hostname,
HLS playlist, or a preview image. Requests use a fixed B2O HTTPS endpoint;
no user-supplied URLs, arbitrary network targets or secrets are exposed.
"""

from dataclasses import dataclass, field
import ipaddress
import os
import re
import socket
import subprocess
import time

from linkvideo_vpn_helper.services.archive_service_core import B2OService


@dataclass(frozen=True)
class CameraDiagnostics:
    camera_id: str
    server: str
    ip: str
    rtsp_port: int
    ping_text: str
    tcp_text: str
    preview: bytes | None = None  # Compatibility; the information tab never loads previews.
    operator: str = ""
    name: str = ""
    status: str = ""
    serial: str = ""
    account_id: str = ""
    archive_depth: str = ""
    model: str = ""
    # Only the modal uses raw API metadata to locate an approved live playlist.
    # Never log it: it may contain signed URLs.
    metadata: dict = field(default_factory=dict, repr=False, compare=False)
    last_stream: str = ""


class CameraLookupError(Exception):
    """Safe user-facing error without API bodies, credentials, or URLs."""


def _field(record: dict, *names: str) -> str:
    for key in names:
        value = record.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            text = str(value).strip()
            if text:
                return text[:160]
    return ""


def _camera_ip(record: dict) -> str:
    for name in ("ip_address", "camera_ip", "ip", "device_ip", "ipAddress", "cameraIp"):
        value = str(record.get(name) or "").strip()
        if not value:
            continue
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if (address.is_unspecified or address.is_multicast or address.is_loopback
                or address.is_link_local):
            continue
        return str(address)
    return ""


def _camera_rtsp_port(record: dict) -> int:
    for name in ("rtsp_port", "rtspPort", "port_rtsp"):
        try:
            port = int(record.get(name))
            if 1 <= port <= 65535:
                return port
        except (TypeError, ValueError):
            continue
    return 554


def _ping_windows(ip: str) -> str:
    if os.name != "nt":
        return "ICMP доступен только в Windows-версии Helper."
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        result = subprocess.run(
            ["ping.exe", "-n", "1", "-w", "1500", ip],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=4, check=False, creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "ICMP: нет ответа или проверка заблокирована."
    return ("ICMP: ответ получен." if result.returncode == 0
            else "ICMP: нет ответа (возможно, блокируется).")


def _test_tcp(ip: str, port: int) -> str:
    started = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=2.0):
            pass
    except (OSError, TimeoutError):
        return f"TCP:{port} — не отвечает с этого компьютера."
    elapsed = int((time.monotonic() - started) * 1000)
    return f"TCP:{port} — открыт с этого компьютера ({elapsed} мс)."


def _read_camera(b2o: B2OService, camera_id: str, operator_id: int, token: str):
    number, label = b2o.normalize_camera_id(camera_id, operator_id)
    url = b2o.CAMERA_URL.format(operator=int(operator_id), camera=number)
    payload = b2o.request_json(url, token, timeout=15)
    if not isinstance(payload, dict):
        raise CameraLookupError("Админка вернула некорректный ответ.")
    row = payload.get("data", payload)
    if isinstance(row, list):
        row = row[0] if row else None
    if not isinstance(row, dict) or not row:
        raise CameraLookupError("Админка не вернула карточку камеры.")
    return row, label


def _error_message(exc: Exception) -> str:
    if isinstance(exc, CameraLookupError):
        return str(exc)
    msg = str(exc)
    # Only return these vetted categories, never raw HTTP bodies or URLs.
    if "HTTP 401" in msg or "истекла" in msg.lower():
        return "Сессия админки устарела. Переподключите аккаунт «Админка»."
    if "HTTP 403" in msg:
        return "У учётной записи нет прав на просмотр этой камеры."
    if "HTTP 404" in msg:
        return "Камера не найдена по указанному ID. Проверьте страну и номер."
    if "HTTP 400" in msg or "HTTP 422" in msg:
        return "Админка не приняла ID камеры. Проверьте номер и страну."
    if "Требуется авторизация" in msg:
        return "Войдите в аккаунт «Админка» в боковой панели."
    if "недоступен" in msg.lower() or isinstance(exc, (ConnectionError, TimeoutError)):
        return "Нет ответа API админки. Проверьте соединение и повторите запрос."
    return "Не удалось прочитать карточку камеры. Проверьте ID, страну и права аккаунта."


def inspect_camera(
    b2o: B2OService, camera_id: str, operator_id: int, *,
    with_preview: bool = False, with_network: bool = True,
) -> CameraDiagnostics:
    """Read camera metadata without requiring archive fields or a preview."""
    operator = b2o.resolve_operator_id(camera_id, operator_id)
    number, label = b2o.normalize_camera_id(camera_id, operator)
    token = b2o.token()
    if not token:
        raise CameraLookupError("Войдите в аккаунт «Админка» в боковой панели.")
    try:
        raw, label = _read_camera(b2o, camera_id, operator, token)
    except Exception as exc:
        # Some long numeric strings are device serials rather than B2O camera
        # IDs. Fall back only for an exact serial match and a genuine 404.
        is_not_found = "HTTP 404" in str(exc)
        if is_not_found and re.fullmatch(r"[0-9]{8,18}", camera_id.strip()):
            from linkvideo_vpn_helper.services.agent_b2o_preview import b2o_camera_for_serial
            try:
                match = b2o_camera_for_serial(camera_id.strip(), token, b2o)
                if match is not None:
                    country, resolved_id = match
                    matches = {"Россия": 241, "Беларусь": 1741, "Казахстан": 1721}
                    operator = matches[country]
                    raw, label = _read_camera(b2o, resolved_id, operator, token)
                else:
                    raise CameraLookupError(
                        "Камера с таким ID или точным серийным номером не найдена."
                    )
            except CameraLookupError:
                raise
            except Exception:
                raise CameraLookupError(
                    "Не удалось найти камеру по ID или серийному номеру."
                ) from None
        else:
            raise CameraLookupError(_error_message(exc)) from None

    ip = _camera_ip(raw)
    port = _camera_rtsp_port(raw)
    if not ip:
        ping_text = "ICMP: IP не передан админкой, проверка не выполнялась."
        tcp_text = "TCP: IP не передан админкой, проверка не выполнялась."
    elif not with_network:
        ping_text = "ICMP: проверка не запрошена."
        tcp_text = "TCP: проверка не запрошена."
    else:
        ping_text = _ping_windows(ip)
        tcp_text = _test_tcp(ip, port)

    # No image requests: information must remain available without previews.
    return CameraDiagnostics(
        camera_id=label,
        server=b2o.normalize_vcore_host(_field(raw, "server_name", "server")),
        ip=ip, rtsp_port=port, ping_text=ping_text, tcp_text=tcp_text,
        operator=b2o.operator_country(operator),
        name=_field(raw, "camera_name", "name", "title"),
        status=_field(raw, "camera_status", "status", "state"),
        serial=_field(raw, "serial_number", "serialNumber", "serial", "sn"),
        account_id=_field(raw, "id_account", "account_id"),
        archive_depth=_field(raw, "archive_depth", "archive_days"),
        model=_field(raw, "camera_model", "model", "device_model"),
        last_stream=_field(raw, "last_stream_at", "last_video_at",
                           "last_frame_at", "last_online_at"),
        metadata=dict(raw),
    )
