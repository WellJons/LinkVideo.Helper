from __future__ import annotations

import json
from typing import Any

from .activity_bus import activity_bus


def _wake_deadlines() -> None:
    try:
        from .deadline_scheduler import wake_deadline_scheduler
        wake_deadline_scheduler()
    except Exception:
        pass


def install_activity_tracking() -> None:
    """Audit every automatic snapshot and wake push/deadline subscribers.

    Raw RouterOS events are already persisted by SnapshotSync in ``sync_events``.
    This wrapper adds one higher-level audit row describing the completed
    reconciliation and publishes an in-process wake-up for connected desktops.
    Every completed snapshot also re-arms the nearest-deadline scheduler because
    last-seen/lifecycle data may have changed. Audit/wake failures are deliberately
    non-fatal and must never break RouterOS reconciliation itself.
    """

    from . import monitor as monitor_module

    cls = monitor_module.SnapshotSync
    if getattr(cls, "_activity_tracking", False):
        return

    original_sync = cls.sync

    def _server_id(self, host: str) -> int | None:
        try:
            with self.db.connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT id FROM vpn_servers WHERE hostname = %s", (str(host),))
                row = cur.fetchone()
                return int(row["id"]) if row else None
        except Exception:
            return None

    def _audit(self, *, target, event_path: str, result: dict[str, Any] | None, error: Exception | None) -> None:
        server_id = _server_id(self, target.host)
        source = "VPNSync startup" if event_path == "startup" else "RouterOS event"
        actor = "VPNSync" if event_path == "startup" else "RouterOS"
        details: dict[str, Any] = {
            "host": target.host,
            "event_path": event_path,
        }
        if result:
            details.update({
                "clients": int(result.get("clients") or 0),
                "active": int(result.get("active") or 0),
                "added": int(result.get("added") or 0),
                "changed": int(result.get("changed") or 0),
                "deleted": int(result.get("deleted") or 0),
            })
        if error is not None:
            details["error"] = str(error)[:1000]
        try:
            with self.db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vpnsync_audit_log
                        (actor, role, source, action, server_id, success, details)
                    VALUES (%s, '', %s, 'sync.snapshot', %s, %s, %s::jsonb)
                    """,
                    (
                        actor,
                        source,
                        server_id,
                        error is None,
                        json.dumps(details, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
                conn.commit()
        except Exception:
            pass
        activity_bus.publish({
            "kind": "sync",
            "source": source,
            "server": target.host,
            "success": error is None,
            "details": details,
        })
        _wake_deadlines()

    def sync(self, target, *, event_path: str = "startup", event_payload: dict[str, str] | None = None):
        try:
            result = original_sync(self, target, event_path=event_path, event_payload=event_payload)
        except Exception as exc:
            _audit(self, target=target, event_path=event_path, result=None, error=exc)
            raise
        _audit(self, target=target, event_path=event_path, result=result, error=None)
        return result

    cls.sync = sync
    cls._activity_tracking = True
