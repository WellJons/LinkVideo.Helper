from __future__ import annotations

"""Compatibility hook for the retired automatic Google Sheets bridge.

Google Sheets is disaster-recovery only in 3.0.13. Normal Helper operations
work directly with RouterOS, while VPNSync/PostgreSQL receives audit/history
asynchronously. This installer intentionally performs no imports or mutations;
it remains only because older startup code still calls the hook.
"""


_INSTALLED = False


def install_vpn_automation_sheets_bridge() -> None:
    """Keep the legacy startup hook harmless and deterministic."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
