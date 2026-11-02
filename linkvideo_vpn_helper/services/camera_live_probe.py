from __future__ import annotations

"""Bounded measurement of recent HLS media, only when B2O supplies a live URL.

Never guess media paths from server names. Avoid confusing HTTP download speed,
RTSP TCP availability or configured bitrate with actual encoded media bitrate.
"""
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
import re

from linkvideo_vpn_helper.services.camera_diagnostics import (
    CameraDiagnostics, _ping_windows, _test_tcp,
)

MAX_PLAYLIST = 128 * 1024
MAX_SEGMENT = 6 * 1024 * 1024


@dataclass(frozen=True)
class LiveProbe:
    bitrate_mbps: str
    last_stream: str
    connectivity: str
    detail: str


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _allowed_media(url: str, suffix: str) -> bool:
    if not isinstance(url, str) or not 12 <= len(url) <= 2048:
        return False
    try:
        parsed = urlsplit(url)
        return bool(
            parsed.scheme == "https"
            and parsed.hostname and parsed.hostname.endswith(".video.goodline.info")
            and parsed.hostname != "video.goodline.info"
            and parsed.port in (None, 443)
            and not parsed.username and not parsed.password and not parsed.fragment
            and parsed.path.lower().endswith(suffix)
        )
    except ValueError:
        return False


def _playlist_url(report: CameraDiagnostics) -> str:
    for name in ("live_hls_url", "hls_live_url", "live_playlist_url",
                 "playlist_url", "hls_url"):
        url = report.metadata.get(name)
        if _allowed_media(url, ".m3u8"):
            return url
    return ""


def parse_recent_segment(playlist: str, base: str) -> tuple[str, float, str] | None:
    """Last plain TS/fMP4 segment and declared media seconds, not download speed."""
    length = None
    last_time = ""
    items = []
    for raw_line in playlist.splitlines()[:800]:
        line = raw_line.strip()
        if line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            last_time = line.split(":", 1)[1].strip()[:64]
        elif line.startswith("#EXTINF:"):
            try:
                length = float(line.split(":", 1)[1].split(",", 1)[0])
            except ValueError:
                length = None
        elif line and not line.startswith("#") and length is not None:
            full = urljoin(base, line)
            if any(_allowed_media(full, ext) for ext in (".ts", ".m4s", ".mp4")):
                if 0.1 <= length <= 10.0:
                    items.append((full, length, last_time))
            length = None
    return items[-1] if items else None


def _fetch_bytes(opener, url: str, max_bytes: int) -> bytes:
    req = Request(url, headers={"User-Agent": "LinkVideo.Helper/3.0",
                                "Accept": "*/*"})
    with opener.open(req, timeout=5) as response:
        raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError("Слишком большой медиасегмент.")
        return raw


def _display_timestamp(value: str) -> str:
    if not value:
        return "Нет подтверждённой даты"
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return value[:50] + " (часовой пояс не указан)"
        return dt.astimezone().strftime("%d.%m.%Y %H:%M:%S")
    except ValueError:
        return value[:50] + " (формат сервера)"


def probe_live_camera(report: CameraDiagnostics, *, opener=None) -> LiveProbe:
    """Probe the workstation network in the worker thread, never in Qt."""
    if report.ip:
        network = (_ping_windows(report.ip) + "\n" +
                   _test_tcp(report.ip, report.rtsp_port))
    elif report.server and report.server.endswith(".video.goodline.info"):
        network = (
            "IP камеры отсутствует. Проверка медиа-сервера:\n" +
            _ping_windows(report.server) + "\n" +
            _test_tcp(report.server, 443)
        )
    else:
        network = "Нет адреса камеры или проверенного медиа-сервера для ICMP/TCP."

    stamp = _display_timestamp(report.last_stream)
    playlist = _playlist_url(report)
    if not playlist:
        return LiveProbe(
            "Недоступен", stamp, network,
            "B2O не предоставил проверенную ссылку на текущий HLS-поток; "
            "скорость TCP или тариф не выдаются за битрейт камеры.",
        )

    opener = opener or build_opener(_NoRedirect())
    try:
        raw = _fetch_bytes(opener, playlist, MAX_PLAYLIST).decode("utf-8", "replace")
        segment = parse_recent_segment(raw, playlist)
        if segment is None:
            return LiveProbe(
                "Не измерен", stamp, network,
                "Live-плейлист не содержит доступного медиасегмента.",
            )
        url, duration, timestamp = segment
        # Compute encoded media bits per represented media second.
        # This is NOT the download throughput of the operator's connection.
        media = _fetch_bytes(opener, url, MAX_SEGMENT)
        if not media:
            raise ValueError("Пустой сегмент.")
        bitrate = len(media) * 8.0 / duration / 1_000_000
        if timestamp:
            stamp = _display_timestamp(timestamp)
        return LiveProbe(
            f"{bitrate:.2f} Мбит/с", stamp, network,
            f"Замер по последнему HLS-сегменту ({duration:.1f} с видео), "
            "не заявленный тарифный битрейт.",
        )
    except Exception:
        return LiveProbe(
            "Не измерен", stamp, network,
            "Не удалось прочитать live-медиасегмент: поток может быть отключён "
            "или не допускает внешнюю проверку.",
        )
