from __future__ import annotations

"""Inspect the camera's administrator-provided stream without persisting secrets."""

from dataclasses import dataclass
from urllib.parse import urlsplit
import re


@dataclass(frozen=True)
class CameraStream:
    url: str
    host: str
    port: int


def parse_camera_stream(payload: dict) -> CameraStream | None:
    data = payload.get("data", payload) if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    url = data.get("camera_stream_main")
    if not isinstance(url, str) or not 10 <= len(url) <= 4096:
        return None
    if any(ord(c) < 32 for c in url):
        return None
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port or 554
        if parsed.scheme.lower() != "rtsp" or not 1 <= port <= 65535:
            return None
        # Only company camera domains are accepted as external probe targets.
        if not (hostname.endswith(".linkvideo.ru")
                or hostname.endswith(".video.goodline.info")):
            return None
        if not re.fullmatch(r"[a-z0-9.-]{1,253}", hostname):
            return None
        return CameraStream(url, hostname, port)
    except (ValueError, TypeError):
        return None


def fetch_camera_stream(b2o, camera_id: str, operator_id: int):
    number, _ = b2o.normalize_camera_id(camera_id, operator_id)
    token = b2o.token()
    if not token:
        return None
    url = (
        "https://api.b2o.goodline.info/ords/mobile/vc2/operators/"
        f"{int(operator_id)}/cameras/{number}/camera-streams-urls"
    )
    try:
        return parse_camera_stream(b2o.request_json(url, token, timeout=8))
    except Exception:
        return None
