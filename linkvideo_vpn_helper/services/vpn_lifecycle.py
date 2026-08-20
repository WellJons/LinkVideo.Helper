from __future__ import annotations

import re
import time
from dataclasses import dataclass


LV_AUTOMATION_VERSION = "2.0.0"
LV_MARKER = "|LV2|"
LEGACY_LV_MARKER = "|LV1|"
DAY_NS = 86_400_000_000_000
SLEEP_DAYS = 30
QUARANTINE_DAYS = 90
ARCHIVE_DAYS = 365


@dataclass(slots=True)
class LifecycleMeta:
    state: str = "U"
    last_ns: int = 0
    version: str = LV_AUTOMATION_VERSION
    base_comment: str = ""

    @property
    def age_days(self) -> int | None:
        if self.last_ns <= 0:
            return None
        return max(0, int((time.time_ns() - self.last_ns) // DAY_NS))


def _first_marker_pos(raw: str) -> int:
    positions = [pos for pos in (raw.find(LV_MARKER), raw.find(LEGACY_LV_MARKER)) if pos >= 0]
    return min(positions) if positions else -1


def _day_to_ns(value: int) -> int:
    return max(0, int(value or 0)) * DAY_NS


def _ns_to_day(value: int) -> int:
    number = max(0, int(value or 0))
    # Existing callers pass nanoseconds. A small value is already a day index and
    # is accepted to keep migration/test helpers tolerant.
    return number if 0 < number < 10_000_000 else number // DAY_NS


def parse_lv_comment(comment: str) -> LifecycleMeta:
    """Read both compact LV2 metadata and legacy LV1 metadata.

    LV2 stores only day precision, which is sufficient for 30/90/365-day policy
    and makes PPP Secret comments much shorter. If an interrupted migration left
    both markers in one comment, the newest LV2 state wins while the most recent
    known activity timestamp is preserved.
    """
    raw = str(comment or "")
    first = _first_marker_pos(raw)
    base = raw[:first].rstrip() if first >= 0 else raw.rstrip()

    state = "U"
    last_ns = 0
    version = ""

    legacy_pos = raw.find(LEGACY_LV_MARKER)
    if legacy_pos >= 0:
        tail = raw[legacy_pos:]
        state_m = re.search(r"\|state=([A-Z]+)\|", tail)
        last_m = re.search(r"\|last=(\d+)\|", tail)
        ver_m = re.search(r"\|ver=([^|]+)\|", tail)
        if state_m:
            state = state_m.group(1)
        if last_m:
            last_ns = max(last_ns, int(last_m.group(1)))
        if ver_m:
            version = ver_m.group(1)

    current_pos = raw.find(LV_MARKER)
    if current_pos >= 0:
        tail = raw[current_pos:]
        state_m = re.search(r"\|s=([A-Z]+)\|", tail)
        last_m = re.search(r"\|l=(\d+)\|", tail)
        if state_m:
            state = state_m.group(1)
        if last_m:
            last_ns = max(last_ns, _day_to_ns(int(last_m.group(1))))
        version = LV_AUTOMATION_VERSION

    return LifecycleMeta(
        state=state or "U",
        last_ns=max(0, int(last_ns or 0)),
        version=version,
        base_comment=base,
    )


def compose_lv_comment(
    base_comment: str,
    state: str,
    last_ns: int,
    version: str = LV_AUTOMATION_VERSION,
) -> str:
    """Compose the compact LV2 marker used by RouterOS automation."""
    base = str(base_comment or "").strip()
    marker = f"{LV_MARKER}s={str(state or 'U').upper()}|l={_ns_to_day(last_ns)}|"
    return f"{base} {marker}".strip()


def classify_state(last_ns: int, disabled: bool, active: bool = False) -> str:
    if active:
        return "A"
    if disabled:
        return "M"
    if last_ns <= 0:
        return "U"
    age = max(0, int((time.time_ns() - int(last_ns)) // DAY_NS))
    if age >= ARCHIVE_DAYS:
        return "R"
    if age >= QUARANTINE_DAYS:
        return "Q"
    if age >= SLEEP_DAYS:
        return "S"
    return "A"
