from __future__ import annotations

"""Keep the simplified VPN Servers layout without removing LV management.

The operator polish intentionally hides duplicate lifecycle metrics and the long
legend, but installation/update and runtime controls are real operational tools.
This final wrapper makes those controls visible after all legacy/Sheets UI
wrappers have been composed.
"""

from PySide6.QtWidgets import QLabel


_INSTALLED = False


def install_vpn_servers_lv_controls_restore() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.pages.vpn_servers_page import VPNServersPage

    previous_build = VPNServersPage._build

    def build(self):
        previous_build(self)

        # Fleet-wide controls remain part of the supported Helper workflow.
        install_all = getattr(self, "install_all_btn", None)
        if install_all is not None:
            install_all.setText("Установить / обновить LV на всех")
            install_all.show()

        start_all = getattr(self, "start_all_btn", None)
        if start_all is not None:
            start_all.setText("Запустить LV на всех")
            start_all.show()

        stop_all = getattr(self, "stop_all_btn", None)
        if stop_all is not None:
            stop_all.setText("Остановить LV на всех")
            stop_all.show()

        # The compact page may keep duplicate summary metrics/legend hidden, but
        # it must not claim that RouterOS LV management moved away from Helper.
        for label in self.findChildren(QLabel):
            text = str(label.text() or "")
            if text == "Нагрузка и состояние VPN-серверов.":
                label.setText("Нагрузка, состояние VPN-серверов и управление LV-скриптами.")

    VPNServersPage._build = build
    _INSTALLED = True
