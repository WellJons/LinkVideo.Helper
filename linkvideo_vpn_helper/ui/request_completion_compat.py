from __future__ import annotations

"""Make interactive VPN progress follow real request completion.

There is deliberately no wall-clock search deadline here.  Every RouterOS API
connection already has its own socket timeout, so a multi-server search finishes
when every started RouterOS request has either returned or raised a real network
/API error.  The short Queue timeout below is used only to observe an explicit
Esc cancellation; it never turns a pending server into a synthetic timeout.
"""

import queue
import threading
from typing import Callable, Iterable

from linkvideo_vpn_helper.services.errors import classify_exception
from linkvideo_vpn_helper.services.search_service import FastSearchService
from linkvideo_vpn_helper.services.search_service_core import ServerSearchError
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials
from linkvideo_vpn_helper.ui.components import TaskStatus


_INSTALLED = False


def install_request_completion_compat() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    def real_request_batch(
        self,
        servers: Iterable[str],
        worker: Callable[[str], object],
        *,
        creds: SessionCredentials,
        progress: Callable[[int, int, str], None] | None = None,
        cancel_event=None,
        deadline_seconds: float | None = None,
    ) -> tuple[list[tuple[str, object]], list[ServerSearchError], int]:
        # ``deadline_seconds`` is intentionally retained only for call-site
        # compatibility with older 3.0.13 layers.  It no longer controls search.
        _ = creds, deadline_seconds
        ordered = [str(value).strip() for value in servers if str(value).strip()]
        total = len(ordered)
        if total == 0:
            return [], [], 0

        completed: queue.Queue[tuple[str, bool, object]] = queue.Queue()

        def run_one(server: str) -> None:
            try:
                completed.put((server, True, worker(server)))
            except BaseException as exc:
                completed.put((server, False, exc))

        for server in ordered:
            threading.Thread(
                target=run_one,
                args=(server,),
                daemon=True,
                name=f"lv-vpn-request:{server}",
            ).start()

        pending = set(ordered)
        results: list[tuple[str, object]] = []
        errors: list[ServerSearchError] = []
        checked = 0

        while pending:
            if cancel_event is not None and cancel_event.is_set():
                break
            try:
                if cancel_event is None:
                    server, ok, payload = completed.get()
                else:
                    # This wake-up exists only so Esc can be observed promptly.
                    # Pending RouterOS calls are never failed because of it.
                    server, ok, payload = completed.get(timeout=0.10)
            except queue.Empty:
                continue

            if server not in pending:
                continue
            pending.remove(server)
            checked += 1
            if ok:
                results.append((server, payload))
            else:
                errors.append(ServerSearchError(server, classify_exception(payload)))
            if progress and not (cancel_event is not None and cancel_event.is_set()):
                progress(checked, total, server)

        return results, errors, checked

    FastSearchService._daemon_server_calls = real_request_batch

    # TaskStatus.busy() uses a separate floating BusyDialog. QWidget.hide()
    # previously hid only the inline TaskStatus widget, leaving that dialog on
    # screen after searchReady had already delivered and rendered the result.
    original_hide = TaskStatus.hide

    def hide(self):
        self._close_busy_dialog()
        return original_hide(self)

    TaskStatus.hide = hide
    TaskStatus._lv_real_request_completion = True
    _INSTALLED = True
