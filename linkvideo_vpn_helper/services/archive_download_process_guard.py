from __future__ import annotations

"""Process-lifetime guard for archive Curl transfers.

The Curl transport runs as a child process.  Cancellation, timeout or an
unexpected Python exception must never leave curl.exe running after the Helper
operation has stopped.  Keep this guard separate from transfer semantics so the
three download methods remain easy to review.
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
        # Runs for cancellation, timeout and any unrelated exception.  A normal
        # completed process has poll()!=None, so this is a no-op on success.
        _stop_process(proc)


def install_archive_download_process_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from linkvideo_vpn_helper.services import archive_download_methods

    archive_download_methods._run_process_cancellable = _run_process_cancellable
    _INSTALLED = True
