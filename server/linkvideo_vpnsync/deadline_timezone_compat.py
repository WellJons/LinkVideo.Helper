from __future__ import annotations

import threading


def install_deadline_timezone_compat() -> None:
    """Keep deadline scheduler diagnostics aligned with configured business time."""
    from . import deadline_scheduler as module

    cls = module.DeadlineScheduler
    if getattr(cls, "_timezone_status_compat", False):
        return

    def timezone_label(self) -> str:
        offset = int(getattr(self.settings, "business_utc_offset_hours", 7))
        return f"UTC{offset:+d}"

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        module.deadline_wake_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="vpnsync-deadline",
        )
        self._thread.start()
        print(
            f"[RETENTION] deadline scheduler started; "
            f"execution={'ENABLED' if self.enabled else 'DISABLED'}; "
            f"timezone={timezone_label(self)}",
            flush=True,
        )

    def status(self):
        thread = self._thread
        with self._state_lock:
            return {
                "enabled": self.enabled,
                "worker_alive": bool(thread and thread.is_alive()),
                "business_timezone": timezone_label(self),
                "next_due_at": self._next_due_at,
                "next_action_type": self._next_action_type,
                "next_login": self._next_login,
                "next_server": self._next_server,
                "last_action_at": self._last_action_at,
                "last_error": self._last_error,
            }

    cls.start = start
    cls.status = status
    cls._timezone_status_compat = True
