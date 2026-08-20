from __future__ import annotations

"""Archive download transport compatibility for 3.0.11.

Keep the modern DVR discovery logic, but restore the operator-facing download
methods that existed in the mature Helper UI:

* FFmpeg        - HLS/remux with audio;
* Curl          - Nimble ``export_mp4`` transport;
* Без звука     - HLS/remux with ``-an`` forced.

FFmpeg is intentionally NOT part of the installer.  The first FFmpeg-based
operation downloads it into LocalAppData with visible byte progress, validates
it, and reuses the cached executable afterwards.
"""

import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from linkvideo_vpn_helper.services.errors import OperationCancelled


ARCHIVE_DOWNLOAD_METHODS: tuple[tuple[str, str], ...] = (
    ("1. FFmpeg", "ffmpeg"),
    ("2. Curl", "curl"),
    ("3. Без звука", "ffmpeg_no_audio"),
)
DEFAULT_ARCHIVE_DOWNLOAD_METHOD = "ffmpeg"
FFMPEG_DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

_INSTALLED = False


def curl_export_url(slice_) -> str:
    """Build the historical Nimble direct-MP4 endpoint for one discovered slice."""
    return (
        f"http://{slice_.host}:8086/manage/dvr/export_mp4/"
        f"{slice_.app}/{slice_.stream}?start={int(round(slice_.start))}&end={int(round(slice_.end))}"
    )


def ffmpeg_no_audio_args(ffmpeg: str, url: str, output: Path, *, headers: str = "") -> list[str]:
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1"]
    if headers:
        args.extend(["-headers", headers])
    args.extend(["-i", url, "-c:v", "copy", "-an", str(output)])
    return args


def _cancelled(cancel_event) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _check_cancel(cancel_event) -> None:
    if _cancelled(cancel_event):
        raise OperationCancelled("Операция отменена пользователем")


def _fmt_mib(value: int | float) -> str:
    return f"{max(0.0, float(value)) / (1024 * 1024):.1f} МБ"


def _download_ffmpeg(self, progress: Callable[[str, str], None] | None = None, cancel_event=None) -> str:
    """Find or download FFmpeg into a writable per-user cache.

    This replaces the old apparently-frozen 300 second download: every received
    chunk updates the archive page, including real bytes and percentage when the
    server provides Content-Length.
    """
    _check_cancel(cancel_event)

    local_cached = self.tools_dir() / "ffmpeg.exe"
    for candidate in self._ffmpeg_candidates():
        if not (candidate.exists() and candidate.is_file()):
            continue
        if self._ffmpeg_usable(candidate):
            return str(candidate)
        # Only remove our own cached copy. Never touch a system/bundled binary.
        try:
            if candidate.resolve() == local_cached.resolve():
                candidate.unlink(missing_ok=True)
        except Exception:
            pass

    tools = self.tools_dir()
    try:
        tools.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise RuntimeError(f"Не удалось подготовить папку FFmpeg: нет доступа к {tools}") from exc

    target = tools / "ffmpeg.exe"
    temp_target = tools / "ffmpeg.exe.download"
    temp_target.unlink(missing_ok=True)

    if progress:
        progress("Подготавливаю FFmpeg", "FFmpeg не найден. Начинаю загрузку… · 0%")

    with tempfile.TemporaryDirectory(prefix="lv_ffmpeg_") as td:
        _check_cancel(cancel_event)
        zpath = Path(td) / "ffmpeg.zip"
        req = urllib.request.Request(FFMPEG_DOWNLOAD_URL, headers={"User-Agent": "LinkVideo.Helper/3.0"})
        try:
            with urllib.request.urlopen(req, timeout=60) as response, zpath.open("wb") as handle:
                try:
                    total = int(response.headers.get("Content-Length", 0) or 0)
                except Exception:
                    total = 0
                received = 0
                last_percent = -1
                last_report = 0.0
                while True:
                    _check_cancel(cancel_event)
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    now = time.monotonic()
                    percent = int(min(99, received * 100 / total)) if total > 0 else -1
                    if progress and (percent != last_percent or now - last_report >= 0.4):
                        if total > 0:
                            detail = f"Скачано {_fmt_mib(received)} из {_fmt_mib(total)} · {percent}%"
                        else:
                            detail = f"Скачано {_fmt_mib(received)}"
                        progress("Подготавливаю FFmpeg", detail)
                        last_percent = percent
                        last_report = now
        except OperationCancelled:
            raise
        except Exception as exc:
            raise RuntimeError(f"Не удалось скачать FFmpeg: {exc}") from exc

        if not zpath.exists() or zpath.stat().st_size < 10 * 1024 * 1024:
            raise RuntimeError("Архив FFmpeg скачан не полностью или имеет неверный размер")

        _check_cancel(cancel_event)
        if progress:
            progress("Подготавливаю FFmpeg", "Загрузка завершена · распаковываю компонент · 99%")
        try:
            with zipfile.ZipFile(zpath) as archive:
                member = next(
                    (
                        name
                        for name in archive.namelist()
                        if name.replace("\\", "/").lower().endswith("/bin/ffmpeg.exe")
                    ),
                    None,
                )
                if not member:
                    raise RuntimeError("В загруженном архиве не найден ffmpeg.exe")
                with archive.open(member) as src, temp_target.open("wb") as dst:
                    while True:
                        _check_cancel(cancel_event)
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
            temp_target.replace(target)
        finally:
            temp_target.unlink(missing_ok=True)

    if not target.exists() or not target.is_file():
        raise RuntimeError("FFmpeg был загружен, но итоговый файл не найден")
    if not self._ffmpeg_usable(target):
        target.unlink(missing_ok=True)
        raise RuntimeError("Загруженный FFmpeg повреждён или не запускается")
    if progress:
        progress("Подготавливаю FFmpeg", "FFmpeg готов · 100%")
    return str(target)


def _run_process_cancellable(
    cmd: list[str],
    *,
    cancel_event=None,
    timeout: float,
) -> tuple[int, str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
    )
    deadline = time.monotonic() + max(1.0, float(timeout))
    while True:
        _check_cancel(cancel_event)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            raise subprocess.TimeoutExpired(cmd, timeout)
        try:
            stdout, _ = proc.communicate(timeout=min(0.25, remaining))
            return int(proc.returncode or 0), stdout or ""
        except subprocess.TimeoutExpired:
            continue
        except OperationCancelled:
            raise
        finally:
            if _cancelled(cancel_event) and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass


def _run_ffmpeg_progress(
    cmd: list[str],
    *,
    base_done: float,
    item_duration: float,
    total: float,
    progress=None,
    cancel_event=None,
) -> tuple[int, str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
    )
    diagnostic: list[str] = []
    while True:
        if _cancelled(cancel_event):
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            raise OperationCancelled("Скачивание отменено пользователем")
        line = proc.stdout.readline() if proc.stdout else ""
        if line:
            value = line.strip()
            if value.startswith("out_time_ms="):
                try:
                    local = min(float(item_duration), float(value.split("=", 1)[1]) / 1_000_000.0)
                    done = min(float(total), float(base_done) + local)
                    if progress:
                        progress(
                            {
                                "type": "progress",
                                "value": int(min(99, done / max(1.0, total) * 100)),
                                "done": done,
                                "total": total,
                            }
                        )
                except Exception:
                    pass
            elif value and "=" not in value:
                diagnostic.append(value)
        if proc.poll() is not None:
            break
        if not line:
            time.sleep(0.03)
    return int(proc.wait()), "\n".join(diagnostic)


def _slices_for_direct_transport(self, discovery):
    slices = list(discovery.slices or [])
    if slices:
        return slices
    host = str(discovery.hls_fallback_host or discovery.camera.server or "").strip()
    if not host:
        return []
    candidates = self._stream_candidates(discovery.camera.stream_name, discovery.camera.raw)
    if not candidates:
        return []
    app, stream = candidates[0]
    from linkvideo_vpn_helper.services.archive_service import ArchiveSlice

    return [
        ArchiveSlice(
            host,
            app,
            stream,
            float(discovery.requested_start),
            float(discovery.requested_end),
            discovery.camera.signature,
            "player",
        )
    ]


def _concat_parts(self, ffmpeg: str, parts: list[Path], output: Path, *, cancel_event=None) -> None:
    if len(parts) == 1:
        shutil.copyfile(parts[0], output)
        return
    list_file = parts[0].parent / "concat.txt"
    list_file.write_text(
        "\n".join(
            "file '" + str(part).replace("\\", "/").replace("'", "'\\''") + "'"
            for part in parts
        ),
        encoding="utf-8",
    )
    timeout = max(120, min(7200, int(sum(max(1, p.stat().st_size) for p in parts) / (1024 * 1024)) + 180))
    code, text = self._run_cancellable(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(output),
        ],
        timeout,
        cancel_event,
    )
    if code != 0 or not output.exists() or output.stat().st_size <= 0:
        reason = (text or "").strip().splitlines()
        raise RuntimeError(
            "Скачанные части не удалось объединить в MP4"
            + ((": " + reason[-1]) if reason else "")
        )


def _download_no_audio(self, discovery, output: Path, progress=None, cancel_event=None):
    from linkvideo_vpn_helper.services.archive_service import ArchiveDownloadResult

    _check_cancel(cancel_event)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = self.ensure_ffmpeg(
        lambda a, b: progress and progress({"type": "stage", "title": a, "detail": b}),
        cancel_event,
    )
    total = max(1.0, float(discovery.covered_duration or discovery.requested_duration or 1.0))
    header_value = (
        "Origin: https://test-desktop-player-b2o.elct.ru\r\n"
        "Referer: https://test-desktop-player-b2o.elct.ru/\r\n"
        "User-Agent: LinkVideo.Helper/3.0\r\n"
    )

    if discovery.hls_fallback_url and (
        not discovery.slices or str(discovery.hls_fallback_method or "").startswith("player playlist")
    ):
        if progress:
            progress({"type": "stage", "title": "Скачиваю без звука", "detail": "Плейлист плеера · аудио отключено"})
        cmd = ffmpeg_no_audio_args(ffmpeg, str(discovery.hls_fallback_url), output, headers=header_value)
        code, text = _run_ffmpeg_progress(
            cmd,
            base_done=0.0,
            item_duration=total,
            total=total,
            progress=progress,
            cancel_event=cancel_event,
        )
        if code != 0 or not output.exists() or output.stat().st_size <= 0:
            output.unlink(missing_ok=True)
            reason = (text or "").strip().splitlines()
            raise RuntimeError(reason[-1] if reason else "FFmpeg не получил архив без аудио")
        if progress:
            progress({"type": "progress", "value": 100, "done": total, "total": total})
        return ArchiveDownloadResult(output, [], [], [], direct_download_duration=total)

    slices = _slices_for_direct_transport(self, discovery)
    if not slices:
        raise RuntimeError("Нет подтверждённых участков для скачивания без звука")

    tmp = Path(tempfile.mkdtemp(prefix="lv_archive_no_audio_"))
    parts: list[Path] = []
    downloaded = []
    failed = []
    errors: list[str] = []
    completed = 0.0
    try:
        for idx, sl in enumerate(slices, 1):
            _check_cancel(cancel_event)
            target = tmp / f"part_{idx:03d}.mp4"
            url = self.playlist_url(sl)
            if progress:
                progress(
                    {
                        "type": "stage",
                        "title": "Скачиваю без звука",
                        "detail": f"Участок {idx}/{len(slices)} · {sl.host}",
                    }
                )
            cmd = ffmpeg_no_audio_args(ffmpeg, url, target)
            code, text = _run_ffmpeg_progress(
                cmd,
                base_done=completed,
                item_duration=sl.duration,
                total=total,
                progress=progress,
                cancel_event=cancel_event,
            )
            if code == 0 and target.exists() and target.stat().st_size > 0:
                parts.append(target)
                downloaded.append(sl)
            else:
                failed.append(sl)
                reason = (text or "").strip().splitlines()
                errors.append(f"{sl.host}: {reason[-1] if reason else 'FFmpeg не получил участок'}")
                target.unlink(missing_ok=True)
            completed += sl.duration

        if not parts:
            raise RuntimeError(errors[0] if errors else "Ни один участок не удалось скачать без звука")
        if progress and len(parts) > 1:
            progress({"type": "stage", "title": "Объединяю участки", "detail": "Склеиваю части без аудио в один MP4…"})
        _concat_parts(self, ffmpeg, parts, output, cancel_event=cancel_event)
        if progress:
            progress({"type": "progress", "value": 100, "done": total, "total": total})
        return ArchiveDownloadResult(output, downloaded, failed, errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _curl_executable() -> str:
    found = shutil.which("curl.exe") or shutil.which("curl")
    if not found:
        raise RuntimeError("Curl не найден в Windows. Выберите FFmpeg или «Без звука».")
    return found


def _download_curl(self, discovery, output: Path, progress=None, cancel_event=None):
    from linkvideo_vpn_helper.services.archive_service import ArchiveDownloadResult

    _check_cancel(cancel_event)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    slices = _slices_for_direct_transport(self, discovery)
    if not slices:
        raise RuntimeError("Нет подтверждённых DVR-участков для Curl")

    curl = _curl_executable()
    total = max(1.0, float(sum(sl.duration for sl in slices) or discovery.requested_duration or 1.0))
    tmp = Path(tempfile.mkdtemp(prefix="lv_archive_curl_"))
    parts: list[Path] = []
    downloaded = []
    failed = []
    errors: list[str] = []
    completed = 0.0
    try:
        for idx, sl in enumerate(slices, 1):
            _check_cancel(cancel_event)
            target = tmp / f"part_{idx:03d}.mp4"
            url = curl_export_url(sl)
            if progress:
                progress(
                    {
                        "type": "stage",
                        "title": "Скачиваю через Curl",
                        "detail": f"Участок {idx}/{len(slices)} · {sl.host}",
                    }
                )
            cmd = [
                curl,
                "--fail",
                "--location",
                "--silent",
                "--show-error",
                "--connect-timeout",
                "12",
                "--retry",
                "2",
                "--retry-delay",
                "1",
                "--output",
                str(target),
                url,
            ]
            timeout = max(180.0, min(21600.0, float(sl.duration) * 4.0 + 180.0))
            try:
                code, text = _run_process_cancellable(cmd, cancel_event=cancel_event, timeout=timeout)
            except subprocess.TimeoutExpired:
                code, text = 28, "Curl не завершил загрузку за допустимое время"
            valid = code == 0 and target.exists() and target.stat().st_size > 0
            if valid:
                try:
                    head = target.open("rb").read(512).lower()
                    if b"<html" in head or b"404 not found" in head or b"not found" in head:
                        valid = False
                        text = "DVR вернул HTML/ошибку вместо MP4"
                except Exception:
                    pass
            if valid:
                parts.append(target)
                downloaded.append(sl)
            else:
                failed.append(sl)
                errors.append(f"{sl.host}: {(text or 'Curl не получил MP4').strip()[-300:]}")
                target.unlink(missing_ok=True)
            completed += sl.duration
            if progress:
                progress(
                    {
                        "type": "progress",
                        "value": int(min(95, completed / total * 95)),
                        "done": completed,
                        "total": total,
                    }
                )

        if not parts:
            raise RuntimeError(errors[0] if errors else "Curl не смог скачать ни один подтверждённый участок")

        if len(parts) == 1:
            shutil.copyfile(parts[0], output)
        else:
            # Curl remains the download transport. FFmpeg is needed only to
            # remux multiple DVR exports into one valid MP4 when the archive
            # moved between servers during the requested period.
            if progress:
                progress(
                    {
                        "type": "stage",
                        "title": "Объединяю Curl-части",
                        "detail": "Архив найден на нескольких DVR; подготавливаю FFmpeg только для склейки…",
                    }
                )
            ffmpeg = self.ensure_ffmpeg(
                lambda a, b: progress and progress({"type": "stage", "title": a, "detail": b}),
                cancel_event,
            )
            _concat_parts(self, ffmpeg, parts, output, cancel_event=cancel_event)

        if not output.exists() or output.stat().st_size <= 0:
            raise RuntimeError("Curl завершился, но итоговый MP4 не создан")
        if progress:
            progress({"type": "progress", "value": 100, "done": total, "total": total})
        return ArchiveDownloadResult(output, downloaded, failed, errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def install_archive_download_methods() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel
    from linkvideo_vpn_helper.services.archive_service import ArchiveService
    from linkvideo_vpn_helper.ui.pages.archive_download_page import ArchiveDownloadPage

    original_download = ArchiveService.download
    original_build = ArchiveDownloadPage._build
    original_page_download = ArchiveDownloadPage._download
    original_page_update = ArchiveDownloadPage._on_download_update
    original_page_cancel = ArchiveDownloadPage.cancel_current_action

    ArchiveService.ensure_ffmpeg = _download_ffmpeg

    def service_download(self, discovery, output, progress=None, cancel_event=None):
        method = str(getattr(self, "_lv_archive_download_method", DEFAULT_ARCHIVE_DOWNLOAD_METHOD) or DEFAULT_ARCHIVE_DOWNLOAD_METHOD)
        if method == "curl":
            return _download_curl(self, discovery, output, progress, cancel_event)
        if method == "ffmpeg_no_audio":
            return _download_no_audio(self, discovery, output, progress, cancel_event)
        return original_download(self, discovery, output, progress, cancel_event)

    ArchiveService.download = service_download

    def page_build(self):
        original_build(self)
        row_widget = self.btn_find.parentWidget()
        layout = row_widget.layout() if row_widget is not None else None
        if layout is None:
            return

        method_row = QHBoxLayout()
        method_row.setSpacing(10)
        method_label = QLabel("Метод")
        method_label.setMinimumWidth(110)
        self.archive_method_combo = QComboBox()
        for label, value in ARCHIVE_DOWNLOAD_METHODS:
            self.archive_method_combo.addItem(label, value)
        saved = str(self.settings.value("archive/download_method", DEFAULT_ARCHIVE_DOWNLOAD_METHOD, str) or DEFAULT_ARCHIVE_DOWNLOAD_METHOD)
        index = self.archive_method_combo.findData(saved)
        self.archive_method_combo.setCurrentIndex(index if index >= 0 else 0)
        self.archive_method_combo.setMinimumHeight(38)
        self.archive_method_combo.currentIndexChanged.connect(
            lambda *_: self.settings.setValue(
                "archive/download_method",
                str(self.archive_method_combo.currentData() or DEFAULT_ARCHIVE_DOWNLOAD_METHOD),
            )
        )
        method_row.addWidget(method_label)
        method_row.addWidget(self.archive_method_combo, 1)
        button_index = layout.indexOf(self.btn_find)
        if button_index < 0:
            layout.addLayout(method_row)
        else:
            layout.insertLayout(button_index, method_row)

    def page_download(self):
        combo = getattr(self, "archive_method_combo", None)
        method = str(combo.currentData() if combo is not None else DEFAULT_ARCHIVE_DOWNLOAD_METHOD)
        if method not in {value for _, value in ARCHIVE_DOWNLOAD_METHODS}:
            method = DEFAULT_ARCHIVE_DOWNLOAD_METHOD
        self.settings.setValue("archive/download_method", method)
        self.service._lv_archive_download_method = method
        if combo is not None:
            combo.setEnabled(False)
        try:
            return original_page_download(self)
        except Exception:
            if combo is not None:
                combo.setEnabled(True)
            raise

    def page_update(self, payload):
        if isinstance(payload, dict) and payload.get("type") == "stage":
            title = str(payload.get("title") or "")
            detail = str(payload.get("detail") or "")
            if title == "Подготавливаю FFmpeg":
                match = re.search(r"(?:^|\s)(\d{1,3})%(?:\s|$)", detail)
                if match:
                    value = max(0, min(100, int(match.group(1))))
                    self.task.busy(title, detail, value)
                    return
        result = original_page_update(self, payload)
        if isinstance(payload, dict) and payload.get("type") in {"done", "error"}:
            combo = getattr(self, "archive_method_combo", None)
            if combo is not None:
                combo.setEnabled(True)
        return result

    def page_cancel(self) -> bool:
        result = original_page_cancel(self)
        if result:
            combo = getattr(self, "archive_method_combo", None)
            if combo is not None:
                combo.setEnabled(True)
        return result

    ArchiveDownloadPage._build = page_build
    ArchiveDownloadPage._download = page_download
    ArchiveDownloadPage._on_download_update = page_update
    ArchiveDownloadPage.cancel_current_action = page_cancel
    _INSTALLED = True
