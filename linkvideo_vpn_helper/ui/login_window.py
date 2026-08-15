from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout,
)

from linkvideo_vpn_helper.theme import get_theme_style
from linkvideo_vpn_helper.version import APP_VERSION


@dataclass(slots=True)
class LoginPayload:
    username: str
    password: str


class LoginWindow(QDialog):
    def __init__(self, settings: QSettings | None = None, parent=None):
        super().__init__(parent)
        self.settings = settings or QSettings("LinkVideo", "LinkVideo.Helper")
        self.payload: LoginPayload | None = None
        self.setWindowTitle("LinkVideo.Helper")
        icon_path = Path(__file__).resolve().parents[2] / "icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self._icon_path = icon_path
        self.resize(760, 470)
        self.setMinimumSize(680, 430)
        self.setObjectName("AppRoot")
        self.setStyleSheet(get_theme_style(str(self.settings.value("ui/theme_v2", "system", str) or "system")))
        self._build()

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(14)

        brand = QFrame()
        brand.setObjectName("AccentCard")
        brand.setMinimumWidth(270)
        bl = QVBoxLayout(brand)
        bl.setContentsMargins(28, 28, 28, 28)
        bl.setSpacing(9)
        mark = QLabel("LV")
        mark.setObjectName("BrandMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(52, 52)
        if self._icon_path.exists():
            mark.setPixmap(QIcon(str(self._icon_path)).pixmap(52, 52))
            mark.setStyleSheet("background: transparent; border: none;")
        title = QLabel("LinkVideo.Helper")
        title.setObjectName("PageTitle")
        text = QLabel("Создание и управление VPN-клиентами, работа с портами и скачивание архива LinkVideo.")
        text.setObjectName("Muted")
        text.setWordWrap(True)
        version = QLabel(f"Версия {APP_VERSION}")
        version.setObjectName("TinyMuted")
        bl.addWidget(mark)
        bl.addSpacing(10)
        bl.addWidget(title)
        bl.addWidget(text)
        bl.addStretch(1)
        bl.addWidget(version)
        root.addWidget(brand, 1)

        form = QFrame()
        form.setObjectName("HeroCard")
        fl = QVBoxLayout(form)
        fl.setContentsMargins(28, 28, 28, 28)
        fl.setSpacing(11)
        heading = QLabel("Вход")
        heading.setObjectName("PageTitle")
        sub = QLabel("Данные используются для подключения к RouterOS API.")
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        fl.addWidget(heading)
        fl.addWidget(sub)
        fl.addSpacing(8)

        user_label = QLabel("Логин MikroTik")
        user_label.setObjectName("CardTitle")
        self.login = QLineEdit(str(self.settings.value("username", "", str) or ""))
        self.login.setPlaceholderText("Введите логин")
        fl.addWidget(user_label)
        fl.addWidget(self.login)

        pw_label = QLabel("Пароль")
        pw_label.setObjectName("CardTitle")
        pw_row = QHBoxLayout()
        pw_row.setSpacing(8)
        self.password = QLineEdit(str(self.settings.value("password", "", str) or ""))
        self.password.setPlaceholderText("Введите пароль")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_password = QPushButton("Показать")
        self.show_password.setProperty("role", "soft")
        self.show_password.setCheckable(True)
        self.show_password.toggled.connect(self._toggle_password)
        pw_row.addWidget(self.password, 1)
        pw_row.addWidget(self.show_password)
        fl.addWidget(pw_label)
        fl.addLayout(pw_row)

        self.remember = QCheckBox("Запомнить данные на этом компьютере")
        self.remember.setChecked(bool(self.settings.value("remember", True, bool)))
        fl.addWidget(self.remember)
        self.error = QLabel("")
        self.error.setObjectName("DangerText")
        self.error.setWordWrap(True)
        fl.addWidget(self.error)
        fl.addStretch(1)
        self.submit = QPushButton("Войти в LinkVideo.Helper")
        self.submit.setProperty("role", "primary")
        self.submit.setMinimumHeight(48)
        self.submit.clicked.connect(self._submit)
        fl.addWidget(self.submit)
        root.addWidget(form, 2)

        self.password.returnPressed.connect(self._submit)
        self.login.returnPressed.connect(lambda: self.password.setFocus())
        if self.login.text().strip():
            self.password.setFocus()
        else:
            self.login.setFocus()

    def _toggle_password(self, visible: bool):
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self.show_password.setText("Скрыть" if visible else "Показать")

    def _submit(self):
        user = self.login.text().strip()
        password = self.password.text()
        if not user or not password:
            self.error.setText("Введите логин и пароль MikroTik.")
            return
        remember = self.remember.isChecked()
        self.settings.setValue("remember", remember)
        if remember:
            self.settings.setValue("username", user)
            self.settings.setValue("password", password)
        else:
            self.settings.remove("username")
            self.settings.remove("password")
        self.payload = LoginPayload(user, password)
        self.accept()
