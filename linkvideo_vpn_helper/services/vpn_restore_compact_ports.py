from __future__ import annotations

"""Compatibility for restoring archive rows whose visible NAT field is compact.

3.0.13 intentionally stores only external ports in the operator-facing sheet while
keeping the complete NAT rules in RouterOS snapshot. Normally restoration uses the
snapshot. This fallback covers old/damaged archive rows without a usable snapshot:
`10001; 10002 [off]` is interpreted conservatively as TCP 1:1 NAT.
"""

import re


_INSTALLED = False


def _fallback_nat_compact(self, row: dict[str, str], login: str, remote: str) -> list[dict[str, str]]:
    text = str(row.get("NAT / Порты", "") or "")
    rules: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for raw_part in text.split(";"):
        part = raw_part.strip()
        if not part:
            continue
        disabled = "[off]" in part.lower()
        clean = re.sub(r"\s*\[off\]\s*$", "", part, flags=re.I).strip()

        # Lossless legacy representation: `tcp 10001→20001`.
        verbose = re.fullmatch(
            r"(?P<proto>[a-z0-9]+)\s+(?P<ext>\d+)\s*→\s*(?P<to>\d+)",
            clean,
            flags=re.I,
        )
        if verbose:
            proto = verbose.group("proto").lower()
            ext = verbose.group("ext")
            to_port = verbose.group("to")
        else:
            # Compact operator representation contains external port only. With
            # no snapshot there is no trustworthy alternate internal target, so
            # the safest recoverable interpretation is the normal LinkVideo 1:1
            # TCP mapping.
            compact = re.fullmatch(r"(?:(?P<proto>tcp|udp)\s+)?(?P<ext>\d+)", clean, flags=re.I)
            if not compact:
                continue
            proto = str(compact.group("proto") or "tcp").lower()
            ext = compact.group("ext")
            to_port = ext

        key = (proto, ext, to_port)
        if key in seen:
            continue
        seen.add(key)
        rules.append(
            {
                "chain": "dstnat",
                "protocol": proto,
                "dst-port": ext,
                "action": "dst-nat",
                "to-addresses": remote,
                "to-ports": to_port,
                "comment": login,
                "disabled": "yes" if disabled else "no",
            }
        )
    return rules


def install_vpn_restore_compact_ports() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from linkvideo_vpn_helper.services.vpn_restore_service import VPNRestoreService

    VPNRestoreService._fallback_nat = _fallback_nat_compact
    _INSTALLED = True
