"""Deprecated transition integration.

The central PostgreSQL service is no longer exposed as a normal VPN activity
backend in Helper. It is reserved for account archive/recovery.
"""

from __future__ import annotations


_INSTALLED = False


def install_cloud_activity_nav() -> None:
    """Do nothing: no central activity page is added to Helper navigation."""
    global _INSTALLED
    _INSTALLED = True
