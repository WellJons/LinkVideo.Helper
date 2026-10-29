from __future__ import annotations

"""Safe *display-only* CRM tariff filtering and archive-depth sorting.

The CRM reference list does NOT prove that a tariff can be assigned.
Never treat `test_days` (trial period) as archive retention.
"""

import re
from typing import Iterable, TypeVar

from linkvideo_vpn_helper.services.billing_catalog import TariffCatalogEntry

T = TypeVar("T", bound=TariffCatalogEntry)

_ARCHIVE_RE = re.compile(
    r"(?<![\d])([0-9]{1,4})\s*(?:дн(?:ей|я|ь|и)?\.?|сут(?:ок|ки|ки)?)(?![\w])",
    re.IGNORECASE,
)
_ISO_CURRENCY_RE = re.compile(r"(?<![A-Za-z])(?:BYN|KZT|RUB|RUR)(?![A-Za-z])", re.I)
_BITRATE_RE = re.compile(r"(?<![0-9])([0-9]+(?:[.,][0-9]+)?)\s*мбит(?:\s*/\s*с|/c)?", re.I)


def bitrate_mbps_from_name(name: str) -> float | None:
    """One explicit Mbps speed in a tariff name; don't infer if ambiguous."""
    matches = _BITRATE_RE.findall(name or "")
    if len(matches) != 1:
        return None
    value = float(matches[0].replace(",", "."))
    return value if 0 < value < 10000 else None


def archive_days_from_name(name: str) -> int | None:
    """Extract explicit retention expressed in days, without guessing."""
    matches = [int(match.group(1)) for match in _ARCHIVE_RE.finditer(name or "")]
    return matches[0] if len(matches) == 1 else None


def country_tariff_visible(tariff: TariffCatalogEntry, country: str) -> bool:
    """Filter by CRM country and guard against foreign currency labels.

    Original CRM tariff names may omit ISO codes entirely ("ЮЛ 30 дней",
    "Стартовый 90"). Keep these authentic labels and only reject an
    explicitly contradictory foreign ISO-currency marker.
    """
    if tariff.country != country:
        return False
    label = tariff.name
    iso_markers = {m.group(0).upper() for m in _ISO_CURRENCY_RE.finditer(label)}
    if country == "Беларусь":
        return not iso_markers or iso_markers == {"BYN"}
    expected = {
        "Казахстан": {"KZT"},
        "Российская федерация": {"RUB", "RUR"},
    }.get(country)
    if expected is None:
        return False
    return not iso_markers or iso_markers <= expected


def select_tariffs(
    tariffs: Iterable[T], *,
    country: str = "",
    account_type: str = "",
    days: int = 0,
    search: str = "",
    bitrate_mbps: float = 0,
) -> tuple[T, ...]:
    """Sort by archive days ascending; unknown durations appear last.

    An entered days count is an EXACT archive-depth filter; zero means all.
    Types and country are display filters, not authorization rules.
    """
    if days < 0:
        raise ValueError("Глубина архива не может быть отрицательной")
    term = search.strip().casefold()
    filtered = []
    for entry in tariffs:
        if country and not country_tariff_visible(entry, country):
            continue
        if account_type and entry.account_type != account_type:
            continue
        depth = archive_days_from_name(entry.name)
        if days and depth != days:
            continue
        if bitrate_mbps and bitrate_mbps_from_name(entry.name) != bitrate_mbps:
            continue
        if term and term not in (entry.name + " " + entry.description + " " + entry.id).casefold():
            continue
        filtered.append(entry)
    return tuple(sorted(filtered, key=lambda x: (
        archive_days_from_name(x.name) is None,
        archive_days_from_name(x.name) if archive_days_from_name(x.name) is not None else 99999,
        x.name.casefold(), x.id,
    )))
