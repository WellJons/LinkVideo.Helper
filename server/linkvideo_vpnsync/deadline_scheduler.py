from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from typing import Any

from .activity_bus import activity_bus
from .monitor import _bool, load_targets
from .routeros import RouterOSClient
from .timezone_compat import BUSINESS_TZ


deadline_wake_event = threading.Event()


def wake_deadline_scheduler() -> None:
    deadline_wake_event.set()


class DeadlineScheduler:
    """Nearest-deadline lifecycle scheduler; no recurring fleet polling.

    It queries one nearest ``next_action_at`` row, sleeps exactly until that
    instant, and is interrupted only when an event-driven RouterOS sync changes
    lifecycle data. Destructive work is additionally gated by
    ``VPNSYNC_RETENTION_ENABLED``.
    """

    def __init__(self, db, settings, monitor) -> None:
        self.db = db
        self.settings = settings
        self.monitor = monitor
        self.enabled = bool(settings.retention_enabled)
        self.targets = {target.host: target for target in load_targets(settings)}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._next_due_at = None
        self._next_action_type = ""
        self._next_login = ""
        self._next_server = ""
        self._last_action_at = None
        self._last_error = ""

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        deadline_wake_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="vpnsync-deadline")
        self._thread.start()
        print(
            f"[RETENTION] deadline scheduler started; execution={'ENABLED' if self.enabled else 'DISABLED'}; timezone=UTC+3",
            flush=True,
        )

    def stop(self) -> None:
        self._stop.set()
        deadline_wake_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=10.0)

    def status(self) -> dict[str, Any]:
        thread = self._thread
        with self._state_lock:
            return {
                "enabled": self.enabled,
                "worker_alive": bool(thread and thread.is_alive()),
                "business_timezone": "UTC+3",
                "next_due_at": self._next_due_at,
                "next_action_type": self._next_action_type,
                "next_login": self._next_login,
                "next_server": self._next_server,
                "last_action_at": self._last_action_at,
                "last_error": self._last_error,
            }

    def _set_next(self, row: dict[str, Any] | None) -> None:
        with self._state_lock:
            if not row:
                self._next_due_at = None
                self._next_action_type = ""
                self._next_login = ""
                self._next_server = ""
                return
            self._next_due_at = row.get("next_action_at")
            self._next_action_type = str(row.get("next_action_type") or "")
            self._next_login = str(row.get("login") or "")
            self._next_server = str(row.get("hostname") or "")

    def _nearest(self) -> dict[str, Any] | None:
        with self.db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.server_id, c.login, c.lifecycle_state, c.disabled,
                       c.last_seen_at, c.first_seen_at, c.next_action_at,
                       c.next_action_type, c.password_enc IS NOT NULL AS password_saved,
                       c.recovery_snapshot_enc IS NOT NULL AS snapshot_saved,
                       s.hostname
                  FROM vpn_clients c
                  JOIN vpn_servers s ON s.id = c.server_id
                 WHERE c.next_action_at IS NOT NULL
                 ORDER BY c.next_action_at, c.id
                 LIMIT 1
                """
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                row = self._nearest()
                self._set_next(row)
                if row is None:
                    deadline_wake_event.wait()
                    deadline_wake_event.clear()
                    continue

                due = row.get("next_action_at")
                if due is None:
                    deadline_wake_event.wait()
                    deadline_wake_event.clear()
                    continue
                if due.tzinfo is None:
                    due = due.replace(tzinfo=BUSINESS_TZ)
                now = datetime.now(BUSINESS_TZ)
                seconds = (due.astimezone(BUSINESS_TZ) - now).total_seconds()
                if seconds > 0:
                    deadline_wake_event.wait(timeout=seconds)
                    if deadline_wake_event.is_set():
                        deadline_wake_event.clear()
                        continue
                if self._stop.is_set():
                    return

                if not self.enabled:
                    # Retention is deliberately safety-gated. Do not loop on an
                    # already-due row; wait until a real DB/RouterOS event wakes
                    # us or the service restarts with retention enabled.
                    deadline_wake_event.wait()
                    deadline_wake_event.clear()
                    continue

                self._process_due(row)
            except Exception as exc:
                with self._state_lock:
                    self._last_error = str(exc)[:1000]
                print(f"[RETENTION] scheduler error: {exc}", flush=True)
                # Failure recovery is event-driven too. A short wait only backs
                # off a failed action; it is not a fleet polling interval.
                if deadline_wake_event.wait(timeout=60.0):
                    deadline_wake_event.clear()

    def _audit(self, row: dict[str, Any], action: str, *, success: bool, details: dict[str, Any] | None = None) -> None:
        payload = dict(details or {})
        try:
            with self.db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vpnsync_audit_log
                        (actor, role, source, action, server_id, login, success, details)
                    VALUES ('VPNSync', 'system', 'retention', %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        action,
                        int(row["server_id"]),
                        str(row["login"]),
                        bool(success),
                        json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")),
                    ),
                )
                conn.commit()
        finally:
            activity_bus.publish({
                "kind": "retention",
                "source": "retention",
                "action": action,
                "server": str(row.get("hostname") or ""),
                "login": str(row.get("login") or ""),
                "success": bool(success),
                "details": payload,
            })

    def _process_due(self, stale_row: dict[str, Any]) -> None:
        host = str(stale_row.get("hostname") or "")
        target = self.targets.get(host)
        if target is None:
            self._audit(stale_row, "retention.error", success=False, details={"error": "RouterOS target is not configured"})
            self._postpone(int(stale_row["id"]), minutes=15)
            return

        # Share the same per-host lock as event/startup snapshot sync so a
        # listener cannot race a destructive retention action.
        lock = self.monitor.syncer._lock(host)
        with lock:
            row = self._reload_client(int(stale_row["id"]))
            if row is None or row.get("next_action_at") is None:
                return
            due = row["next_action_at"]
            if due.tzinfo is None:
                due = due.replace(tzinfo=BUSINESS_TZ)
            if due.astimezone(BUSINESS_TZ) > datetime.now(BUSINESS_TZ):
                return

            action_type = str(row.get("next_action_type") or "")
            if action_type == "sleep":
                self._mark_sleeping(row)
            elif action_type == "quarantine":
                self._quarantine(target, row)
            elif action_type in {"delete", "delete_never_active"}:
                self._delete(target, row, never_active=action_type == "delete_never_active")
            else:
                self._audit(row, "retention.error", success=False, details={"error": f"Unknown action {action_type}"})
                self._postpone(int(row["id"]), minutes=15)
                return

        with self._state_lock:
            self._last_action_at = datetime.now(BUSINESS_TZ)
            self._last_error = ""
        deadline_wake_event.set()

    def _reload_client(self, client_id: int) -> dict[str, Any] | None:
        with self.db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.server_id, c.login, c.lifecycle_state, c.disabled,
                       c.last_seen_at, c.first_seen_at, c.next_action_at,
                       c.next_action_type, c.password_enc IS NOT NULL AS password_saved,
                       c.recovery_snapshot_enc IS NOT NULL AS snapshot_saved,
                       s.hostname
                  FROM vpn_clients c
                  JOIN vpn_servers s ON s.id = c.server_id
                 WHERE c.id = %s
                """,
                (int(client_id),),
            )
            result = cur.fetchone()
            return dict(result) if result else None

    def _postpone(self, client_id: int, *, minutes: int) -> None:
        with self.db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE vpn_clients SET next_action_at = now() + (%s * interval '1 minute'), updated_at = now() WHERE id=%s",
                (max(1, int(minutes)), int(client_id)),
            )
            conn.commit()

    @staticmethod
    def _next_delete_at(row: dict[str, Any]) -> datetime:
        reference = row.get("last_seen_at") or row.get("first_seen_at") or datetime.now(BUSINESS_TZ)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=BUSINESS_TZ)
        return reference.astimezone(BUSINESS_TZ) + timedelta(days=365)

    def _mark_sleeping(self, row: dict[str, Any]) -> None:
        reference = row.get("last_seen_at") or row.get("first_seen_at")
        if reference is None:
            return
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=BUSINESS_TZ)
        quarantine_at = reference.astimezone(BUSINESS_TZ) + timedelta(days=90)
        with self.db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE vpn_clients
                   SET lifecycle_state='sleeping',
                       next_action_at=%s,
                       next_action_type='quarantine',
                       updated_at=now()
                 WHERE id=%s
                """,
                (quarantine_at, int(row["id"])),
            )
            conn.commit()
        self._audit(row, "retention.sleep", success=True, details={"next_action_at": quarantine_at})

    def _routeros_rows(self, api: RouterOSClient, login: str) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
        all_secrets = api.print("/ppp/secret")
        secrets = [item for item in all_secrets if str(item.get("name") or "").strip() == login]
        active = [item for item in api.print("/ppp/active") if str(item.get("name") or "").strip() == login]
        profiles = api.print("/ppp/profile")
        nat = api.print("/ip/firewall/nat")
        return secrets, active, profiles, nat

    def _active_now(self, row: dict[str, Any], active: list[dict[str, str]]) -> bool:
        if not active:
            return False
        now = datetime.now(BUSINESS_TZ)
        with self.db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE vpn_clients
                   SET lifecycle_state='active', last_seen_at=%s,
                       next_action_at=%s, next_action_type='sleep', updated_at=now()
                 WHERE id=%s
                """,
                (now, now + timedelta(days=30), int(row["id"])),
            )
            conn.commit()
        self._audit(row, "retention.cancelled_active", success=True, details={"active_sessions": len(active)})
        return True

    def _quarantine(self, target, row: dict[str, Any]) -> None:
        login = str(row["login"])
        try:
            with RouterOSClient(target.host, target.username, target.password, port=target.port, timeout=target.timeout) as api:
                secrets, active, _profiles, _nat = self._routeros_rows(api, login)
                if self._active_now(row, active):
                    return
                if not secrets:
                    self._audit(row, "retention.quarantine", success=False, details={"error": "PPP secret is already missing"})
                    self._postpone(int(row["id"]), minutes=15)
                    return

                already_disabled = all(_bool(secret.get("disabled")) for secret in secrets)
                manual_disabled = str(row.get("lifecycle_state") or "") == "manual_disabled"
                if not already_disabled:
                    for secret in secrets:
                        item_id = str(secret.get(".id") or "")
                        if item_id:
                            api.disable("/ppp/secret", item_id)

            next_at = self._next_delete_at(row)
            state = "manual_disabled" if manual_disabled and already_disabled else "quarantine"
            with self.db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE vpn_clients
                       SET disabled=TRUE, lifecycle_state=%s,
                           next_action_at=%s, next_action_type='delete', updated_at=now()
                     WHERE id=%s
                    """,
                    (state, next_at, int(row["id"])),
                )
                conn.commit()
            self._audit(
                row,
                "retention.quarantine" if state == "quarantine" else "retention.manual_disabled_preserved",
                success=True,
                details={"routeros_rows": len(secrets), "next_action_at": next_at},
            )
        except Exception as exc:
            self._audit(row, "retention.quarantine", success=False, details={"error": str(exc)[:1000]})
            self._postpone(int(row["id"]), minutes=15)
            raise

    def _delete(self, target, row: dict[str, Any], *, never_active: bool) -> None:
        login = str(row["login"])
        try:
            with RouterOSClient(target.host, target.username, target.password, port=target.port, timeout=target.timeout) as api:
                secrets, active, profiles, live_nat = self._routeros_rows(api, login)
                if self._active_now(row, active):
                    return

                # A recoverable encrypted DB snapshot must exist before RouterOS
                # mutation. Password may be absent for legacy imports, but the
                # snapshot itself is mandatory for automatic destructive action.
                if not bool(row.get("snapshot_saved")):
                    self._audit(row, "retention.delete", success=False, details={"error": "Encrypted recovery snapshot is missing"})
                    self._postpone(int(row["id"]), minutes=60)
                    return

                with self.db.connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT p.routeros_rule_id
                          FROM vpn_nat_ports p
                         WHERE p.client_id=%s AND p.deleted=FALSE AND p.routeros_rule_id<>''
                        """,
                        (int(row["id"]),),
                    )
                    wanted_nat_ids = {str(item["routeros_rule_id"]) for item in cur.fetchall()}

                live_nat_ids = {str(item.get(".id") or "") for item in live_nat}
                for rule_id in sorted(wanted_nat_ids & live_nat_ids):
                    api.remove("/ip/firewall/nat", rule_id)

                profile_names = {str(secret.get("profile") or "").strip() for secret in secrets}
                secret_ids = {str(secret.get(".id") or "") for secret in secrets if str(secret.get(".id") or "")}
                for secret_id in sorted(secret_ids):
                    api.remove("/ppp/secret", secret_id)

                # Remove only custom profiles no longer referenced by another
                # PPP secret. Built-in profiles are never touched.
                remaining_secrets = [item for item in api.print("/ppp/secret") if str(item.get(".id") or "") not in secret_ids]
                used_profiles = {str(item.get("profile") or "").strip() for item in remaining_secrets}
                protected_profiles = {"", "default", "default-encryption"}
                for profile_name in sorted(profile_names - used_profiles - protected_profiles):
                    for profile in profiles:
                        if str(profile.get("name") or "").strip() != profile_name:
                            continue
                        profile_id = str(profile.get(".id") or "")
                        if profile_id:
                            api.remove("/ppp/profile", profile_id)

            archived = self.db.archive_client(
                int(row["server_id"]),
                login,
                deleted_reason="Никогда не подключался 30 дней" if never_active else "Неактивен 365 дней",
                deleted_by="VPNSync",
                source="retention",
            )
            self.db.append_change(
                server_id=int(row["server_id"]),
                client_id=None,
                login=login,
                event_type="deleted",
                summary="Автоматическое удаление VPN-клиента по сроку",
                source="retention",
                actor="VPNSync",
            )
            self._audit(
                row,
                "retention.delete",
                success=True,
                details={
                    "never_active": bool(never_active),
                    "nat_removed": len(wanted_nat_ids & live_nat_ids),
                    "secrets_removed": len(secret_ids),
                    "archived": bool(archived),
                },
            )
        except Exception as exc:
            self._audit(row, "retention.delete", success=False, details={"error": str(exc)[:1000]})
            self._postpone(int(row["id"]), minutes=60)
            raise
