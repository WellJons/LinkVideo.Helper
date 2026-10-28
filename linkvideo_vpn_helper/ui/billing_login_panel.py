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
    sessionChanged = Signal(bool)
    def __init__(self, settings: QSettings | None = None, parent=None, *,
                 login_checker=None, credential_store: BillingCredentialStore | None = None):
        super().__init__(kind="hero", parent=parent)
        self.setProperty("billingLoginPanel", True)
        self.settings = settings if settings is not None else QSettings("LinkVideo", "LinkVideo.Helper")
        self.store = credential_store or BillingCredentialStore(self.settings)
        self.checker = login_checker or check_billing_login
        self._attempt = 0
        self._busy = False
        self._token = ""
        self._events = _LoginEvents(self)
        self._events.done.connect(self._on_login_done)
        self._build()

    @property
    def login_verified(self) -> bool:
        return bool(self._token)

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
        self.username.textEdited.connect(self._credentials_edited)
        self.password.textEdited.connect(self._credentials_edited)
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
            "Укажите корпоративный логин и пароль. После входа токен "
            "будет храниться только в памяти до выхода или закрытия Helper."
        )
        self.message.setObjectName("TinyMuted")
        self.message.setWordWrap(True)
        footer.addWidget(self.message, 1)
        self.clear_btn = QPushButton("Очистить данные")
        self.clear_btn.setProperty("role", "soft")
        self.clear_btn.clicked.connect(self._clear_credentials)
        self.logout_btn = QPushButton("Выйти")
        self.logout_btn.setObjectName("BillingLogoutButton")
        self.logout_btn.setEnabled(False)
        self.logout_btn.clicked.connect(self._logout)
        self.submit = QPushButton("Войти")
        self.submit.setObjectName("BillingLoginSubmit")
        self.submit.setProperty("role", "primary")
        self.submit.clicked.connect(self._login)
        footer.addWidget(self.clear_btn)
        footer.addWidget(self.logout_btn)
        footer.addWidget(self.submit)
        root.addLayout(footer)

    def _credentials_edited(self, *_):
        if self._token:
            self._drop_session()
            self.status.set_status("Не подключён", "neutral")
            self.message.setText("Данные изменены. Войдите заново для новой сессии.")

    def _reuse_saved_credentials(self):
        if self._busy:
            return
        old = self.store.helper_credentials()
        if not old.username or not old.password:
            self.message.setText("В Helper нет сохранённых данных для переноса.")
            return
        self._drop_session()
        self.username.setText(old.username)
        self.password.setText(old.password)
        self.message.setText(
            "Данные подставлены из Helper. Для CRM они ещё не проверены. "
            "Нажмите «Войти»."
        )

    def _set_busy(self, busy: bool):
        self._busy = busy
        for widget in (self.username, self.password, self.remember,
                       self.submit, self.clear_btn, self.use_saved, self.logout_btn):
            widget.setEnabled(not busy)
        self.submit.setText("Выполняется вход…" if busy else "Войти")
        self.logout_btn.setEnabled(not busy and bool(self._token))

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
        self._drop_session()
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
        token = getattr(outcome, "token", "")
        if not isinstance(token, str) or not token.strip():
            self.status.set_status("Вход не выполнен", "danger")
            self.message.setText("Сервер не выдал токен. Проверьте данные для входа.")
            return
        self._token = token
        self.logout_btn.setEnabled(True)
        self.sessionChanged.emit(True)
        try:
            self.store.save(
                self.username.text(), self.password.text(),
                remember=self.remember.isChecked(),
            )
        except Exception:
            self.status.set_status("Вход без сохранения", "warning")
            self.message.setText(
                "Пароль подтверждён, но Windows DPAPI не сохранил данные. "
                "Токен действует только в текущем окне."
            )
            return
        self.status.set_status("Вход выполнен", "success")
        self.message.setText(
            "CRM выдала токен входа. Он хранится только в памяти. "
            "Для загрузки клиентов и тарифов осталось подтвердить "
            "заголовок авторизации последующих запросов."
        )

    def _drop_session(self):
        was_verified = bool(self._token)
        self._token = ""
        if was_verified:
            self.sessionChanged.emit(False)
        self.logout_btn.setEnabled(False)

    def _logout(self):
        if self._busy:
            return
        self._attempt += 1
        self._drop_session()
        self.status.set_status("Не подключён", "neutral")
        self.message.setText(
            "Вы вышли из CRM в Helper. Сохранённые данные остались "
            "на этом компьютере, если было выбрано «Запомнить»."
        )

    def _clear_credentials(self):
        if self._busy:
            return
        self._attempt += 1
        self._drop_session()
        self.store.clear()
        self.username.clear()
        self.password.clear()
        self.remember.setChecked(False)
        self.status.set_status("Не подключён", "neutral")
        self.message.setText("Локально сохранённые данные CRM очищены.")
