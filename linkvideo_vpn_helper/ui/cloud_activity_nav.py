from __future__ import annotations


_INSTALLED = False


def install_cloud_activity_nav() -> None:
    """Install cloud-backed recovery support without exposing a global audit page.

    Employee audit records are still written to PostgreSQL by the activity
    bridge. The desktop no longer has a separate activity/history navigation
    item: if operator history is exposed again, it should be scoped to the
    client currently opened from Search & Manage.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    # VPNSync API credentials are intentionally separate from the Ubuntu SSH
    # account. Make an authentication failure explicit before any operator can
    # mistake it for a network failure.
    from linkvideo_vpn_helper.services.cloud_auth_message_compat import install_cloud_auth_message_compat
    install_cloud_auth_message_compat()

    # Preserve structured VPNSync recovery errors before any cloud-backed
    # support UI is initialized. Active search and normal mutations still go
    # directly to MikroTik.
    from linkvideo_vpn_helper.services.cloud_http_error_compat import install_cloud_http_error_details
    install_cloud_http_error_details()

    # Archive recovery belongs inside the normal client-search workflow.
    from linkvideo_vpn_helper.ui.cloud_archive_search_integration import install_cloud_archive_search
    install_cloud_archive_search()
    from linkvideo_vpn_helper.ui.cloud_archive_completion_compat import install_cloud_archive_completion_compat
    install_cloud_archive_completion_compat()

    # Keep the main navigation intentionally small. This also removes the item
    # defensively if another compatibility layer happened to add it earlier.
    from linkvideo_vpn_helper.ui.main_window import MainWindow

    MainWindow.NAV_ITEMS = tuple(item for item in MainWindow.NAV_ITEMS if item[0] != "vpn_activity")
    _INSTALLED = True
