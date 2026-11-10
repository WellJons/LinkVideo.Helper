from __future__ import annotations

"""RouterOS 6 day counter for LV lifecycle scripts.

RouterOS 6.47 does not support RouterOS 7's :timestamp/:tonsec. Convert the
legacy mmm/DD/YYYY clock date to an epoch-day integer (days since 1970-01-01).
The LV2 comment format stays identical, so Windows and v7 track the same days.
This is a source-generation adapter only; no router is modified here.
"""

import datetime
import re

V7_DAY_EXPR = ":local nowDay ([:tonsec [:timestamp]] / 86400000000000)"

# Month offsets for a non-leap year, evaluated using RouterOS v6 commands.
MONTH_OFFSETS = (
    ("jan", 0), ("feb", 31), ("mar", 59), ("apr", 90),
    ("may", 120), ("jun", 151), ("jul", 181), ("aug", 212),
    ("sep", 243), ("oct", 273), ("nov", 304), ("dec", 334),
)

# No :timestamp, :tonsec, or v7-only parser features here.
V6_DAY_SOURCE = '''
:local lvDate [/system clock get date]
:if ([:len $lvDate] != 11) do={ :error "LV: unsupported RouterOS 6 date format" }
:local lvMon [:pick $lvDate 0 3]
:local lvYear [:tonum [:pick $lvDate 7 11]]
:local lvDom [:tonum [:pick $lvDate 4 6]]
:local lvOffset -1
'''.strip() + "\n" + "\n".join(
    f':if ($lvMon = "{month}") do={{ :set lvOffset {offset} }}'
    for month, offset in MONTH_OFFSETS
) + '''
:if (($lvOffset < 0) || ($lvYear < 2020) || ($lvDom < 1) || ($lvDom > 31)) do={
    :error "LV: invalid RouterOS 6 clock; refusing to age VPN clients"
}
:local lvLeap 0
:if ((($lvYear % 4) = 0) && ((($lvYear % 100) != 0) || (($lvYear % 400) = 0))) do={
    :if ($lvOffset > 31) do={ :set lvLeap 1 }
}
:local lvYprev ($lvYear - 1)
:local nowDay ((($lvYear - 1970) * 365) + ($lvYprev / 4) - ($lvYprev / 100) + ($lvYprev / 400) - 477 + $lvOffset + $lvDom - 1 + $lvLeap)
'''.strip()


def adapt_script_for_routeros6(source: str) -> str:
    """Replace only the LV2 date expression; leave AutoRestore unchanged."""
    result = str(source)
    if V7_DAY_EXPR not in result:
        return result
    result = result.replace(V7_DAY_EXPR, V6_DAY_SOURCE)
    if ":timestamp" in result or ":tonsec" in result:
        raise ValueError("LV RouterOS 6 adaptation left a RouterOS 7 date expression")
    return result


def epoch_day_for_legacy_date(date_text: str) -> int:
    """Python reference oracle for testing the exact v6 arithmetic."""
    match = re.fullmatch(r"([a-z]{3})/(\d{2})/(\d{4})", date_text)
    if not match:
        raise ValueError(date_text)
    mon, day, year = match.groups()
    month_num = next((i + 1 for i, (name, _) in enumerate(MONTH_OFFSETS) if name == mon), None)
    if month_num is None:
        raise ValueError(mon)
    dt = datetime.date(int(year), month_num, int(day))
    return (dt - datetime.date(1970, 1, 1)).days
