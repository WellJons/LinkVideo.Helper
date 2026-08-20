from __future__ import annotations

"""Process-lifetime guard for archive transfer children.

Curl and FFmpeg are child processes. Cancellation, timeout or an unexpected
Python exception must never leave either process running after the Helper
operation has stopped. Network FFmpeg inputs additionally receive the central
30-second AVIO stall timeout from ``archive_process_hardening``.
"""

import os
import subprocess
import time

from linkvideo_vpn_helper.services.errors import OperationCancelled


_INSTALLED = False


def _stop_process(proc) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=2)
        return
    except Exception:
        pass
    try:
        proc.kill()
        proc.wait(timeout=2)
    except Exception:
        pass


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
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelled("Операция отменена пользователем")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(cmd, timeout)
            try:
                stdout, _ = proc.communicate(timeout=min(0.25, remaining))
                return int(proc.returncode or 0), stdout or ""
            except subprocess.TimeoutExpired:
                continue
    finally:
        # Runs for cancellation, timeout and any unrelated exception. A normal
        # completed process has poll()!=None, so this is a no-op on success.
        _stop_process(proc)


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
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
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
                    except (TypeError, ValueError, OverflowError):
                        # A malformed FFmpeg progress line is not fatal. The
                        # process exit code and output file remain authoritative.
                        pass
                elif value and "=" not in value:
                    diagnostic.append(value)
            if proc.poll() is not None:
                break
            if not line:
                time.sleep(0.03)
        return int(proc.wait()), "\n".join(diagnostic)
    finally:
        _stop_process(proc)


def install_archive_download_process_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from linkvideo_vpn_helper.services import archive_download_methods

    archive_download_methods._run_process_cancellable = _run_process_cancellable
    archive_download_methods._run_ffmpeg_progress = _run_ffmpeg_progress
    _INSTALLED = True
