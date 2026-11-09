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

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.parse
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
_FFMPEG_ALLOWED_HOSTS = frozenset({"gyan.dev", "www.gyan.dev"})
_FFMPEG_MIN_ZIP_SIZE = 10 * 1024 * 1024
_FFMPEG_MAX_ZIP_SIZE = 750 * 1024 * 1024
_FFMPEG_MIN_EXE_SIZE = 10 * 1024 * 1024
_FFMPEG_MAX_EXE_SIZE = 500 * 1024 * 1024

_INSTALLED = False


def curl_export_url(slice_) -> str:
    """Build the historical Nimble direct-MP4 endpoint for one discovered slice."""
    return (
        f"http://{slice_.host}:8086/manage/dvr/export_mp4/"
        f"{slice_.app}/{slice_.stream}?start={int(round(slice_.start))}&end={int(round(slice_.end))}"
    )


def ffmpeg_no_audio_args(ffmpeg: str, url: str, output: Path, *, headers: str = "") -> list[str]:
    args = [ffmpeg, "-y", "-nostdin", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1"]
    if headers:
        args.extend(["-headers", headers])
    if str(url).lower().startswith(("http://", "https://")):
        args.extend(["-rw_timeout", "30000000"])
    args.extend(["-i", url, "-c:v", "copy", "-an", str(output)])
    return args


def ffmpeg_audio_args(ffmpeg: str, url: str, output: Path, *, headers: str = "") -> list[str]:
    args = [ffmpeg, "-y", "-nostdin", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1"]
    if headers:
        args.extend(["-headers", headers])
    if str(url).lower().startswith(("http://", "https://")):
        args.extend(["-rw_timeout", "30000000"])
    args.extend(["-i", url, "-c", "copy", str(output)])
    return args


_PLAYER_HEADERS = (
    "Origin: https://test-desktop-player-b2o.elct.ru\r\n"
    "Referer: https://test-desktop-player-b2o.elct.ru/\r\n"
    "User-Agent: LinkVideo.Helper/3.0\r\n"
)

# Operator-editable command. Input and output are supplied by Helper, never
# by shell interpolation, so paths with spaces and signed URLs remain intact.
MANUAL_FFMPEG_TEMPLATE = (
    'ffmpeg -y -nostdin -hide_banner -loglevel error -progress pipe:1 '
    '-rw_timeout 30000000 -i "{input}" -c copy "{output}"'
)
MANUAL_FFMPEG_NO_AUDIO_TEMPLATE = (
    'ffmpeg -y -nostdin -hide_banner -loglevel error -progress pipe:1 '
    '-rw_timeout 30000000 -i "{input}" -c:v copy -an "{output}"'
)


def _split_manual_command(text: str) -> list[str]:
    """Parse text as argv, not shell syntax (including native Windows quotes)."""
    if os.name != "nt":
        return shlex.split(text)
    import ctypes
    from ctypes import wintypes
    shell = ctypes.windll.shell32
    shell.CommandLineToArgvW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
    shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    count = ctypes.c_int(0)
    ptr = shell.CommandLineToArgvW(text, ctypes.byref(count))
    if not ptr:
        raise ValueError("Не удалось разобрать команду FFmpeg.")
    try:
        return [ptr[i] for i in range(count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(ptr, ctypes.c_void_p))


def validate_manual_ffmpeg_command(text: str) -> list[str]:
    """Keep the executable, input and staged output under Helper control."""
    if not isinstance(text, str) or len(text) > 4000:
        raise ValueError("Команда FFmpeg слишком длинная.")
    try:
        args = _split_manual_command(text.strip())
    except (ValueError, OSError) as exc:
        raise ValueError("Неверный формат кавычек команды FFmpeg.") from exc
    if not args or Path(args[0]).name.lower() not in {"ffmpeg", "ffmpeg.exe"}:
        raise ValueError("Команда должна начинаться с ffmpeg.")
    if args.count("-i") != 1 or args[args.index("-i") + 1:][:1] != ["{input}"]:
        raise ValueError('Укажите один источник: -i "{input}".')
    if args.count("{input}") != 1 or args.count("{output}") != 1 or args[-1] != "{output}":
        raise ValueError('Используйте "{input}" и "{output}" как отдельные аргументы.')
    if any(("{input}" in x and x != "{input}") or
           ("{output}" in x and x != "{output}") for x in args):
        raise ValueError("Переменные {input} и {output} нельзя объединять с другим текстом.")
    return args


def ffmpeg_manual_args(ffmpeg: str, url: str, output: Path, template: str,
                       *, headers: str = _PLAYER_HEADERS) -> list[str]:
    args = validate_manual_ffmpeg_command(template)
    args = [str(ffmpeg)] + args[1:]
    args = [str(url) if value == "{input}" else str(output) if value == "{output}"
            else value for value in args]
    input_index = args.index("-i")
    if headers and str(url).lower().startswith(("http://", "https://")) and "-headers" not in args:
        args[input_index:input_index] = ["-headers", headers]
        input_index += 2
    if "-progress" not in args:
        args[input_index:input_index] = ["-progress", "pipe:1"]
    return args


def _ffmpeg_transport_args(self, ffmpeg: str, url: str, output: Path,
                           *, no_audio: bool = False,
                           transcode_audio: bool = False) -> list[str]:
    manual = str(getattr(self, "_lv_archive_manual_command", "") or "").strip()
    if manual:
        return ffmpeg_manual_args(ffmpeg, url, output, manual)
    builder = ffmpeg_no_audio_args if no_audio else ffmpeg_audio_args
    args = builder(ffmpeg, url, output, headers=_PLAYER_HEADERS)
    if transcode_audio and not no_audio:
        # Some DVR cameras use G.711/PCM or other audio that an MP4
        # container cannot stream-copy. On retry preserve video copy and
        # convert only audio to AAC instead of silently dropping sound.
        index = args.index("-c")
        args[index:index + 2] = ["-c:v", "copy", "-c:a", "aac", "-b:a", "128k"]
    return args


def _safe_hls_error(text: str) -> str:
    """FFmpeg may echo signed playlist URLs; never forward tokens to the UI."""
    line = next((item.strip() for item in reversed(str(text or "").splitlines())
                 if item.strip()), "FFmpeg не смог обработать HLS")
    line = re.sub(r'https?://[^\s"<>]+', "[HLS-URL]", line, flags=re.I)
    line = re.sub(r"(?i)(wmsAuthSign|token|signature|auth)=([^&\s]+)",
                  r"\1=[скрыто]", line)
    return line[:350]


def _ffmpeg_hls_attempts(cmd_factory, target: Path, *,
                         base_done: float, item_duration: float, total: float,
                         progress=None, cancel_event=None, retry_count: int = 2):
    """Retry transient HLS transport failures without keeping corrupt output."""
    last_text = ""
    for attempt in range(retry_count):
        _check_cancel(cancel_event)
        target.unlink(missing_ok=True)
        cmd = cmd_factory(attempt)
        try:
            code, text = _run_ffmpeg_progress(
                cmd, base_done=base_done, item_duration=item_duration, total=total,
                progress=progress, cancel_event=cancel_event)
        except subprocess.TimeoutExpired:
            # The process guard already terminated FFmpeg. A stale HLS socket
            # should trigger the same bounded retry as HTTP 404/403.
            code, text = 124, "Превышено время ожидания HLS-сервера"
        if code == 0 and _is_probable_mp4(target):
            return True, ""
        last_text = _safe_hls_error(text)
        if attempt + 1 < retry_count and progress:
            progress({"type": "stage", "title": "Повторяю получение HLS",
                      "detail": f"Попытка {attempt + 2}/{retry_count} · " + last_text})
    target.unlink(missing_ok=True)
    return False, last_text


def _cancelled(cancel_event) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _check_cancel(cancel_event) -> None:
    if _cancelled(cancel_event):
        raise OperationCancelled("Операция отменена пользователем")


def _fmt_mib(value: int | float) -> str:
    return f"{max(0.0, float(value)) / (1024 * 1024):.1f} МБ"


def _create_staged_output(output: Path) -> Path:
    """Reserve a same-volume temporary MP4 for an atomic final commit."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{output.stem}.",
        suffix=".download.mp4",
        dir=str(output.parent),
    )
    os.close(descriptor)
    return Path(raw_path)


def _is_probable_mp4(path: Path) -> bool:
    """Reject HTML/JSON/error bodies that were saved with an .mp4 suffix.

    FFmpeg and Nimble ``export_mp4`` both produce ISO Base Media files whose
    first top-level box is ``ftyp`` (or ``styp`` for a fragmented stream).  A
    non-empty file alone is not proof of a successful archive export: several
    deployed DVR endpoints return a textual error with HTTP 200.
    """
    try:
        size = path.stat().st_size
        if size < 12:
            return False
        with path.open("rb") as handle:
            header = handle.read(16)
        if len(header) < 8 or header[4:8] not in {b"ftyp", b"styp"}:
            return False
        box_size = int.from_bytes(header[:4], "big", signed=False)
        if box_size == 0:
            return True
        if box_size == 1:
            return len(header) >= 16 and 16 <= int.from_bytes(header[8:16], "big", signed=False) <= size
        return 8 <= box_size <= size
    except OSError:
        return False


def _commit_staged_output(staged: Path, output: Path) -> None:
    if not _is_probable_mp4(staged):
        raise RuntimeError("Скачивание завершилось без корректного итогового MP4")
    staged.replace(output)


def _trusted_ffmpeg_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(str(url).strip())
    if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() not in _FFMPEG_ALLOWED_HOSTS:
        raise RuntimeError("Сервер FFmpeg вернул недоверенный адрес загрузки")
    return urllib.parse.urlunsplit(parsed)


def _sha256_file(path: Path, cancel_event=None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            _check_cancel(cancel_event)
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _download_expected_sha256(resolved_zip_url: str, cancel_event=None) -> str:
    parsed_zip = urllib.parse.urlsplit(_trusted_ffmpeg_url(resolved_zip_url))
    checksum_url = urllib.parse.urlunsplit(
        (parsed_zip.scheme, parsed_zip.netloc, parsed_zip.path + ".sha256", "", "")
    )
    request = urllib.request.Request(checksum_url, headers={"User-Agent": "LinkVideo.Helper/3.0"})
    _check_cancel(cancel_event)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            final_url = _trusted_ffmpeg_url(response.geturl())
            if not urllib.parse.urlsplit(final_url).path.endswith(".sha256"):
                raise RuntimeError("сервер вернул неверный файл контрольной суммы")
            payload = response.read(4097)
    except OperationCancelled:
        raise
    except Exception as exc:
        raise RuntimeError(f"Не удалось получить SHA-256 FFmpeg: {exc}") from exc
    if len(payload) > 4096:
        raise RuntimeError("Файл SHA-256 FFmpeg имеет неверный размер")
    try:
        token = payload.decode("ascii", errors="strict").strip().split(maxsplit=1)[0].lower()
    except (UnicodeDecodeError, IndexError) as exc:
        raise RuntimeError("Файл SHA-256 FFmpeg имеет неверный формат") from exc
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise RuntimeError("Файл SHA-256 FFmpeg имеет неверный формат")
    return token


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
        resolved_zip_url = ""
        try:
            with urllib.request.urlopen(req, timeout=60) as response, zpath.open("wb") as handle:
                resolved_zip_url = _trusted_ffmpeg_url(response.geturl())
                try:
                    total = int(response.headers.get("Content-Length", 0) or 0)
                except Exception:
                    total = 0
                if total > _FFMPEG_MAX_ZIP_SIZE:
                    raise RuntimeError("Архив FFmpeg имеет недопустимый размер")
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
                    if received > _FFMPEG_MAX_ZIP_SIZE:
                        raise RuntimeError("Архив FFmpeg превысил допустимый размер")
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

        if not zpath.exists() or zpath.stat().st_size < _FFMPEG_MIN_ZIP_SIZE:
            raise RuntimeError("Архив FFmpeg скачан не полностью или имеет неверный размер")

        _check_cancel(cancel_event)
        if progress:
            progress("Подготавливаю FFmpeg", "Загрузка завершена · проверяю SHA-256 · 99%")
        expected_hash = _download_expected_sha256(resolved_zip_url, cancel_event)
        actual_hash = _sha256_file(zpath, cancel_event)
        if actual_hash != expected_hash:
            raise RuntimeError("Проверка SHA-256 FFmpeg не пройдена: архив повреждён или подменён")

        _check_cancel(cancel_event)
        if progress:
            progress("Подготавливаю FFmpeg", "SHA-256 подтверждён · распаковываю компонент · 99%")
        try:
            with zipfile.ZipFile(zpath) as archive:
                member = next(
                    (
                        info
                        for info in archive.infolist()
                        if info.filename.replace("\\", "/").lower().endswith("/bin/ffmpeg.exe")
                    ),
                    None,
                )
                if not member:
                    raise RuntimeError("В загруженном архиве не найден ffmpeg.exe")
                if not (_FFMPEG_MIN_EXE_SIZE <= member.file_size <= _FFMPEG_MAX_EXE_SIZE):
                    raise RuntimeError("Файл ffmpeg.exe в архиве имеет неверный размер")
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
    from linkvideo_vpn_helper.services.archive_download_process_guard import (
        _run_process_cancellable as guarded_run,
    )

    return guarded_run(cmd, cancel_event=cancel_event, timeout=timeout)


def _run_ffmpeg_progress(
    cmd: list[str],
    *,
    base_done: float,
    item_duration: float,
    total: float,
    progress=None,
    cancel_event=None,
) -> tuple[int, str]:
    from linkvideo_vpn_helper.services.archive_download_process_guard import (
        _run_ffmpeg_progress as guarded_run,
    )

    return guarded_run(
        cmd,
        base_done=base_done,
        item_duration=item_duration,
        total=total,
        progress=progress,
        cancel_event=cancel_event,
    )


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
        with parts[0].open("rb") as source, output.open("wb") as target:
            while True:
                _check_cancel(cancel_event)
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                target.write(chunk)
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
    code, text = _run_process_cancellable(
        [
            ffmpeg,
            "-y",
            "-nostdin",
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
        timeout=timeout,
        cancel_event=cancel_event,
    )
    if code != 0 or not output.exists() or output.stat().st_size <= 0:
        reason = (text or "").strip().splitlines()
        raise RuntimeError(
            "Скачанные части не удалось объединить в MP4"
            + ((": " + reason[-1]) if reason else "")
        )


def _download_with_ffmpeg_audio(self, discovery, output: Path, progress=None, cancel_event=None):
    from linkvideo_vpn_helper.services.archive_service import ArchiveDownloadResult

    _check_cancel(cancel_event)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = self.ensure_ffmpeg(
        lambda a, b: progress and progress({"type": "stage", "title": a, "detail": b}),
        cancel_event,
    )
    total = max(1.0, float(discovery.covered_duration or discovery.requested_duration or 1.0))
    # Do not create anything in the user's destination directory until FFmpeg
    # has been found/downloaded and all local preconditions are ready.  A failed
    # first-use FFmpeg setup must not leave an empty .download.mp4 behind.
    staged_output = _create_staged_output(output)

    try:
        if discovery.hls_fallback_url and (
            not discovery.slices or str(discovery.hls_fallback_method or "").startswith("player playlist")
        ):
            if progress:
                progress({"type": "stage", "title": "Скачиваю через FFmpeg", "detail": "Плейлист плеера · со звуком"})
            def command_for_attempt(attempt: int):
                url = str(discovery.hls_fallback_url)
                if attempt:
                    # Long-running searches sometimes leave an expiring signed
                    # HLS URL. Re-read the documented player playlist once.
                    try:
                        fresh, _ = self._probe_hls_host(
                            discovery.hls_fallback_host or discovery.camera.server,
                            discovery.camera, discovery.requested_start,
                            discovery.requested_end, 8)
                        if fresh and fresh.get("url"):
                            url = str(fresh["url"])
                    except Exception:
                        # Keep the original signed URL as a bounded fallback;
                        # never expose it in diagnostics or event logs.
                        url = str(discovery.hls_fallback_url)
                return _ffmpeg_transport_args(
                    self, ffmpeg, url, staged_output, transcode_audio=bool(attempt))

            success, detail = _ffmpeg_hls_attempts(
                command_for_attempt, staged_output,
                base_done=0.0, item_duration=total, total=total,
                progress=progress, cancel_event=cancel_event)
            if not success:
                raise RuntimeError(detail + ". Попробуйте метод «Без звука».")
            _commit_staged_output(staged_output, output)
            if progress:
                progress({"type": "progress", "value": 100, "done": total, "total": total})
            return ArchiveDownloadResult(output, [], [], [], direct_download_duration=total)

        slices = _slices_for_direct_transport(self, discovery)
        if not slices:
            raise RuntimeError("Нет подтверждённых участков для скачивания через FFmpeg")

        tmp = Path(tempfile.mkdtemp(prefix="lv_archive_ffmpeg_"))
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
                            "title": "Скачиваю через FFmpeg",
                            "detail": f"Участок {idx}/{len(slices)} · {sl.host}",
                        }
                    )
                success, detail = _ffmpeg_hls_attempts(
                    lambda attempt: _ffmpeg_transport_args(
                        self, ffmpeg, url, target, transcode_audio=bool(attempt)),
                    target, base_done=completed, item_duration=sl.duration,
                    total=total, progress=progress, cancel_event=cancel_event,
                )
                if success:
                    parts.append(target)
                    downloaded.append(sl)
                else:
                    failed.append(sl)
                    errors.append(f"{sl.host}: {detail or 'FFmpeg не получил участок'}")
                    target.unlink(missing_ok=True)
                completed += sl.duration

            if not parts:
                detail = errors[0] if errors else "Ни один участок не удалось скачать через FFmpeg"
                raise RuntimeError(detail + ". Попробуйте метод «Без звука».")
            if progress and len(parts) > 1:
                progress({"type": "stage", "title": "Объединяю участки", "detail": "Склеиваю FFmpeg-части в один MP4…"})
            _concat_parts(self, ffmpeg, parts, staged_output, cancel_event=cancel_event)
            _commit_staged_output(staged_output, output)
            if progress:
                progress({"type": "progress", "value": 100, "done": total, "total": total})
            return ArchiveDownloadResult(output, downloaded, failed, errors)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    finally:
        staged_output.unlink(missing_ok=True)


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
    staged_output = _create_staged_output(output)

    try:
        if discovery.hls_fallback_url and (
            not discovery.slices or str(discovery.hls_fallback_method or "").startswith("player playlist")
        ):
            if progress:
                progress({"type": "stage", "title": "Скачиваю без звука", "detail": "Плейлист плеера · аудио отключено"})
            def command_for_attempt(attempt: int):
                url = str(discovery.hls_fallback_url)
                if attempt:
                    try:
                        fresh, _ = self._probe_hls_host(
                            discovery.hls_fallback_host or discovery.camera.server,
                            discovery.camera, discovery.requested_start,
                            discovery.requested_end, 8)
                        if fresh and fresh.get("url"):
                            url = str(fresh["url"])
                    except Exception:
                        url = str(discovery.hls_fallback_url)
                return _ffmpeg_transport_args(
                    self, ffmpeg, url, staged_output, no_audio=True)

            success, detail = _ffmpeg_hls_attempts(
                command_for_attempt, staged_output,
                base_done=0.0, item_duration=total, total=total,
                progress=progress, cancel_event=cancel_event)
            if not success:
                raise RuntimeError(detail or "FFmpeg не получил архив без аудио")
            _commit_staged_output(staged_output, output)
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
                success, detail = _ffmpeg_hls_attempts(
                    lambda _attempt: _ffmpeg_transport_args(
                        self, ffmpeg, url, target, no_audio=True),
                    target, base_done=completed, item_duration=sl.duration,
                    total=total, progress=progress, cancel_event=cancel_event,
                )
                if success:
                    parts.append(target)
                    downloaded.append(sl)
                else:
                    failed.append(sl)
                    errors.append(f"{sl.host}: {detail or 'FFmpeg не получил участок'}")
                    target.unlink(missing_ok=True)
                completed += sl.duration

            if not parts:
                raise RuntimeError(errors[0] if errors else "Ни один участок не удалось скачать без звука")
            if progress and len(parts) > 1:
                progress({"type": "stage", "title": "Объединяю участки", "detail": "Склеиваю части без аудио в один MP4…"})
            _concat_parts(self, ffmpeg, parts, staged_output, cancel_event=cancel_event)
            _commit_staged_output(staged_output, output)
            if progress:
                progress({"type": "progress", "value": 100, "done": total, "total": total})
            return ArchiveDownloadResult(output, downloaded, failed, errors)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    finally:
        staged_output.unlink(missing_ok=True)


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
    try:
        staged_output = _create_staged_output(output)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
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
            valid = code == 0 and _is_probable_mp4(target)
            if code == 0 and not valid:
                text = "DVR вернул ответ, который не является корректным MP4"
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
            _concat_parts(self, "", parts, staged_output, cancel_event=cancel_event)
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
            _concat_parts(self, ffmpeg, parts, staged_output, cancel_event=cancel_event)

        if not staged_output.exists() or staged_output.stat().st_size <= 0:
            raise RuntimeError("Curl завершился, но итоговый MP4 не создан")
        _commit_staged_output(staged_output, output)
        if progress:
            progress({"type": "progress", "value": 100, "done": total, "total": total})
        return ArchiveDownloadResult(output, downloaded, failed, errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        staged_output.unlink(missing_ok=True)


def install_archive_download_methods() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (
        QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox,
        QPlainTextEdit, QPushButton, QVBoxLayout,
    )
    from linkvideo_vpn_helper.ui.pages.archive_download_page import ArchiveDownloadPage

    original_build = ArchiveDownloadPage._build
    original_page_download = ArchiveDownloadPage._download
    original_page_update = ArchiveDownloadPage._on_download_update
    original_page_cancel = ArchiveDownloadPage.cancel_current_action

    def _open_manual_ffmpeg_dialog(page):
        from PySide6.QtGui import QFont
        from linkvideo_vpn_helper.theme import colors

        dialog = QDialog(page)
        dialog.setProperty("helperModal", True)
        dialog.setWindowTitle("Настройка FFmpeg · HLS")
        dialog.resize(880, 440)
        dialog.setMinimumSize(680, 390)
        content = QVBoxLayout(dialog)
        content.setContentsMargins(22, 20, 22, 20)
        content.setSpacing(12)

        heading = QLabel("Ручная команда FFmpeg")
        heading.setObjectName("SectionTitle")
        content.addWidget(heading)

        explanation = QLabel(
            "Задайте параметры обработки HLS-архива. "
            "Адрес плейлиста и путь сохранения подставляются автоматически — "
            "вставлять реальную ссылку или токен не требуется."
        )
        explanation.setObjectName("Muted")
        explanation.setWordWrap(True)
        content.addWidget(explanation)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        preset_title = QLabel("БЫСТРЫЕ НАСТРОЙКИ")
        preset_title.setObjectName("TinyMuted")
        preset_row.addWidget(preset_title)
        preset_row.addStretch(1)
        with_audio = QPushButton("С сохранением звука")
        no_audio = QPushButton("Без звука")
        with_audio.setProperty("role", "soft")
        no_audio.setProperty("role", "soft")
        preset_row.addWidget(with_audio)
        preset_row.addWidget(no_audio)
        content.addLayout(preset_row)

        code_label = QLabel("Команда запуска")
        code_label.setObjectName("CardTitle")
        content.addWidget(code_label)

        edit = QPlainTextEdit(dialog)
        edit.setObjectName("ArchiveManualFFmpegEditor")
        edit.setFont(QFont("Consolas", 10))
        edit.setMinimumHeight(106)
        edit.setMaximumHeight(156)
        edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        palette = colors(str(page.settings.value(
            "ui/theme_v2", "linkvideo_2026", str) or "linkvideo_2026"))
        edit.setStyleSheet(
            "QPlainTextEdit {"
            f"background: {palette['panel']}; color: {palette['text']};"
            f"border: 1px solid {palette['border']}; border-radius: 10px;"
            "padding: 12px; selection-background-color: "
            f"{palette['accent']};"
            "}"
        )
        method = str(page.archive_method_combo.currentData() or "ffmpeg")
        default = (MANUAL_FFMPEG_NO_AUDIO_TEMPLATE if method == "ffmpeg_no_audio"
                   else MANUAL_FFMPEG_TEMPLATE)
        saved = str(page.settings.value("archive/manual_ffmpeg_template", default, str)
                    or default)
        edit.setPlainText(str(getattr(page, "_lv_archive_manual_command", "")
                              or saved))
        content.addWidget(edit, 1)
        with_audio.clicked.connect(lambda: edit.setPlainText(MANUAL_FFMPEG_TEMPLATE))
        no_audio.clicked.connect(lambda: edit.setPlainText(MANUAL_FFMPEG_NO_AUDIO_TEMPLATE))

        placeholders = QLabel(
            '{input} — ссылка на HLS-плейлист   ·   {output} — временный MP4'
        )
        placeholders.setObjectName("TinyMuted")
        content.addWidget(placeholders)

        status = QLabel()
        status.setObjectName("TinyMuted")
        status.setWordWrap(True)
        status.setMinimumHeight(22)
        content.addWidget(status)

        note = QLabel(
            "Команда запускается напрямую, без оболочки Windows. HTTP-заголовки "
            "плеера добавляются автоматически. Секретные ссылки не сохраняются."
        )
        note.setObjectName("TinyMuted")
        note.setWordWrap(True)
        content.addWidget(note)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        reset = QPushButton("Сбросить")
        cancel = QPushButton("Отмена")
        save = QPushButton("Сохранить")
        run = QPushButton("Сохранить и скачать")
        run.setProperty("role", "primary")
        actions.addWidget(reset)
        actions.addStretch(1)
        actions.addWidget(cancel)
        actions.addWidget(save)
        actions.addWidget(run)
        content.addLayout(actions)

        def update_validation():
            try:
                validate_manual_ffmpeg_command(edit.toPlainText().strip())
            except ValueError as exc:
                status.setText("⚠ " + str(exc))
                status.setStyleSheet(f"color: {palette['danger']};")
                save.setEnabled(False)
                run.setEnabled(False)
                return
            status.setText("✓ Команда корректна, можно сохранить.")
            status.setStyleSheet(f"color: {palette['success']};")
            save.setEnabled(True)
            downloadable = bool(
                page.discovery and page.discovery.has_downloadable_archive)
            run.setEnabled(downloadable and getattr(page, "_cancel_event", None) is None)
            run.setToolTip(
                "Сначала найдите архив за нужный период" if not downloadable
                else "Начать загрузку с выбранной командой")

        edit.textChanged.connect(update_validation)
        reset.clicked.connect(lambda: edit.setPlainText(default))
        cancel.clicked.connect(dialog.reject)

        def apply_command(download: bool = False):
            try:
                template = edit.toPlainText().strip()
                validate_manual_ffmpeg_command(template)
                if download and not (page.discovery and page.discovery.has_downloadable_archive):
                    raise ValueError("Сначала найдите архив за выбранный период.")
                if getattr(page, "_cancel_event", None) is not None:
                    raise ValueError("Дождитесь завершения текущей операции.")
            except ValueError as exc:
                status.setText("⚠ " + str(exc))
                status.setStyleSheet(f"color: {palette['danger']};")
                return
            page.settings.setValue("archive/manual_ffmpeg_template", template)
            # Method selection clears old custom state, then restores this one.
            if page.archive_method_combo.currentData() != "ffmpeg":
                index = page.archive_method_combo.findData("ffmpeg")
                page.archive_method_combo.setCurrentIndex(index)
            page._lv_archive_manual_command = template
            page.service._lv_archive_manual_command = template
            page.archive_command_button.setText("✎ Команда FFmpeg · изменена")
            dialog.accept()
            if download:
                QTimer.singleShot(0, page._download)

        save.clicked.connect(lambda: apply_command(False))
        run.clicked.connect(lambda: apply_command(True))
        update_validation()
        dialog.exec()

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
        def update_method(*_):
            self.settings.setValue(
                "archive/download_method",
                str(self.archive_method_combo.currentData() or DEFAULT_ARCHIVE_DOWNLOAD_METHOD),
            )
            self._lv_archive_manual_command = ""
            self.service._lv_archive_manual_command = ""
            button = getattr(self, "archive_command_button", None)
            if button is not None:
                button.setText("✎ Команда FFmpeg…")

        self.archive_method_combo.currentIndexChanged.connect(update_method)
        method_row.addWidget(method_label)
        method_row.addWidget(self.archive_method_combo, 1)
        button_index = layout.indexOf(self.btn_find)
        if button_index < 0:
            layout.addLayout(method_row)
        else:
            layout.insertLayout(button_index, method_row)

        self._lv_archive_manual_command = ""
        self.service._lv_archive_manual_command = ""
        self.archive_command_button = QPushButton("✎ Команда FFmpeg…")
        self.archive_command_button.setObjectName("ArchiveManualFFmpegButton")
        self.archive_command_button.setProperty("role", "soft")
        self.archive_command_button.setMinimumHeight(40)
        self.archive_command_button.setToolTip(
            "Редактировать аргументы FFmpeg и запустить вручную на найденном HLS.")
        self.archive_command_button.clicked.connect(
            lambda: _open_manual_ffmpeg_dialog(self))
        summary_layout = self.summary.layout()
        inserted = False
        for row in range(summary_layout.count()):
            sublayout = summary_layout.itemAt(row).layout()
            if sublayout is not None and sublayout.indexOf(self.btn_download) >= 0:
                sublayout.insertWidget(sublayout.indexOf(self.btn_download),
                                       self.archive_command_button)
                inserted = True
                break
        if not inserted:
            summary_layout.addWidget(self.archive_command_button)

    def page_download(self):
        combo = getattr(self, "archive_method_combo", None)
        method = str(combo.currentData() if combo is not None else DEFAULT_ARCHIVE_DOWNLOAD_METHOD)
        if method not in {value for _, value in ARCHIVE_DOWNLOAD_METHODS}:
            method = DEFAULT_ARCHIVE_DOWNLOAD_METHOD
        self.settings.setValue("archive/download_method", method)
        self.service._lv_archive_download_method = method
        self.service._lv_archive_manual_command = str(
            getattr(self, "_lv_archive_manual_command", "") or "")
        if self.service._lv_archive_manual_command:
            # The operator explicitly opted into an editable FFmpeg command,
            # not the Curl exporter.
            self.service._lv_archive_download_method = "ffmpeg"
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
