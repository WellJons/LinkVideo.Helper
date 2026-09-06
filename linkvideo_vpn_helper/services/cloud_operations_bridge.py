"""Deprecated transition bridge.

Interactive LinkVideo.Helper VPN operations must always execute directly against
MikroTik. VPNSync/PostgreSQL is an archive/recovery service, not the required
writer for normal operator work.

The compatibility entry point remains as a no-op so stale imports cannot switch
VPNService methods back to cloud-routed writes.
"""

from __future__ import annotations


_INSTALLED = False


def install_cloud_operations_bridge(service, settings) -> None:
    """Do nothing: interactive VPN mutations remain direct RouterOS calls."""
    global _INSTALLED
    _INSTALLED = True
