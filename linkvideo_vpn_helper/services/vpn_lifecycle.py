from __future__ import annotations

import re
import time
from dataclasses import dataclass

LV_AUTOMATION_VERSION = "1.0.1"
LV_MARKER = "|LV1|"
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


def parse_lv_comment(comment: str) -> LifecycleMeta:
    raw = str(comment or "")
    pos = raw.find(LV_MARKER)
    if pos < 0:
        return LifecycleMeta(base_comment=raw.rstrip())
    base = raw[:pos].rstrip()
    tail = raw[pos:]
    state_m = re.search(r"\|state=([A-Z]+)\|", tail)
    last_m = re.search(r"\|last=(\d+)\|", tail)
    ver_m = re.search(r"\|ver=([^|]+)\|", tail)
    return LifecycleMeta(
        state=(state_m.group(1) if state_m else "U"),
        last_ns=(int(last_m.group(1)) if last_m else 0),
        version=(ver_m.group(1) if ver_m else ""),
        base_comment=base,
    )


def compose_lv_comment(base_comment: str, state: str, last_ns: int, version: str = LV_AUTOMATION_VERSION) -> str:
    base = str(base_comment or "").strip()
    marker = f"{LV_MARKER}state={state}|last={max(0, int(last_ns))}|ver={version}|"
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
