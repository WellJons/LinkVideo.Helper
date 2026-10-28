from __future__ import annotations

"""Desktop CRM login form; opt-in reuse, separate DPAPI storage, no token leaks."""

import threading

from PySide6.QtCore import QSettings, QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.billing_auth_contract import BillingLoginRequest
from linkvideo_vpn_helper.services.billing_credential_store import BillingCredentialStore
from linkvideo_vpn_helper.services.billing_login_probe import check_billing_login
from linkvideo_vpn_helper.ui.components import Card, StatusPill


class _LoginEvents(QObject):
    done = Signal(int, object, object)


class BillingLoginPanel(Card):
    def __init__(self, settings: QSettings | None = None, parent=None, *,
                 login_checker=None, credential_store: BillingCredentialStore | None = None):
        super().__init__(kind="hero", parent=parent)
        self.setProperty("billingLoginPanel", True)
        self.settings = settings if settings is not None else QSettings("LinkVideo", "LinkVideo.Helper")
        self.store = credential_store or BillingCredentialStore(self.settings)
        self.checker = login_checker or check_billing_login
        self._attempt = 0
        self._busy = False
        self._events = _LoginEvents(self)
        self._events.done.connect(self._on_login_done)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(19, 17, 19, 17)
        root.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("Вход в новый биллинг")
        title.setObjectName("SectionTitle")
        self.status = StatusPill("Не подключён", "neutral")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.status)
        root.addLayout(header)

        explanation = QLabel(
            "Для CRM используются корпоративные данные. Их можно сохранить "
            "отдельно, под защитой Windows DPAPI. Вход в MikroTik остаётся независимым."
        )
        explanation.setObjectName("TinyMuted")
        explanation.setWordWrap(True)
        root.addWidget(explanation)

        stored = self.store.load()
        fields = QHBoxLayout()
        fields.setSpacing(9)
        self.username = QLineEdit(stored.username)
        self.username.setObjectName("BillingLoginUser")
        self.username.setPlaceholderText("Логин CRM")
        self.username.setMinimumHeight(42)
        self.password = QLineEdit(stored.password)
        self.password.setObjectName("BillingLoginPassword")
        self.password.setPlaceholderText("Пароль CRM")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setMinimumHeight(42)
        self.password.returnPressed.connect(self._login)
        fields.addWidget(self.username, 1)
        fields.addWidget(self.password, 1)
        root.addLayout(fields)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.remember = QCheckBox("Запомнить через Windows DPAPI")
        self.remember.setObjectName("BillingRemember")
        self.remember.setChecked(True)
        self.show_password = QCheckBox("Показать")
        self.show_password.toggled.connect(lambda checked: self.password.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        ))
        self.use_saved = QPushButton("Взять данные из Helper")
        self.use_saved.setObjectName("BillingReuseHelperLogin")
        self.use_saved.setToolTip(
            "Только по нажатию. Не отправляет пароль на сервер и не сохраняет его автоматически."
        )
        self.use_saved.clicked.connect(self._reuse_saved_credentials)
        buttons.addWidget(self.remember)
        buttons.addWidget(self.show_password)
        buttons.addStretch(1)
        buttons.addWidget(self.use_saved)
        root.addLayout(buttons)

        footer = QHBoxLayout()
        self.message = QLabel(
            "Сведения о токене или cookie пока не подтверждены: "
            "проверка входа не означает подключения API для просмотра тарифов."
        )
        self.message.setObjectName("TinyMuted")
        self.message.setWordWrap(True)
        footer.addWidget(self.message, 1)
        self.clear_btn = QPushButton("Очистить данные")
        self.clear_btn.setProperty("role", "soft")
        self.clear_btn.clicked.connect(self._clear_credentials)
        self.submit = QPushButton("Проверить вход")
        self.submit.setObjectName("BillingLoginSubmit")
        self.submit.setProperty("role", "primary")
        self.submit.clicked.connect(self._login)
        footer.addWidget(self.clear_btn)
        footer.addWidget(self.submit)
        root.addLayout(footer)

    def _reuse_saved_credentials(self):
        if self._busy:
            return
        old = self.store.helper_credentials()
        if not old.username or not old.password:
            self.message.setText("В Helper нет сохранённых данных для переноса.")
            return
        self.username.setText(old.username)
        self.password.setText(old.password)
        self.message.setText(
            "Данные подставлены из Helper. Для CRM они ещё не проверены. "
            "Нажмите «Проверить вход»."
        )

    def _set_busy(self, busy: bool):
        self._busy = busy
        for widget in (self.username, self.password, self.remember,
                       self.submit, self.clear_btn, self.use_saved):
            widget.setEnabled(not busy)
        self.submit.setText("Проверяю…" if busy else "Проверить вход")

    def _login(self):
        if self._busy:
            return
        try:
            request = BillingLoginRequest(self.username.text(), self.password.text())
        except Exception:
            self.status.set_status("Проверьте данные", "warning")
            self.message.setText("Введите логин и пароль корпоративного биллинга.")
            return
        self._attempt += 1
        attempt = self._attempt
        self._set_busy(True)
        self.status.set_status("Проверка…", "info")

        def worker():
            try:
                response = self.checker(request)
                self._events.done.emit(attempt, response, None)
            except Exception:
                # Error values from network libraries may contain URLs or
                # credentials; no exception text is surfaced or logged.
                self._events.done.emit(attempt, None, "Сервер отклонил вход или недоступен.")
        threading.Thread(target=worker, name="crm-login-check", daemon=True).start()

    def _on_login_done(self, attempt: int, outcome, error):
        if attempt != self._attempt:
            return
        self._set_busy(False)
        if error is not None:
            self.status.set_status("Вход не подтверждён", "danger")
            self.message.setText(error)
            return
        try:
            self.store.save(self.username.text(), self.password.text(), remember=self.remember.isChecked())
        except Exception:
            self.status.set_status("Не сохранён", "warning")
            self.message.setText(
                "Сервер ответил на проверку, но DPAPI-сохранение не выполнено. "
                "Уточните настройки Windows."
            )
            return
        self.status.set_status("Ответ получен", "info")
        fields = ", ".join(outcome.response_keys) if outcome else "—"
        self.message.setText(
            "CRM ответила на запрос входа. Формат сессии ещё не установлен; "
            f"имена полей ответа: {fields}. Доступ к API пока не активирован."
        )

    def _clear_credentials(self):
        if self._busy:
            return
        self._attempt += 1
        self.store.clear()
        self.username.clear()
        self.password.clear()
        self.remember.setChecked(False)
        self.status.set_status("Не подключён", "neutral")
        self.message.setText("Локально сохранённые данные CRM очищены.")
