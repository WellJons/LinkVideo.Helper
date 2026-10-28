from __future__ import annotations

"""B2O operator client form. Writes restricted to the captured RU payload."""

import secrets
import string
import threading

from PySide6.QtCore import QSettings, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.b2o_client_admin import (
    B2OClientDraft, B2OCreationOutcomeUnknown, B2OOperatorClientManager,
    operator_admin_url, operator_for_country,
)
from linkvideo_vpn_helper.ui.components import Card, StatusPill


class B2OClientCreationWidget(QWidget):
    accessReady = Signal(int, str, object, object)
    createReady = Signal(object, object)

    def __init__(self, settings: QSettings, parent=None, b2o_service=None):
        super().__init__(parent)
        self.settings = settings
        self.b2o = b2o_service or B2OService(settings)
        self.api = B2OOperatorClientManager(self.b2o)
        self._attempt = 0
        self._creating = False
        self._created = False
        self._outcome_unknown = False
        self.accessReady.connect(self._on_access)
        self.createReady.connect(self._on_created)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        card = Card(kind="hero")
        form = QVBoxLayout(card)
        form.setContentsMargins(20, 18, 20, 18)
        form.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Добавить клиента в админке B2O")
        title.setObjectName("SectionTitle")
        self.access_status = StatusPill("Доступ не проверен", "neutral")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.access_status)
        form.addLayout(header)

        hint = QLabel(
            "Клиент создаётся у выбранного оператора B2O. "
            "Это отдельная операция от создания VPN-учётки и поиска в CRM."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        form.addWidget(hint)

        countries = QHBoxLayout()
        countries.setSpacing(10)
        self.country = QComboBox()
        self.country.setObjectName("B2OCreateCountry")
        for name in ("Россия", "Казахстан", "Беларусь"):
            self.country.addItem(name, operator_for_country(name))
        self.country.setMinimumHeight(44)
        self.country.currentIndexChanged.connect(self._country_changed)
        self.check_access = QPushButton("Проверить B2O")
        self.check_access.setObjectName("B2OCheckOperatorAccess")
        self.check_access.clicked.connect(self._check_access)
        countries.addWidget(self.country, 1)
        countries.addWidget(self.check_access)
        form.addLayout(countries)

        switches = QHBoxLayout()
        self.in_billing = QCheckBox("Завести в биллинг")
        self.in_billing.setObjectName("B2OCreateInBilling")
        self.cross_share = QCheckBox("Разрешить общий доступ к камерам операторов")
        self.cross_share.setObjectName("B2OCreateCrossShare")
        switches.addWidget(self.in_billing)
        switches.addWidget(self.cross_share)
        switches.addStretch(1)
        form.addLayout(switches)

        fields = QFormLayout()
        fields.setSpacing(11)
        self.name = QLineEdit()
        self.name.setObjectName("B2ONewClientName")
        self.name.setPlaceholderText("Имя клиента")
        self.login = QLineEdit()
        self.login.setObjectName("B2ONewClientLogin")
        self.login.setPlaceholderText("Логин клиента")
        self.password = QLineEdit()
        self.password.setObjectName("B2ONewClientPassword")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Пароль клиента")
        password_row = QHBoxLayout()
        password_row.setSpacing(8)
        password_row.addWidget(self.password, 1)
        self.generate_password = QPushButton("Создать пароль")
        self.generate_password.setObjectName("B2OGeneratePassword")
        self.generate_password.clicked.connect(self._generate_password)
        password_row.addWidget(self.generate_password)
        self.phone = QLineEdit()
        self.phone.setObjectName("B2ONewClientPhone")
        self.phone.setPlaceholderText("Необязательно")
        self.account_type = QComboBox()
        self.account_type.setObjectName("B2OCreateAccountType")
        # Only type "1" was observed in a successful B2O POST.
        self.account_type.addItem("Выберите тип аккаунта", "")
        self.account_type.addItem("Тип аккаунта 1 (подтверждён)", "1")
        self.account_type.setCurrentIndex(0)
        for label, field in (
            ("Имя *", self.name), ("Логин *", self.login),
            ("Пароль *", None), ("Номер телефона", self.phone),
            ("Тип аккаунта *", self.account_type),
        ):
            fields.addRow(label, password_row if field is None else field)
        form.addLayout(fields)

        self.message = QLabel(
            "Создание через API подтверждено для России, типа 1, "
            "с включённым «Завести в биллинг» и выключенным общим доступом. "
            "Другие сочетания пока недоступны."
        )
        self.message.setObjectName("Muted")
        self.message.setWordWrap(True)
        form.addWidget(self.message)
        actions = QHBoxLayout()
        self.create_button = QPushButton("Создать клиента у оператора")
        self.create_button.setProperty("role", "primary")
        self.create_button.setObjectName("B2OCreateClient")
        self.create_button.setEnabled(False)
        self.create_button.clicked.connect(self._start_create)
        self.open_admin = QPushButton("Открыть админку B2O")
        self.open_admin.setObjectName("B2OOpenOperatorAdmin")
        self.open_admin.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(operator_admin_url()))
        )
        actions.addWidget(self.create_button, 1)
        actions.addWidget(self.open_admin)
        form.addLayout(actions)
        root.addWidget(card)
        root.addStretch(1)
        self.country.currentIndexChanged.connect(self._refresh_create_enabled)
        self.in_billing.toggled.connect(self._refresh_create_enabled)
        self.cross_share.toggled.connect(self._refresh_create_enabled)
        self.account_type.currentIndexChanged.connect(self._refresh_create_enabled)
        for field in (self.name, self.login, self.password, self.phone):
            field.textChanged.connect(self._refresh_create_enabled)
        self.login.textChanged.connect(self._reset_creation_lock_for_new_login)
        self._refresh_create_enabled()

    def _country_changed(self, *_):
        self._attempt += 1
        self.access_status.set_status("Доступ не проверен", "neutral")

    def _generate_password(self):
        alphabet = string.ascii_letters + string.digits + "@#_-"
        # Cryptographic randomness; no logging, clipboard or network side effect.
        self.password.setText("".join(secrets.choice(alphabet) for _ in range(18)))

    def draft(self) -> B2OClientDraft:
        return B2OClientDraft(
            country=self.country.currentText(),
            name=self.name.text().strip(),
            login=self.login.text().strip(),
            password=self.password.text(),
            phone=self.phone.text().strip(),
            account_type=str(self.account_type.currentData() or ""),
            create_in_billing=self.in_billing.isChecked(),
            cross_sharing=self.cross_share.isChecked(),
        )

    def _check_access(self):
        country = self.country.currentText()
        self._attempt += 1
        attempt = self._attempt
        token = self.b2o.token()
        if not token:
            self.access_status.set_status("Нужен вход B2O", "warning")
            self.message.setText(
                "Войдите в B2O через «Скачать архив» или «Диагностика архива». "
                "Для всех трёх операторов используется та же сессия B2O."
            )
            return
        self.check_access.setEnabled(False)
        self.access_status.set_status("Проверяю…", "neutral")

        def work():
            try:
                self.api.check_access(country, token=token)
                self.accessReady.emit(attempt, country, True, None)
            except Exception as exc:
                # Don't surface response bodies, headers or token in error UI.
                self.accessReady.emit(attempt, country, False, type(exc).__name__)

        threading.Thread(target=work, name="b2o-operator-access", daemon=True).start()

    def _on_access(self, attempt: int, country: str, ok: object, error: object):
        if attempt != self._attempt or country != self.country.currentText():
            return
        self.check_access.setEnabled(True)
        if ok:
            self.access_status.set_status("B2O доступен", "success")
            self.message.setText(
                "Доступ к настройкам оператора подтверждён. "
                "Право на создание клиентов этим GET-запросом не проверяется."
            )
        else:
            self.access_status.set_status("Нет подтверждения", "warning")
            self.message.setText(
                "Не удалось прочитать настройки B2O. "
                "Проверьте авторизацию и права сотрудника. "
                f"Тип ошибки: {str(error or 'unknown')}."
            )
