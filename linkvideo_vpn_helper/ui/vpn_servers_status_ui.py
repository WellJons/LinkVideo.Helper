from __future__ import annotations

"""Small UI corrections for VPN automation/lifecycle status."""


_INSTALLED = False


def _refresh_automation_cells(page) -> None:
    table = getattr(page, "table", None)
    statuses = getattr(page, "_automation_by_host", {}) or {}
    if table is None:
        return

    for row in range(table.rowCount()):
        host_item = table.item(row, 0)
        status_item = table.item(row, 7)
        if host_item is None or status_item is None:
            continue
        auto = statuses.get(host_item.text())
        if auto is None:
            continue

        # AutomationStatus.state_text already contains the quarantine state.
        status_item.setText(auto.state_text)
        if auto.installed and not auto.paused:
            status_item.setToolTip(
                "LV-Aging запускается раз в сутки в 03:20: 30+ дней — спящая, "
                "90+ — PPP Secret отключается, 365+ — удаляются PPP Secret, его NAT и отдельный неиспользуемый профиль."
                if auto.aging_enabled
                else
                "LV-Aging выключен. Карантин и автоматическое удаление не выполняются."
            )
        else:
            status_item.setToolTip("")


def install_vpn_servers_status_ui() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from PySide6.QtWidgets import QLabel
    from linkvideo_vpn_helper.ui.pages.vpn_servers_page import VPNServersPage
    from linkvideo_vpn_helper.ui.pages.inactive_clients_page import InactiveClientsPage

    original_build = VPNServersPage._build
    original_on_stats = VPNServersPage._on_stats
    original_inactive_build = InactiveClientsPage._build

    def patched_build(self):
        original_build(self)
        for label in self.findChildren(QLabel):
            text = label.text()
            if "Кандидат в архив — 365+ дней" in text:
                label.setText("<b>Автоудаление — 365+ дней</b> — удаляется PPP-учётка, её NAT и отдельный неиспользуемый профиль")
            elif "Активность неизвестна" in text and "автоматика не отключает" in text:
                label.setText("<b>Активность неизвестна</b> — для never-active годовой отсчёт идёт от даты создания/первой LV-метки")
            elif "Отключена вручную" in text and "автовосстановление запрещено" in text:
                label.setText("<b>Отключена вручную</b> — автовосстановление запрещено; автоудаление после 365 дней без активности сохраняется")

    def patched_inactive_build(self):
        original_inactive_build(self)
        for label in self.findChildren(QLabel):
            text = label.text()
            if "кандидаты в архив 365+" in text:
                label.setText(
                    "Состояния VPN-учёток: активные, спящие 30+ дней, автоматический карантин 90+, "
                    "автоудаление 365+, ручные отключения и записи без подтверждённой активности."
                )

    def patched_on_stats(self, rows):
        original_on_stats(self, rows)
        _refresh_automation_cells(self)

    VPNServersPage._build = patched_build
    VPNServersPage._on_stats = patched_on_stats
    InactiveClientsPage._build = patched_inactive_build
    _INSTALLED = True
