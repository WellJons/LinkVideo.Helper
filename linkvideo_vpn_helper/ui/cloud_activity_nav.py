from __future__ import annotations


_INSTALLED = False


def install_cloud_activity_nav() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.main_window import MainWindow

    if not any(item[0] == "vpn_activity" for item in MainWindow.NAV_ITEMS):
        items = list(MainWindow.NAV_ITEMS)
        insert_at = next((index + 1 for index, item in enumerate(items) if item[0] == "vpn_servers"), len(items))
        items.insert(insert_at, ("vpn_activity", "≡", "История VPN", "MikroTik, синхронизация и действия сотрудников"))
        MainWindow.NAV_ITEMS = tuple(items)

    original_factory = MainWindow._factory

    def factory(self, key: str):
        if key == "vpn_activity":
            from linkvideo_vpn_helper.ui.pages.vpn_activity_page import VPNActivityPage
            return VPNActivityPage(self.settings, self)
        return original_factory(self, key)

    MainWindow._factory = factory
    _INSTALLED = True
