from __future__ import annotations

"""Retired compatibility shim for the old automatic-patch entrypoint.

Old installations can still consume the legacy public manifest, but updated
Helper builds must not stage patches, register close hooks, or invoke a SYSTEM
scheduled task. The ordinary visible full-Setup UX is used instead.
"""

def install_silent_patch_updates() -> None:
    from linkvideo_vpn_helper.ui.update_ux_integration import install_update_ux
    from linkvideo_vpn_helper.ui.background_ux_integration import install_background_ux

    install_update_ux()
    install_background_ux()
