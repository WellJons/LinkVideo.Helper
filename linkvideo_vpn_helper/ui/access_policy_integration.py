from __future__ import annotations

"""UI policy for the shared AdminChats employee account."""

import time

from PySide6.QtWidgets import QLabel, QPushButton

from linkvideo_vpn_helper.services.access_policy import (
    is_employee_credentials,
    install_service_access_policy,
)
from linkvideo_vpn_helper.ui.dialogs import ConfirmDialog


_INSTALLED = False
_RESTRICTED_SECTIONS = {"inactive", "vpn_servers"}


def _is_employee_window(window) -> bool:
    credentials = getattr(window, "credentials", None)
    if credentials is not None:
        return is_employee_credentials(credentials)
    settings = getattr(window, "settings", None)
    if settings is not None:
        try:
            return str(settings.value("username", "", str) or "").strip().casefold() == "adminchats"
        except Exception:
            pass
    return False


def _employee_warning(page, action: str) -> None:
    task = getattr(page, "client_task", None) or getattr(page, "task", None)
    if task is not None:
        try:
            task.show()
            task.warning(
                "Только системный администратор",
                f"{action} доступно только системному администратору.",
            )
            return
        except Exception:
            pass


def _find_management_card(page):
    layout = getattr(page, "detail_l", None)
    if layout is None:
        return None
    for index in range(layout.count() - 1, -1, -1):
        item = layout.itemAt(index)
        widget = item.widget() if item is not None else None
        if widget is None:
            continue
        if any(str(label.text() or "").strip() == "Управление" for label in widget.findChildren(QLabel)):
            return widget
    return None


def _apply_client_controls(page) -> None:
    control = _find_management_card(page)
    if control is None:
        return

    if control.findChild(QPushButton, "ReconnectVPNButton") is None:
        reconnect = QPushButton("Переподключить VPN")
        reconnect.setObjectName("ReconnectVPNButton")
        reconnect.setToolTip("Завершить текущую VPN-сессию. Клиентский роутер должен подключиться снова автоматически.")
        reconnect.clicked.connect(page._disconnect)
        control.layout().addWidget(reconnect)

    if not is_employee_credentials(getattr(page, "credentials", None)):
        return

    # Employee mode keeps everyday support actions, but removes every operation
    # capable of breaking an existing client configuration.
    password_edit = getattr(page, "password_edit", None)
    if password_edit is not None:
        password_edit.hide()
    for attr in ("toggle_port_button", "remove_port_button"):
        widget = getattr(page, attr, None)
        if widget is not None:
            widget.hide()

    forbidden_exact = {
        "Сменить пароль",
        "Вкл./выкл. учётку",
        "Удалить клиента",
    }
    for button in control.findChildren(QPushButton):
        text = str(button.text() or "").strip()
        if text in forbidden_exact or text.startswith("Отключить порт ") or text.startswith("Включить порт ") or text.startswith("Удалить порт "):
            button.hide()

    if control.findChild(QLabel, "AdminChatsPolicyNote") is None:
        note = QLabel(
            "Режим сотрудника AdminChats: смена пароля, отключение/включение и удаление портов или учётки "
            "доступны только системному администратору."
        )
        note.setObjectName("AdminChatsPolicyNote")
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        control.layout().addWidget(note)


def install_access_policy() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    install_service_access_policy()

    from linkvideo_vpn_helper.ui.main_window import MainWindow
    from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage

    original_main_init = MainWindow.__init__
    original_go = MainWindow._go
    original_factory = MainWindow._factory
    original_render = SearchManagePage._render_client
    original_action = SearchManagePage._on_action

    def patched_main_init(self, *args, **kwargs):
        original_main_init(self, *args, **kwargs)
        if not _is_employee_window(self):
            # Personal RouterOS logins are system administrators.
            for label in getattr(self, "user_card", self).findChildren(QLabel):
                if str(label.text() or "").strip() == "MikroTik":
                    label.setText("Системный администратор")
                    break
            return

        queue = list(getattr(self, "_preload_queue", []) or [])
        self._preload_queue = [key for key in queue if key not in _RESTRICTED_SECTIONS]
        for key in _RESTRICTED_SECTIONS:
            entry = getattr(self, "nav", {}).get(key)
            if entry:
                entry[0].hide()
        sidebar = getattr(self, "sidebar", None)
        if sidebar is not None:
            for label in sidebar.findChildren(QLabel):
                text = str(label.text() or "").strip()
                if text == "ИНФРАСТРУКТУРА":
                    label.hide()
                elif text == "MikroTik":
                    label.setText("Сотрудник · AdminChats")
        user_card = getattr(self, "user_card", None)
        if user_card is not None:
            user_card.setToolTip("Ограниченный режим сотрудника AdminChats")

    def patched_go(self, key: str):
        if _is_employee_window(self) and key in _RESTRICTED_SECTIONS:
            toast = getattr(self, "toast", None)
            if toast is not None:
                toast.showMessage(
                    "Только системный администратор",
                    "Управление VPN-инфраструктурой и жизненным циклом учёток доступно только системному администратору.",
                    5000,
                )
                try:
                    self._position_toast()
                except Exception:
                    pass
            return
        return original_go(self, key)

    def patched_factory(self, key: str):
        if _is_employee_window(self) and key in _RESTRICTED_SECTIONS:
            raise PermissionError("Раздел доступен только системному администратору.")
        return original_factory(self, key)

    def patched_render(self):
        result = original_render(self)
        _apply_client_controls(self)
        return result

    def guard_ui(method_name: str, action: str):
        original = getattr(SearchManagePage, method_name)

        def guarded(self, *args, **kwargs):
            if is_employee_credentials(getattr(self, "credentials", None)):
                _employee_warning(self, action)
                return None
            return original(self, *args, **kwargs)

        setattr(SearchManagePage, method_name, guarded)

    def patched_disconnect(self):
        client = getattr(self, "current", None)
        if client is None:
            return
        if not client.is_online:
            task = getattr(self, "client_task", None) or getattr(self, "task", None)
            if task is not None:
                task.show()
                task.warning("VPN не подключён", "У клиента сейчас нет активной VPN-сессии для переподключения.")
            return
        dialog = ConfirmDialog(
            "Переподключить VPN",
            f"Завершить текущую VPN-сессию {client.login}? Клиентский роутер должен подключиться снова автоматически.",
            "Переподключить",
            False,
            self,
        )
        if not dialog.exec():
            return

        def operation():
            if not self.service.disconnect_client_session(client.server, self.credentials, client.login):
                raise ValueError("Активная VPN-сессия уже отсутствует")
            time.sleep(0.6)
            return self.service.get_client(client.server, self.credentials, client.login)

        self._run_action("reconnect", operation)

    def patched_action(self, result, error, name: str):
        outcome = original_action(self, result, error, name)
        if name == "reconnect" and error is None:
            task = getattr(self, "client_task", None) or getattr(self, "task", None)
            if task is not None:
                task.show()
                task.done(
                    "VPN-соединение перезапускается",
                    "Текущая сессия завершена. Клиентский роутер должен подключиться повторно автоматически.",
                )
        return outcome

    MainWindow.__init__ = patched_main_init
    MainWindow._go = patched_go
    MainWindow._factory = patched_factory
    SearchManagePage._render_client = patched_render
    SearchManagePage._disconnect = patched_disconnect
    SearchManagePage._on_action = patched_action

    guard_ui("_password_inline", "Смена пароля VPN-учётки")
    guard_ui("_toggle_selected_port", "Включение или отключение NAT-порта")
    guard_ui("_remove_selected_port", "Удаление NAT-порта")
    guard_ui("_toggle_account", "Включение или отключение VPN-учётки")
    guard_ui("_delete", "Удаление VPN-клиента")

    _INSTALLED = True
