from __future__ import annotations

"""B2O operator client form. Writes restricted to the captured RU payload."""

import re
import secrets
import string
import threading

from PySide6.QtCore import QSettings, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.b2o_client_admin import (
    B2OClientDraft, B2OCreationOutcomeUnknown, B2OCreationRejected,
    B2OOperatorClientManager, operator_admin_url, operator_for_country,
)
from linkvideo_vpn_helper.ui.components import Card, StatusPill


def safe_b2o_error_code(exc: Exception) -> str:
    """Expose only a numeric HTTP status, not API response or credentials."""
    found = re.search(r"\bHTTP\s+(\d{3})\b", str(exc))
    return f"HTTP {found.group(1)}" if found else type(exc).__name__


def client_credentials_message(login: str, password: str) -> str:
    """Plain-text message for the customer; shown only after confirmed POST."""
    return (
        "Создали вам аккаунт:\n"
        f"Логин: {login}\n"
        f"Пароль: {password}\n\n"
        "Данные можете поменять после того, как авторизуетесь в личном кабинете."
    )


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
        self._processed_logins: set[str] = set()
        self._pending_draft: B2OClientDraft | None = None
        self._result_message = ""
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
        self.in_billing.setChecked(True)
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
        self.generate_password = QPushButton("Создать пароль (9 символов)")
        self.generate_password.setObjectName("B2OGeneratePassword")
        self.generate_password.clicked.connect(self._generate_password)
        password_row.addWidget(self.generate_password)
        self.show_password = QCheckBox("Показать")
        self.show_password.setObjectName("B2OShowGeneratedPassword")
        self.show_password.toggled.connect(self._toggle_password_visibility)
        password_row.addWidget(self.show_password)
        self.phone = QLineEdit()
        self.phone.setObjectName("B2ONewClientPhone")
        self.phone.setPlaceholderText("Необязательно")
        self.account_type = QComboBox()
        self.account_type.setObjectName("B2OCreateAccountType")
        # Successful browser creation sent account_type="1". The original UI
        # offers "Обычный" and "Бизнес"; business numeric ID has NOT been seen.
        self.account_type.addItem("Обычный", "1")
        self.account_type.addItem("Бизнес", "business-unverified")
        self.account_type.setCurrentIndex(0)
        self.account_type.setToolTip(
            "Для «Обычного» используется подтверждённый код 1. "
            "Для «Бизнеса» нужен пример запроса с выбранным типом."
        )
        for label, field in (
            ("Имя *", self.name), ("Логин *", self.login),
            ("Пароль *", None), ("Номер телефона", self.phone),
            ("Тип аккаунта", self.account_type),
        ):
            fields.addRow(label, password_row if field is None else field)
        form.addLayout(fields)

        self.message = QLabel(
            "Для «Обычного» клиента применяется подтверждённый код 1. "
            "Код «Бизнеса» пока не показан в Network; создавать его наугад нельзя. "
            "При HTTP 400 проверьте также, не остался ли логин в биллинге."
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
        # Place credentials directly below the create controls, not as a large
        # separate panel far below the form.
        self.result_card = Card(subtle=True)
        self.result_card.setObjectName("B2OCreatedClientResult")
        result_layout = QVBoxLayout(self.result_card)
        result_layout.setContentsMargins(14, 12, 14, 12)
        result_layout.setSpacing(7)
        result_header = QHBoxLayout()
        result_title = QLabel("Данные для отправки клиенту")
        result_title.setObjectName("CardTitle")
        result_header.addWidget(result_title)
        result_header.addStretch(1)
        self.copy_credentials = QPushButton("Скопировать")
        self.copy_credentials.setObjectName("B2OCopyCreatedClient")
        self.copy_credentials.setToolTip("Скопировать готовое сообщение с логином и паролем")
        self.copy_credentials.setMinimumHeight(36)
        self.copy_credentials.clicked.connect(self._copy_created_credentials)
        result_header.addWidget(self.copy_credentials)
        result_layout.addLayout(result_header)
        self.result_text = QTextEdit()
        self.result_text.setObjectName("B2OCreatedClientCopyText")
        self.result_text.setReadOnly(True)
        self.result_text.setMinimumHeight(105)
        self.result_text.setMaximumHeight(122)
        result_layout.addWidget(self.result_text)
        self.result_card.hide()
        form.addWidget(self.result_card)
        root.addWidget(card)
        root.addStretch(1)
        self.country.currentIndexChanged.connect(self._refresh_create_enabled)
        self.in_billing.toggled.connect(self._refresh_create_enabled)
        self.cross_share.toggled.connect(self._refresh_create_enabled)
        self.account_type.currentIndexChanged.connect(self._account_type_changed)
        for field in (self.name, self.login, self.password, self.phone):
            field.textChanged.connect(self._refresh_create_enabled)
        self.login.textChanged.connect(self._reset_creation_lock_for_new_login)
        self._refresh_create_enabled()

    def _country_changed(self, *_):
        self._attempt += 1
        self.access_status.set_status("Доступ не проверен", "neutral")

    def _account_type_changed(self, *_):
        if self.account_type.currentData() == "business-unverified":
            self.message.setText(
                "Для «Бизнеса» пока не подтверждён код account_type. "
                "Нужен Payload успешного создания такого клиента в B2O. "
                "До этого отправка отключена, чтобы не создать неправильный тип."
            )
        else:
            self.message.setText(
                "Выбран «Обычный» (код 1, ранее получен успешный ответ B2O). "
                "Если сервер вернёт HTTP 400, смотрите подсказку о проверке полей."
            )
        self._refresh_create_enabled()

    def _reset_creation_lock_for_new_login(self, *_):
        # A distinct login is required before a second POST after success/uncertainty.
        self._created = False
        self._outcome_unknown = False
        self.create_button.setText("Создать клиента у оператора")
        self._refresh_create_enabled()

    def _refresh_create_enabled(self, *_):
        if not hasattr(self, "create_button"):
            return
        allowed = not (self._creating or self._created or self._outcome_unknown)
        if allowed:
            try:
                draft = self.draft()
                draft.verified_payload()
                allowed = draft.login.casefold() not in self._processed_logins
            except ValueError:
                allowed = False
        self.create_button.setEnabled(allowed)

    def _start_create(self):
        if self._creating or self._created or self._outcome_unknown:
            return
        try:
            draft = self.draft()
            draft.verified_payload()
            if draft.login.casefold() in self._processed_logins:
                raise ValueError("Для этого логина уже была отправка. Проверьте его в B2O.")
        except ValueError as exc:
            self.message.setText(str(exc))
            self._refresh_create_enabled()
            return
        token = self.b2o.token()
        if not token:
            self.access_status.set_status("Нужен вход B2O", "warning")
            self.message.setText(
                "Нет активной сессии B2O. Статус «Биллинг подключён» "
                "не означает вход в B2O. Авторизуйтесь в B2O "
                "через раздел архива и повторите создание."
            )
            return
        confirmation = QMessageBox.question(
            self, "Создание клиента B2O",
            f"Создать клиента {draft.login} в B2O и включить создание в биллинге? "
            "Операция будет отправлена один раз.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return
        self._creating = True
        self._pending_draft = draft
        self._result_message = ""
        self.result_text.clear()
        self.result_card.hide()
        self._set_form_busy(True)
        self._refresh_create_enabled()
        self.message.setText("Проверяю отдельную авторизацию B2O перед созданием…")

        def work():
            try:
                # GET does not grant write permission, but detects expired B2O
                # sessions before a non-idempotent POST is sent.
                self.api.check_access(draft.country, token=token)
            except Exception as exc:
                self.createReady.emit(None, "preflight:" + safe_b2o_error_code(exc))
                return
            try:
                result = self.api.create_client(draft, token=token)
                self.createReady.emit(result, None)
            except B2OCreationOutcomeUnknown:
                self.createReady.emit(None, "unknown")
            except B2OCreationRejected as exc:
                self.createReady.emit(None, str(exc))
            except PermissionError:
                self.createReady.emit(None, "auth")
            except ValueError:
                self.createReady.emit(None, "invalid")
            except Exception:
                # Unexpected errors after the POST might hide a real creation.
                self.createReady.emit(None, "unknown")

        threading.Thread(target=work, name="b2o-client-create", daemon=True).start()

    def _on_created(self, result: object, error: object):
        submitted = self._pending_draft
        self._pending_draft = None
        self._creating = False
        self._set_form_busy(False)
        if result is not None:
            self._created = True
            if submitted is not None:
                self._processed_logins.add(submitted.login.casefold())
                self._result_message = client_credentials_message(
                    submitted.login, submitted.password
                )
                self.result_text.setPlainText(self._result_message)
                self.copy_credentials.setText("Скопировать")
                self.result_card.show()
            self.create_button.setText("✓ Клиент создан")
            if result.read_back_verified:
                self.message.setText(
                    f"Клиент создан в B2O: ID {result.id_account}. "
                    "Проверочный GET подтвердил логин клиента."
                )
            else:
                self.message.setText(
                    f"POST подтвердил создание клиента: ID {result.id_account}. "
                    "Не удалось сверить данные GET — проверьте в админке. "
                    "Повторно создавать клиента не нужно."
                )
            self.access_status.set_status("Создан", "success")
        elif error == "unknown":
            self._outcome_unknown = True
            if submitted is not None:
                self._processed_logins.add(submitted.login.casefold())
            self.message.setText(
                "Результат POST неизвестен. Клиент мог быть создан. "
                "Проверьте логин в админке B2O перед любой повторной отправкой."
            )
            self.access_status.set_status("Проверьте B2O", "warning")
        elif isinstance(error, str) and error.startswith("preflight:"):
            code = error.split(":", 1)[1]
            self.message.setText(
                f"Проверка доступа B2O не прошла ({code}). POST не отправлялся. "
                "Войдите в B2O через раздел архива и нажмите «Проверить B2O». "
                "Статус нового биллинга к этому входу не относится."
            )
            self.access_status.set_status("Нет доступа B2O", "warning")
        else:
            if isinstance(error, str) and error.startswith("HTTP "):
                message = error
            elif error == "auth":
                message = "Требуется отдельная авторизация B2O"
            else:
                message = "Локальная проверка данных не пройдена"
            self.message.setText(
                f"Создать клиента не удалось: {message}. "
                "Проверьте настройки B2O и существование логина. "
                "Автоматического повтора POST нет."
            )
            self.access_status.set_status("Не создан", "warning")
        self._refresh_create_enabled()

    def _set_form_busy(self, busy: bool):
        for field in (
            self.country, self.in_billing, self.cross_share, self.name,
            self.login, self.password, self.phone, self.account_type,
            self.generate_password, self.show_password, self.check_access,
        ):
            field.setEnabled(not busy)

    def _toggle_password_visibility(self, visible: bool):
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self.password.setEchoMode(mode)

    def _copy_created_credentials(self):
        if not self._result_message or not self._created:
            return
        QApplication.clipboard().setText(self._result_message)
        self.copy_credentials.setText("✓ Скопировано")

    def _generate_password(self):
        # Nine alphanumeric characters: easier to type, still generated with
        # cryptographic randomness and a letter/number mix.
        alphabet = string.ascii_letters + string.digits
        while True:
            value = "".join(secrets.choice(alphabet) for _ in range(9))
            if (any(ch.islower() for ch in value)
                    and any(ch.isupper() for ch in value)
                    and any(ch.isdigit() for ch in value)):
                break
        self.password.setText(value)

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
                # Do not expose response bodies, headers or the B2O token.
                self.accessReady.emit(attempt, country, False, safe_b2o_error_code(exc))

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
