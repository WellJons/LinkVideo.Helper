from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from linkvideo_vpn_helper.services.server_registry import ServerRegistry
from linkvideo_vpn_helper.ui.components import Card, CounterControl, StatusPill


class ConfirmDialog(QDialog):
    def __init__(self, title: str, text: str, confirm_text: str = "Продолжить", danger: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)
        head = QLabel(title)
        head.setObjectName("SectionTitle")
        body = QLabel(text)
        body.setObjectName("Muted")
        body.setWordWrap(True)
        root.addWidget(head)
        root.addWidget(body)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Отмена")
        ok = QPushButton(confirm_text)
        ok.setProperty("role", "danger" if danger else "primary")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        root.addLayout(row)


class AddServerDialog(QDialog):
    def __init__(self, registry: ServerRegistry, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.server = None
        self.setWindowTitle("Добавить VPN-сервер")
        self.setMinimumWidth(500)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(14)
        title = QLabel("Добавить VPN-сервер")
        title.setObjectName("SectionTitle")
        info = QLabel("Сервер сохранится в LinkVideo.Helper и останется после обновлений. Страна определяется автоматически по имени сервера.")
        info.setObjectName("Muted")
        info.setWordWrap(True)
        self.host = QLineEdit()
        self.host.setPlaceholderText("Например: kz-vpn02.linkvideo.ru")
        self.host.textChanged.connect(self._host_changed)
        country_label = QLabel("Страна")
        country_label.setObjectName("CardTitle")
        self.country = QLineEdit("Россия")
        self.country.setPlaceholderText("Страна для отображения")
        self.country.textChanged.connect(self._preview)
        preview = Card(subtle=True)
        pl = QHBoxLayout(preview)
        pl.setContentsMargins(12, 10, 12, 10)
        self.preview_host = QLabel("—")
        self.preview_host.setObjectName("Value")
        self.preview_country = StatusPill("Россия", "neutral")
        pl.addWidget(self.preview_host, 1)
        pl.addWidget(self.preview_country)
        self.error = QLabel("")
        self.error.setObjectName("DangerText")
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Отмена")
        save = QPushButton("Добавить сервер")
        save.setProperty("role", "primary")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addWidget(title)
        root.addWidget(info)
        root.addWidget(self.host)
        root.addWidget(country_label)
        root.addWidget(self.country)
        root.addWidget(preview)
        root.addWidget(self.error)
        root.addLayout(buttons)
        self._host_changed()

    def _host_changed(self):
        host = self.registry.normalize_host(self.host.text())
        detected = self.registry.detect_country(host)
        self.country.blockSignals(True)
        self.country.setText(detected)
        self.country.blockSignals(False)
        self._preview()

    def _preview(self):
        host = self.registry.normalize_host(self.host.text())
        country = self.country.text().strip() or self.registry.detect_country(host)
        self.preview_host.setText(host or "Адрес сервера")
        self.preview_country.set_status(country, "info" if country != "Россия" else "neutral")
        self.error.clear()

    def _save(self):
        try:
            country = self.country.text().strip() or None
            self.server = self.registry.add(self.host.text(), country)
        except Exception as exc:
            self.error.setText(str(exc))
            return
        self.accept()


class CountDialog(QDialog):
    def __init__(self, title: str, label: str, value: int = 1, minimum: int = 1, maximum: int = 14, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(14)
        head = QLabel(title)
        head.setObjectName("SectionTitle")
        desc = QLabel(label)
        desc.setObjectName("Muted")
        desc.setWordWrap(True)
        self.counter = CounterControl(value, minimum, maximum)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Отмена")
        ok = QPushButton("Продолжить")
        ok.setProperty("role", "primary")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        root.addWidget(head)
        root.addWidget(desc)
        root.addWidget(self.counter)
        root.addLayout(row)

    def value(self) -> int:
        return self.counter.value()


class PasswordDialog(QDialog):
    def __init__(self, generated: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Смена пароля")
        self.setMinimumWidth(460)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(14)
        title = QLabel("Новый пароль")
        title.setObjectName("SectionTitle")
        info = QLabel("Пароль отображается открыто — его можно сверить перед применением.")
        info.setObjectName("Muted")
        self.input = QLineEdit(generated)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Отмена")
        save = QPushButton("Сменить пароль")
        save.setProperty("role", "primary")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(save)
        root.addWidget(title)
        root.addWidget(info)
        root.addWidget(self.input)
        root.addLayout(row)

    def password(self) -> str:
        return self.input.text().strip()


class B2OLoginDialog(QDialog):
    def __init__(self, saved_login: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Авторизация B2O")
        self.setMinimumWidth(470)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(14)
        title = QLabel("Авторизация B2O")
        title.setObjectName("SectionTitle")
        info = QLabel("Используется только для получения server_name, Nimble stream, wmsAuthSign и данных о резервных переносах камеры.")
        info.setObjectName("Muted")
        info.setWordWrap(True)
        self.login_input = QLineEdit(saved_login)
        self.login_input.setPlaceholderText("Логин B2O")
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Пароль B2O")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Отмена")
        ok = QPushButton("Войти")
        ok.setProperty("role", "primary")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        root.addWidget(title)
        root.addWidget(info)
        root.addWidget(self.login_input)
        root.addWidget(self.password_input)
        root.addLayout(row)
        self.password_input.returnPressed.connect(self.accept)

    def credentials(self):
        return self.login_input.text().strip(), self.password_input.text()
