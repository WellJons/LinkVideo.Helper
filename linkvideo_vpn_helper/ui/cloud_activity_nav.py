from __future__ import annotations


_INSTALLED = False


def install_cloud_activity_nav() -> None:
    """Expose central audit/history without changing direct MikroTik workflows."""
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

    # Archive recovery belongs inside the normal client-search workflow. It is
    # installed here because this hook already initializes the read-only cloud
    # support surface, while active search and normal mutations stay direct to
    # MikroTik.
    from linkvideo_vpn_helper.ui.cloud_archive_search_integration import install_cloud_archive_search
    install_cloud_archive_search()
    from linkvideo_vpn_helper.ui.cloud_archive_completion_compat import install_cloud_archive_completion_compat
    install_cloud_archive_completion_compat()

    # History must show the actual non-secret parameters of employee actions
    # (ports, enabled/disabled state, remote address), not only the action name.
    from linkvideo_vpn_helper.ui.vpn_activity_details_compat import install_vpn_activity_details
    install_vpn_activity_details()

    from linkvideo_vpn_helper.ui.main_window import MainWindow

    if not any(item[0] == "vpn_activity" for item in MainWindow.NAV_ITEMS):
        items = list(MainWindow.NAV_ITEMS)
        insert_at = next((index + 1 for index, item in enumerate(items) if item[0] == "vpn_servers"), len(items))
        items.insert(insert_at, ("vpn_activity", "≡", "История VPN", "Действия сотрудников, MikroTik и автоматическая архивация"))
        MainWindow.NAV_ITEMS = tuple(items)

    original_factory = MainWindow._factory

    def factory(self, key: str):
        if key == "vpn_activity":
            from linkvideo_vpn_helper.ui.pages.vpn_activity_page import VPNActivityPage
            return VPNActivityPage(self.settings, self)
        return original_factory(self, key)

    MainWindow._factory = factory
    _INSTALLED = True
