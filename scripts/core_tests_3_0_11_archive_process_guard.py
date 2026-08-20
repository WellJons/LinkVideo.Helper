from __future__ import annotations

import threading
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

    app = (ROOT / "linkvideo_vpn_helper" / "app.py").read_text(encoding="utf-8")
    assert "install_archive_download_process_guard()" in app
    assert app.index("install_archive_download_methods()") < app.index("install_archive_download_process_guard()")
    assert app.index("install_archive_download_process_guard()") < app.index("install_archive_download_ux()")

    print("CORE TESTS 3.0.11 ARCHIVE PROCESS GUARD OK")


if __name__ == "__main__":
    main()
