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

    @property
    def session_token(self) -> str:
        """Transient bearer secret; never persist or log."""
        return self._token

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

        self.connected_row = QWidget()
        connected_layout = QHBoxLayout(self.connected_row)
        connected_layout.setContentsMargins(0, 0, 0, 0)
        self.connected_account = QLabel("")
        self.connected_account.setObjectName("Muted")
        self.change_account = QPushButton("Сменить вход")
        self.change_account.setObjectName("BillingChangeAccount")
        self.change_account.clicked.connect(self._logout)
        connected_layout.addWidget(self.connected_account)
        connected_layout.addStretch(1)
        connected_layout.addWidget(self.change_account)
        root.addWidget(self.connected_row)
        self.connected_row.hide()

        self.login_form = QWidget()
        form = QVBoxLayout(self.login_form)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        root.addWidget(self.login_form)

        explanation = QLabel(
            "Используйте корпоративные данные, как при входе в B2O. "
            "Логин можно взять из уже подключённого B2O."
        )
        explanation.setObjectName("TinyMuted")
        explanation.setWordWrap(True)
        form.addWidget(explanation)

        stored = self.store.load()
        fields = QHBoxLayout()
        fields.setSpacing(9)
        self.username = QLineEdit(stored.username or self.store.b2o_login_name())
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
        form.addLayout(fields)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.remember = QCheckBox("Запомнить через Windows DPAPI")
        self.remember.setObjectName("BillingRemember")
        self.remember.setChecked(True)
        self.show_password = QCheckBox("Показать")
        self.show_password.toggled.connect(lambda checked: self.password.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        ))
        self.use_saved = QPushButton("Логин из B2O")
        self.use_saved.setObjectName("BillingReuseHelperLogin")
        self.use_saved.setToolTip(
            "Подставить логин сотрудника из B2O. Пароли B2O не хранятся."
        )
        self.use_saved.clicked.connect(self._reuse_saved_credentials)
        buttons.addWidget(self.remember)
        buttons.addWidget(self.show_password)
        buttons.addStretch(1)
        buttons.addWidget(self.use_saved)
        form.addLayout(buttons)

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
        form.addLayout(footer)

    def _credentials_edited(self, *_):
        if self._token:
            self._drop_session()
            self.status.set_status("Не подключён", "neutral")
            self.message.setText("Данные изменены. Войдите заново для новой сессии.")

    def _reuse_saved_credentials(self):
        if self._busy:
            return
        login = self.store.b2o_login_name()
        if not login:
            self.message.setText("Сохранённый логин B2O не найден.")
            return
        self._drop_session()
        self.username.setText(login)
        self.password.clear()
        self.message.setText(
            "Логин из B2O подставлен. Введите корпоративный пароль и нажмите «Войти»."
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
        self.status.set_status("Bиллинг подключён", "success")
        self.message.setText("Вход выполнен.")
        self.connected_account.setText(self.username.text().strip())
        self.login_form.hide()
        self.connected_row.show()

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
        self.connected_row.hide()
        self.login_form.show()
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
        self.connected_row.hide()
        self.login_form.show()
        self.username.clear()
        self.password.clear()
        self.remember.setChecked(False)
        self.status.set_status("Не подключён", "neutral")
        self.message.setText("Локально сохранённые данные CRM очищены.")
