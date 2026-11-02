from __future__ import annotations

"""Telegram controls inside Settings — no bot secrets in QSettings."""

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.telegram_invites import InviteStore, state_database_path
from linkvideo_vpn_helper.services.telegram_service_runtime import (
    admin_action, clean_bot_link, configured, is_task_installed, public_bot_link,
)
from linkvideo_vpn_helper.ui.components import Card, StatusPill


class TelegramSettingsWidget(QWidget):
    operationReady = Signal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._buttons = []
        self._build()
        self.operationReady.connect(self._on_operation)
        self._refresh_state()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        settings = Card(kind="hero")
        layout = QVBoxLayout(settings)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        title = QLabel("Telegram-бот")
        title.setObjectName("SectionTitle")
        subtitle = QLabel(
            "Бот запускается отдельно от Helper через задачу Windows при старте компьютера. "
            "Закрытие программы и смена пользователя Windows его не останавливают."
        )
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.status = StatusPill("Проверяю", "neutral")
        layout.addWidget(self.status, 0, Qt.AlignmentFlag.AlignLeft)

        label_url = QLabel("Ссылка на бота")
        label_url.setObjectName("Value")
        layout.addWidget(label_url)
        self.url_input = QLineEdit()
        self.url_input.setObjectName("TelegramBotLink")
        self.url_input.setPlaceholderText("https://t.me/LinkVideoHelperBot")
        self.url_input.setText(public_bot_link())
        layout.addWidget(self.url_input)

        token_label = QLabel("API-токен BotFather (скрыт, сохраняется защищённо)")
        token_label.setObjectName("Value")
        layout.addWidget(token_label)
        self.token_input = QLineEdit()
        self.token_input.setObjectName("TelegramBotToken")
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_input.setPlaceholderText("Введите токен при первой настройке или смене")
        layout.addWidget(self.token_input)

        buttons = QHBoxLayout()
        self.save_button = QPushButton("Сохранить и включить бота")
        self.save_button.setObjectName("TelegramBotSetup")
        self.save_button.setProperty("role", "primary")
        self.save_button.clicked.connect(self._configure)
        buttons.addWidget(self.save_button)
        self.stop_button = QPushButton("Остановить бота")
        self.stop_button.setObjectName("TelegramBotStop")
        self.stop_button.clicked.connect(lambda: self._start("stop", {}))
        buttons.addWidget(self.stop_button)
        self.refresh_button = QPushButton("Проверить")
        self.refresh_button.clicked.connect(self._refresh_state)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch(1)
        for button in (self.save_button, self.stop_button, self.refresh_button):
            self._buttons.append(button)
        layout.addLayout(buttons)
        root.addWidget(settings)

        employees = Card()
        l = QVBoxLayout(employees)
        l.setContentsMargins(18, 16, 18, 16)
        l.setSpacing(10)
        name = QLabel("Доступ сотрудников")
        name.setObjectName("SectionTitle")
        l.addWidget(name)
        hint = QLabel(
            "Можно добавить сотрудника по числовому Telegram ID или выдать "
            "одноразовую ссылку. При переходе по ссылке бот запоминает пользователя сам. "
            "@username не позволяет получить Telegram ID без его обращения к боту."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        l.addWidget(hint)
        controls = QHBoxLayout()
        self.employee_id = QLineEdit()
        self.employee_id.setObjectName("TelegramEmployeeId")
        self.employee_id.setPlaceholderText("Числовой Telegram ID сотрудника")
        controls.addWidget(self.employee_id, 1)
        self.add_button = QPushButton("Добавить")
        self.add_button.setProperty("role", "primary")
        self.add_button.clicked.connect(lambda: self._employee_action("add"))
        controls.addWidget(self.add_button)
        self.remove_button = QPushButton("Удалить")
        self.remove_button.clicked.connect(lambda: self._employee_action("remove"))
        controls.addWidget(self.remove_button)
        self._buttons.extend((self.add_button, self.remove_button))
        l.addLayout(controls)
        self.employee_list = QLabel()
        self.employee_list.setWordWrap(True)
        self.employee_list.setObjectName("Muted")
        l.addWidget(self.employee_list)
        invite_row = QHBoxLayout()
        self.invite_button = QPushButton("Создать ссылку-приглашение")
        self.invite_button.setObjectName("TelegramCreateInvite")
        self.invite_button.clicked.connect(lambda: self._start("invite", {}))
        self._buttons.append(self.invite_button)
        invite_row.addWidget(self.invite_button)
        invite_row.addStretch(1)
        l.addLayout(invite_row)
        self.generated_link = QLineEdit()
        self.generated_link.setObjectName("TelegramInvitationLink")
        self.generated_link.setReadOnly(True)
        self.generated_link.setPlaceholderText("Здесь появится одноразовая ссылка")
        l.addWidget(self.generated_link)
        root.addWidget(employees)

        self.feedback = QLabel("")
        self.feedback.setObjectName("Muted")
        self.feedback.setWordWrap(True)
        root.addWidget(self.feedback)
        note = QLabel(
            "Настройка требует однократного подтверждения администратора Windows. "
            "Токен не хранится в профилях сотрудников. "
            "Для поиска камер в автономном режиме необходим отдельный доступ "
            "службы к B2O — пользовательская сессия Helper не копируется."
        )
        note.setObjectName("TinyMuted")
        note.setWordWrap(True)
        root.addWidget(note)
        root.addStretch(1)

    def _configure(self):
        try:
            link = clean_bot_link(self.url_input.text())
            token = self.token_input.text().strip()
            if not token and not configured():
                raise ValueError("При первой настройке требуется токен BotFather.")
        except ValueError as exc:
            self.feedback.setText(str(exc))
            return
        self._start("configure", {"link": link, "token": token})

    def _employee_action(self, operation: str):
        text = self.employee_id.text().strip()
        if not text.isascii() or not text.isdecimal() or int(text or "0") <= 0:
            self.feedback.setText("Введите числовой Telegram ID сотрудника.")
            return
        self._start(operation, {"telegram_id": int(text)})

    def _start(self, operation: str, payload: dict):
        if self._busy:
            return
        self._busy = True
        self.status.set_status("Применяю", "warning")
        self.feedback.setText("Ожидаю подтверждения администратора Windows…")
        for button in self._buttons:
            button.setEnabled(False)

        def worker():
            try:
                result = admin_action({"operation": operation, **payload})
                self.operationReady.emit(operation, result, "")
            except Exception:
                self.operationReady.emit(
                    operation, "", "Операция отменена или не выполнена. "
                    "Проверьте права администратора Windows."
                )

        threading.Thread(target=worker, name="telegram-settings-action", daemon=True).start()

    def _on_operation(self, operation: str, message: str, error: str):
        self._busy = False
        for button in self._buttons:
            button.setEnabled(True)
        self.feedback.setText(error or message)
        if not error:
            if operation == "configure":
                self.token_input.clear()
                self.url_input.setText(public_bot_link())
            elif operation == "invite":
                self.generated_link.setText(message)
            elif operation in ("add", "remove"):
                self.employee_id.clear()
        self._refresh_state()

    def _refresh_state(self):
        if configured() and is_task_installed():
            self.status.set_status("Автозапуск настроен", "success")
        elif configured():
            self.status.set_status("Сохранён · не запущен", "warning")
        else:
            self.status.set_status("Не настроен", "neutral")
        self.url_input.setText(public_bot_link() or self.url_input.text())
        try:
            people = InviteStore(state_database_path()).members() if (
                state_database_path().exists()
            ) else ()
            self.employee_list.setText(
                "Разрешённые Telegram ID: " + ", ".join(map(str, people))
                if people else "Добавленных сотрудников пока нет."
            )
        except (OSError, PermissionError):
            self.employee_list.setText("Список сотрудников недоступен для чтения.")
