from __future__ import annotations

import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from linkvideo_vpn_helper.services import archive_download_process_guard as guard
from linkvideo_vpn_helper.services.errors import OperationCancelled


class _FakeProcess:
    def __init__(self, *args, **kwargs):
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def communicate(self, timeout=None):
        raise AssertionError("communicate must not run after cancellation is already set")

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode or 0


class _BlockingStdout:
    def readline(self):
        threading.Event().wait(30)
        return ""


class _BlockingProcess(_FakeProcess):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stdout = _BlockingStdout()


def main() -> None:
    original = guard.subprocess.Popen
    created: list[_FakeProcess] = []

    def fake_popen(*args, **kwargs):
        proc = _FakeProcess(*args, **kwargs)
        created.append(proc)
        return proc

    event = threading.Event()
    event.set()
    guard.subprocess.Popen = fake_popen
    try:
        try:
            guard._run_process_cancellable(["curl.exe", "https://example.invalid"], cancel_event=event, timeout=30)
        except OperationCancelled:
            pass
        else:
            raise AssertionError("cancellation must raise OperationCancelled")
    finally:
        guard.subprocess.Popen = original

    assert len(created) == 1
    assert created[0].terminated or created[0].killed, "cancelled Curl child was left running"

    blocked: list[_BlockingProcess] = []

    def fake_blocking_popen(*args, **kwargs):
        proc = _BlockingProcess(*args, **kwargs)
        blocked.append(proc)
        return proc

    event = threading.Event()
    guard.subprocess.Popen = fake_blocking_popen
    setter = threading.Thread(target=lambda: (time.sleep(0.1), event.set()), daemon=True)
    setter.start()
    started = time.monotonic()
    try:
        try:
            guard._run_ffmpeg_progress(
                ["ffmpeg.exe", "-progress", "pipe:1"],
                base_done=0,
                item_duration=10,
                total=10,
                cancel_event=event,
            )
        except OperationCancelled:
            pass
        else:
            raise AssertionError("FFmpeg cancellation must raise OperationCancelled")
    finally:
        guard.subprocess.Popen = original
    assert time.monotonic() - started < 1.5, "blocked FFmpeg stdout hid cancellation"
    assert len(blocked) == 1
    assert blocked[0].terminated or blocked[0].killed, "cancelled FFmpeg child was left running"

    methods = (ROOT / "linkvideo_vpn_helper" / "services" / "archive_download_methods.py").read_text(
        encoding="utf-8"
    )
    guard_source = (ROOT / "linkvideo_vpn_helper" / "services" / "archive_download_process_guard.py").read_text(
        encoding="utf-8"
    )
    assert "archive_download_process_guard import" in methods
    assert "guarded_run" in methods
    assert "archive_download_methods._run_process_cancellable =" not in guard_source

    print("CORE TESTS 3.0.11 ARCHIVE PROCESS GUARD OK")


if __name__ == "__main__":
    main()
