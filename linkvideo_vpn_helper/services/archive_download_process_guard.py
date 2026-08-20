from __future__ import annotations

"""Process-lifetime guard for archive transfer children.

Curl and FFmpeg are child processes. Cancellation, timeout or an unexpected
Python exception must never leave either process running after the Helper
operation has stopped. Network FFmpeg commands carry their own 30-second AVIO
stall timeout directly, without a global ``subprocess.Popen`` monkey patch.
"""

import os
import queue
import subprocess
import threading
import time

from linkvideo_vpn_helper.services.errors import OperationCancelled


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
    output_queue: queue.Queue[str | None] = queue.Queue()

    def _read_stdout() -> None:
        try:
            if proc.stdout is not None:
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    output_queue.put(line)
        finally:
            output_queue.put(None)

    reader = threading.Thread(target=_read_stdout, name="ffmpeg-progress-reader", daemon=True)
    reader.start()

    try:
        duration = max(0.0, float(item_duration))
    except (TypeError, ValueError, OverflowError):
        duration = 0.0
    timeout_seconds = max(600.0, min(86_400.0, duration * 5.0 + 600.0))
    deadline = time.monotonic() + timeout_seconds
    diagnostic: list[str] = []
    reader_done = False
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelled("Скачивание отменено пользователем")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(cmd, timeout_seconds)
            try:
                line = output_queue.get(timeout=min(0.1, remaining))
            except queue.Empty:
                line = ""
            if line is None:
                reader_done = True
                line = ""
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
            if proc.poll() is not None and reader_done and output_queue.empty():
                break
        return int(proc.wait(timeout=2)), "\n".join(diagnostic)
    finally:
        _stop_process(proc)
