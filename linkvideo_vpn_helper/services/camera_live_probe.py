from __future__ import annotations

"""Bounded measurement of recent HLS media, only when B2O supplies a live URL.

Never guess media paths from server names. Avoid confusing HTTP download speed,
RTSP TCP availability or configured bitrate with actual encoded media bitrate.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
import re
import time
import os
import subprocess
from pathlib import Path

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
    program_time = None
    items = []
    for raw_line in playlist.splitlines()[:800]:
        line = raw_line.strip()
        if line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            try:
                program_time = datetime.fromisoformat(
                    line.split(":", 1)[1].strip().replace("Z", "+00:00")
                )
            except ValueError:
                program_time = None
        elif line.startswith("#EXTINF:"):
            try:
                length = float(line.split(":", 1)[1].split(",", 1)[0])
            except ValueError:
                length = None
        elif line and not line.startswith("#") and length is not None:
            full = urljoin(base, line)
            if 0.1 <= length <= 10.0:
                if any(_allowed_media(full, ext) for ext in (".ts", ".m4s", ".mp4")):
                    end_time = (
                        (program_time + timedelta(seconds=length)).isoformat()
                        if program_time is not None else ""
                    )
                    items.append((full, length, end_time))
                if program_time is not None:
                    program_time += timedelta(seconds=length)
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



def _recent_archive_playlists(report: CameraDiagnostics) -> tuple[str, ...]:
    """Restricted signed, recent DVR URLs from the existing archive player."""
    from linkvideo_vpn_helper.services.archive_service_core import ArchiveService, ArchiveSlice
    raw = report.metadata
    signature = str(raw.get("signature") or raw.get("wmsAuthSign") or "")
    stream = str(raw.get("main_stream_name") or raw.get("nimble_id")
                 or raw.get("stream_name") or "").strip()
    host = str(report.server or "").strip().lower()
    if (not host.endswith(".video.goodline.info") or host == "video.goodline.info"
            or not signature or len(signature) > 1024
            or any(ord(c) < 33 or ord(c) > 126 for c in signature)
            or not stream):
        return ()
    start = int(time.time()) - 90
    urls = []
    for app, item in ArchiveService._stream_candidates(stream, raw)[:2]:
        if not (re.fullmatch(r"[A-Za-z0-9_-]{1,128}", app)
                and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", item)):
            continue
        url = ArchiveService.playlist_url(
            ArchiveSlice(host, app, item, start, start + 90, signature, "info")
        )
        if _allowed_media(url, ".m3u8"):
            urls.append(url)
    return tuple(urls)


def _recent_archive_sample(report: CameraDiagnostics, opener):
    """Measure media bits per playback second; no guessing a live stream URL."""
    for playlist_url in _recent_archive_playlists(report):
        try:
            playlist = _fetch_bytes(opener, playlist_url, MAX_PLAYLIST).decode("utf-8", "replace")
            nested = next((
                urljoin(playlist_url, line.strip())
                for line in playlist.splitlines()
                if line.strip() and not line.strip().startswith("#")
                and ".m3u8" in line.split("?", 1)[0].lower()
            ), "")
            if nested and _allowed_media(nested, ".m3u8"):
                playlist_url = nested
                playlist = _fetch_bytes(opener, nested, MAX_PLAYLIST).decode("utf-8", "replace")
            segment = parse_recent_segment(playlist, playlist_url)
            if not segment:
                continue
            media_url, duration, timestamp = segment
            media = _fetch_bytes(opener, media_url, MAX_SEGMENT)
            if not media:
                continue
            bitrate = len(media) * 8 / duration / 1_000_000
            age = None
            if timestamp:
                try:
                    dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    if dt.tzinfo:
                        age = time.time() - dt.timestamp()
                except ValueError:
                    age = None
            recent = age is not None and -10 <= age <= 40
            return bitrate, timestamp, recent
        except Exception:
            continue
    return None



def _cached_ffmpeg() -> str:
    """Existing FFmpeg only: opening a camera must not download executables."""
    from linkvideo_vpn_helper.services.archive_service_core import ArchiveService
    for candidate in ArchiveService._ffmpeg_candidates():
        path = Path(candidate)
        if path.is_file() and ArchiveService._ffmpeg_usable(path):
            return str(path)
    return ""


def _probe_rtsp(report: CameraDiagnostics, *, ffmpeg_path: str = ""):
    """Sample three seconds of actual RTSP media off the UI thread.

    ffmpeg -progress reports media time independently of network download speed.
    MPEG-TS output contains minor mux overhead, so report an approximation.
    Never log subprocess arguments or stderr; RTSP URLs may have passwords.
    """
    url = str(report.rtsp_url or "")
    if not url.startswith(("rtsp://", "rtsps://")):
        return None
    ffmpeg = ffmpeg_path or _cached_ffmpeg()
    if not ffmpeg:
        return None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-rtsp_transport", "tcp", "-rw_timeout", "4000000",
        "-i", url, "-t", "3", "-map", "0:v:0",
        "-an", "-c:v", "copy", "-f", "mpegts",
        "-progress", "pipe:2", "pipe:1",
    ]
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=14, creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode or not 50_000 <= len(result.stdout) <= 12_000_000:
        return None
    times = re.findall(rb"(?:out_time_us|out_time_ms)=(\d+)", result.stderr)
    if not times:
        return None
    duration = int(times[-1]) / 1_000_000
    if duration < 0.5 or duration > 6:
        return None
    bitrate = len(result.stdout) * 8.0 / duration / 1_000_000
    if not 0.02 <= bitrate <= 80.0:
        return None
    return bitrate, datetime.now().astimezone().isoformat()


def probe_live_camera(report: CameraDiagnostics, *, opener=None) -> LiveProbe:
    """Probe the workstation network in the worker thread, never in Qt."""
    if report.ip:
        network = _ping_windows(report.ip)
        if report.rtsp_port:
            network += "\n" + _test_tcp(report.ip, report.rtsp_port)
        else:
            network += "\nTCP: порт не указан в админке — не проверялся."
    elif report.server and report.server.endswith(".video.goodline.info"):
        network = (
            "IP камеры отсутствует. Проверка медиа-сервера:\n" +
            _ping_windows(report.server) + "\n" +
            _test_tcp(report.server, 443)
        )
    else:
        network = "Нет адреса камеры или проверенного медиа-сервера для ICMP/TCP."

    stamp = _display_timestamp(report.last_stream)
    # Prefer actual administrator RTSP over a historical DVR sample.
    measured = _probe_rtsp(report) if report.rtsp_url else None
    if measured is not None:
        mbps, seen_at = measured
        return LiveProbe(
            f"≈{mbps:.2f} Мбит/с", _display_timestamp(seen_at), network,
            "Прямой трёхсекундный RTSP-замер через FFmpeg: получены "
            "видеопакеты. Примерный битрейт учитывает транспортный "
            "контейнер MPEG-TS, а не скорость скачивания или тариф.",
        )
    playlist = _playlist_url(report)
    if not playlist:
        opener = opener or build_opener(_NoRedirect())
        sample = _recent_archive_sample(report, opener)
        if sample is not None:
            mbps, timestamp, recent = sample
            if timestamp:
                stamp = _display_timestamp(timestamp)
            return LiveProbe(
                f"≈{mbps:.2f} Мбит/с" +
                ("" if recent else " (архив)"),
                stamp, network,
                ("Замер по недавнему сегменту видео." if recent else
                 "Замер по фрагменту доступного архива за последние 90 секунд; "
                 "не обязательно текущий битрейт.") +
                " Вычислено по байтам медиасегмента и длительности, "
                "не по скорости TCP и не по тарифу.",
            )
        return LiveProbe(
            "Не измерен", stamp, network,
            "Нет доступного медиасегмента: проверены только подтверждённые "
            "live-URL и подписанные недавние архивные плейлисты. "
            "Доступность HTTPS-сервера не подтверждает передачу видео.",
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
