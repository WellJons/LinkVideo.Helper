from __future__ import annotations

"""Deterministic per-server IPv4 allocation for LinkVideo PPP clients.

A manually assigned high address never advances the cursor. Search from the
configured start of a single /16, skip already occupied addresses, and fail
closed when exhausted. The caller must snapshot RouterOS immediately before
planning and re-check the selected addresses before creating PPP profiles.
"""
import ipaddress
from typing import Iterable, Mapping

DEFAULT_START_IP = "172.16.0.1"


def validate_start_ip(value: str) -> str:
    raw = str(value or "").strip()
    try:
        ip = ipaddress.IPv4Address(raw)
    except (ipaddress.AddressValueError, ValueError, TypeError) as exc:
        raise ValueError("Начальный Remote Address должен быть корректным IPv4-адресом") from exc
    if int(ip) & 0xFF in (0, 255):
        raise ValueError("Начальный Remote Address не может заканчиваться на .0 или .255")
    if ip.is_multicast or ip.is_unspecified or ip.is_loopback or ip.is_link_local:
        raise ValueError("Начальный Remote Address должен быть обычным адресом VPN-пула")
    return str(ip)


def _as_ipv4(raw: object) -> str:
    value = str(raw or "").strip()
    # RouterOS can expose "IP/32"; a pool name, ip-range or an empty value
    # is NOT a fixed address and must not reserve a guessed host.
    if "/" in value:
        address, _sep, prefix = value.partition("/")
        if not prefix.isdigit():
            return ""
        value = address
    try:
        return str(ipaddress.IPv4Address(value))
    except (ipaddress.AddressValueError, ValueError, TypeError):
        return ""


def used_remote_addresses(snapshot: Mapping[str, Iterable[dict]]) -> set[str]:
    occupied: set[str] = set()
    for section, fields in (
        ("secrets", ("remote-address", "remote_address")),
        ("profiles", ("remote-address", "remote_address", "local-address", "local_address")),
        ("actives", ("address", "remote-address", "remote_address")),
        # Manual NAT rules can still direct traffic to an address after the
        # PPP secret was removed. Do not reassign that IP silently.
        ("nat_rules", ("to-addresses", "to-address", "to_addresses")),
    ):
        for row in snapshot.get(section, ()) or ():
            for field in fields:
                ip = _as_ipv4(row.get(field))
                if ip:
                    occupied.add(ip)
    return occupied


def next_free_address(used: Iterable[str], start: str = DEFAULT_START_IP) -> str:
    first = ipaddress.IPv4Address(validate_start_ip(start))
    subnet = ipaddress.IPv4Network(f"{first}/16", strict=False)
    occupied = {_as_ipv4(ip) for ip in used}
    # Do not advance to a different /16 just because someone configured
    # one high manual address. The server's prefix remains fixed.
    for value in range(int(first), int(subnet.broadcast_address)):
        if value & 0xFF in (0, 255):
            continue
        candidate = str(ipaddress.IPv4Address(value))
        if candidate not in occupied:
            return candidate
    raise ValueError(f"Нет свободных Remote Address в диапазоне {first}–{subnet.broadcast_address}")
