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
    from linkvideo_vpn_helper.ui.dialogs import ConfirmDialog
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

    def patched_toggle_quarantine(self, host: str):
        auto = self._automation_by_host.get(host)
        if not auto or not auto.installed:
            self.task.show()
            self.task.warning("LV-автоматика не установлена", "Сначала установите LV на сервер.")
            return

        target = not auto.aging_enabled
        if target and auto.initialized <= 0:
            self.task.show()
            self.task.warning(
                "Последняя активность не инициализирована",
                "Сначала нажмите «Инициализировать активность». Карантин нельзя включать без служебных состояний.",
            )
            return

        if target:
            text = (
                f"Включить LV-Aging на {host}?\n\n"
                f"Сейчас: активные {auto.active}, спящие {auto.sleeping}, карантин {auto.quarantine}, "
                f"365+ дней {auto.archive}, отключены вручную {auto.manual}, активность неизвестна {auto.unknown}.\n\n"
                "Политика будет применена сразу и затем ежедневно в 03:20: 30+ дней — спящая; "
                "90+ дней — PPP Secret отключается; 365+ дней — PPP Secret удаляется вместе с его NAT, "
                "а отдельный неиспользуемый PPP Profile также удаляется. Учётки без единого подключения "
                "считаются от даты создания/начала LV-отсчёта.\n\n"
                "Ручное отключение запрещает автовосстановление, но не отменяет автоудаление после 365 дней."
            )
            title, button, danger = "Включить автоматическую политику", "Включить", True
        else:
            text = (
                f"Выключить LV-Aging на {host}?\n\n"
                "Уже отключённые PPP Secret останутся в текущем состоянии. Новые переходы в карантин и "
                "автоматические удаления по сроку выполняться не будут, пока LV-Aging снова не включат. "
                "Сбор активности и автовосстановление существующего карантина продолжат работать."
            )
            title, button, danger = "Выключить автоматическую политику", "Выключить", False

        dialog = ConfirmDialog(title, text, button, danger, self)
        if not dialog.exec():
            return
        self._run_mutation(
            "quarantine_on" if target else "quarantine_off",
            host,
            lambda h: self.automation.set_quarantine_enabled(h, self.credentials, target),
        )

    def patched_on_stats(self, rows):
        original_on_stats(self, rows)
        _refresh_automation_cells(self)

    VPNServersPage._build = patched_build
    VPNServersPage._on_stats = patched_on_stats
    VPNServersPage._toggle_quarantine = patched_toggle_quarantine
    InactiveClientsPage._build = patched_inactive_build
    _INSTALLED = True
