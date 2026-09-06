"""Deprecated transition bridge.

LinkVideo.Helper no longer mirrors normal interactive RouterOS actions into the
central database. PostgreSQL/VPNSync is reserved for account archive/recovery,
not for the normal operator activity stream.

Kept as a no-op compatibility shim for stale imports.
"""

from __future__ import annotations


_INSTALLED = False


def install_cloud_activity_bridge(service, settings) -> None:
    """Do nothing: normal Helper activity is not persisted to VPNSync."""
    global _INSTALLED
    _INSTALLED = True
