from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any


BUSINESS_TZ = timezone(timedelta(hours=7), name="LinkVideo-UTC+7")


def install_business_timezone() -> None:
    """Force VPN lifecycle calculations to LinkVideo operational time (UTC+7).

    RouterOS timestamps without an explicit offset are interpreted as UTC+7.
    PostgreSQL TIMESTAMPTZ then stores the corresponding instant safely in UTC,
    while employee-facing deadlines and history are rendered back in UTC+7.
    """

    from . import monitor as monitor_module

    if getattr(monitor_module, "_business_timezone_compat", False):
        return

    def parse_routeros_dt(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text or re.match(r"^jan/01/1970\b", text, re.I):
            return None
        for pattern in ("%b/%d/%Y %H:%M:%S", "%b/%d/%Y %H:%M"):
            try:
                return datetime.strptime(text, pattern).replace(tzinfo=BUSINESS_TZ)
            except ValueError:
                pass
        try:
            result = datetime.fromisoformat(text)
            return result.astimezone(BUSINESS_TZ) if result.tzinfo else result.replace(tzinfo=BUSINESS_TZ)
        except ValueError:
            return None

    def next_action(last_seen: datetime | None, first_seen: datetime | None) -> tuple[datetime | None, str | None]:
        now = datetime.now(BUSINESS_TZ)
        if last_seen is None:
            if first_seen is None:
                return None, None
            base = first_seen.astimezone(BUSINESS_TZ) if first_seen.tzinfo else first_seen.replace(tzinfo=BUSINESS_TZ)
            due = base + timedelta(days=30)
            return (due if due > now else now), "delete_never_active"
        base = last_seen.astimezone(BUSINESS_TZ) if last_seen.tzinfo else last_seen.replace(tzinfo=BUSINESS_TZ)
        sleep_at = base + timedelta(days=30)
        quarantine_at = base + timedelta(days=90)
        delete_at = base + timedelta(days=365)
        if now < sleep_at:
            return sleep_at, "sleep"
        if now < quarantine_at:
            return quarantine_at, "quarantine"
        if now < delete_at:
            return delete_at, "delete"
        return now, "delete"

    def state(existing: str, *, disabled: bool, is_active: bool, last_seen: datetime | None) -> str:
        existing = str(existing or "unknown")
        if is_active:
            return "active"
        if disabled:
            if existing in {"quarantine", "manual_disabled"}:
                return existing
            return "manual_disabled"
        if last_seen is None:
            return "never_active"
        seen = last_seen.astimezone(BUSINESS_TZ) if last_seen.tzinfo else last_seen.replace(tzinfo=BUSINESS_TZ)
        age = datetime.now(BUSINESS_TZ) - seen
        if age >= timedelta(days=90):
            return "quarantine_due"
        if age >= timedelta(days=30):
            return "sleeping"
        return "active"

    monitor_module._parse_routeros_dt = parse_routeros_dt
    monitor_module._next_action = next_action
    monitor_module._state = state
    monitor_module._business_timezone_compat = True
