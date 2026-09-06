from __future__ import annotations

import json
from typing import Any

from .activity_bus import activity_bus


_SENSITIVE_PARTS = (
    "password",
    "passwd",
    "secret",
    "private-key",
    "private_key",
    "shared-key",
    "shared_key",
    "auth-key",
    "auth_key",
    "token",
)


def _wake_deadlines() -> None:
    try:
        from .deadline_scheduler import wake_deadline_scheduler
        wake_deadline_scheduler()
    except Exception:
        pass


def _safe_routeros_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Keep useful event context without ever persisting RouterOS credentials."""
    result: dict[str, Any] = {}
    for raw_key, raw_value in dict(payload or {}).items():
        key = str(raw_key or "")[:128]
        lowered = key.lower()
        if any(part in lowered for part in _SENSITIVE_PARTS):
            result[key] = "<redacted>"
            continue
        if isinstance(raw_value, (str, int, float, bool)) or raw_value is None:
            value = raw_value
        else:
            try:
                value = json.dumps(raw_value, ensure_ascii=False, default=str, separators=(",", ":"))
            except Exception:
                value = str(raw_value)
        if isinstance(value, str) and len(value) > 1000:
            value = value[:1000] + "…"
        result[key] = value
    return result


def install_activity_tracking() -> None:
    """Audit raw RouterOS events and every completed automatic reconciliation.

    The listener callback is recorded *before* the one-second debounce. Therefore
    several RouterOS changes in one employee operation remain separate activity
    rows, while only the expensive coherent snapshot is debounced. Sensitive
    fields (passwords, secrets, private/shared/auth keys and tokens) are redacted.

    Completed snapshots are recorded separately and re-arm the nearest-deadline
    scheduler because last-seen/lifecycle data may have changed. Audit failures
    are deliberately non-fatal and must never break RouterOS reconciliation.
    """

    from . import monitor as monitor_module

    cls = monitor_module.SnapshotSync
    manager_cls = monitor_module.RouterOSMonitorManager
    if getattr(cls, "_activity_tracking", False):
        return

    original_sync = cls.sync
    original_on_event = manager_cls._on_event

    def _server_id(self, host: str) -> int | None:
        try:
            with self.db.connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT id FROM vpn_servers WHERE hostname = %s", (str(host),))
                row = cur.fetchone()
                return int(row["id"]) if row else None
        except Exception:
            return None

    def _audit_snapshot(self, *, target, event_path: str, result: dict[str, Any] | None, error: Exception | None) -> None:
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

    def _audit_raw_event(self, target, path: str, payload: dict[str, Any] | None) -> None:
        safe = _safe_routeros_payload(payload)
        login = str(safe.get("name") or safe.get("user") or "")[:128]
        item_id = str(safe.get(".id") or "")[:128]
        server_id = _server_id(self.syncer, target.host)
        details = {
            "host": target.host,
            "path": str(path or "")[:256],
            "routeros_item_id": item_id,
            "fields": [str(key)[:128] for key in safe.keys()],
            "payload": safe,
        }
        try:
            with self.db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vpnsync_audit_log
                        (actor, role, source, action, server_id, login, success, details)
                    VALUES ('RouterOS', '', 'RouterOS listen', 'routeros.event', %s, %s, TRUE, %s::jsonb)
                    """,
                    (
                        server_id,
                        login,
                        json.dumps(details, ensure_ascii=False, default=str, separators=(",", ":")),
                    ),
                )
                conn.commit()
        except Exception:
            # The event must still trigger the normal debounced snapshot even if
            # history storage is temporarily unavailable.
            pass
        activity_bus.publish({
            "kind": "routeros_event",
            "source": "RouterOS listen",
            "action": "routeros.event",
            "server": target.host,
            "login": login,
            "success": True,
            "details": {
                "path": str(path or ""),
                "routeros_item_id": item_id,
                "fields": details["fields"],
            },
        })
        _wake_deadlines()

    def on_event(self, target, path: str, payload: dict[str, str]) -> None:
        if not self._stopped.is_set():
            _audit_raw_event(self, target, path, payload)
        original_on_event(self, target, path, payload)

    def sync(self, target, *, event_path: str = "startup", event_payload: dict[str, str] | None = None):
        try:
            result = original_sync(self, target, event_path=event_path, event_payload=event_payload)
        except Exception as exc:
            _audit_snapshot(self, target=target, event_path=event_path, result=None, error=exc)
            raise
        _audit_snapshot(self, target=target, event_path=event_path, result=result, error=None)
        return result

    manager_cls._on_event = on_event
    cls.sync = sync
    cls._activity_tracking = True
    manager_cls._raw_activity_tracking = True
