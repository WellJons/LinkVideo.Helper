"""Deprecated transition bridge.

Interactive VPN search in LinkVideo.Helper must always query MikroTik directly.
PostgreSQL/VPNSync is reserved for archive/recovery data and must never become a
required dependency for normal operator search.

This module is intentionally kept as a no-op compatibility shim so older imports
or tests do not fail while the central-search experiment is being removed.
"""

from __future__ import annotations


_INSTALLED = False


def install_cloud_search_bridge(search_service, settings, vpn_service=None) -> None:
    """Do nothing: interactive search remains direct RouterOS."""
    global _INSTALLED
    _INSTALLED = True
