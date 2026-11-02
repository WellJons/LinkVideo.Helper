from __future__ import annotations

"""Read-only diagnostics for a camera that is explicitly resolved through B2O.

Checks originate from the operator workstation, NOT the DVR/test server.
No arbitrary hostname/URL is accepted: the address must come from the
authorized B2O camera record. No RTSP credentials or raw response are logged.
"""

from dataclasses import dataclass
import ipaddress
import os
import socket
import subprocess
import time

from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.b2o_camera_preview import fetch_camera_preview


@dataclass(frozen=True)
class CameraDiagnostics:
    camera_id: str
    server: str
    ip: str
    rtsp_port: int
    ping_text: str
    tcp_text: str
    preview: bytes | None


def _camera_ip(record: dict) -> str:
    for name in ("ip_address", "camera_ip", "ip", "device_ip", "ipAddress", "cameraIp"):
        value = str(record.get(name) or "").strip()
        if not value:
            continue
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.is_unspecified or address.is_multicast or address.is_loopback or address.is_link_local:
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
    return "ICMP: ответ получен." if result.returncode == 0 else "ICMP: нет ответа (возможно, блокируется)."


def _test_tcp(ip: str, port: int) -> str:
    started = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=2.0):
            pass
    except (OSError, TimeoutError):
        return f"TCP:{port} — не отвечает с этого компьютера."
    elapsed = int((time.monotonic() - started) * 1000)
    return f"TCP:{port} — открыт с этого компьютера ({elapsed} мс)."


def inspect_camera(
    b2o: B2OService, camera_id: str, operator_id: int, *,
    with_preview: bool = True, with_network: bool = True,
) -> CameraDiagnostics:
    """Probe only a B2O-authorized camera; never scan a caller-supplied IP."""
    operator = b2o.resolve_operator_id(camera_id, operator_id)
    camera = b2o.camera(operator, camera_id)
    ip = _camera_ip(camera.raw)
    port = _camera_rtsp_port(camera.raw)
    if not ip:
        ping_text = "IP не передан B2O — ICMP не выполнялся."
        tcp_text = "IP не передан B2O — TCP не проверялся."
    elif not with_network:
        ping_text = "ICMP не запрашивался."
        tcp_text = "TCP не запрашивался."
    else:
        ping_text = _ping_windows(ip)
        tcp_text = _test_tcp(ip, port)
    preview = None
    if with_preview:
        try:
            preview = fetch_camera_preview(
                b2o.operator_country(operator), camera.camera_id, b2o.token()
            )
        except Exception:
            preview = None
    return CameraDiagnostics(
        camera_id=camera.label, server=camera.server, ip=ip,
        rtsp_port=port, ping_text=ping_text, tcp_text=tcp_text,
        preview=preview,
    )
