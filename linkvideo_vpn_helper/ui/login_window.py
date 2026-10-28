from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSettings, Qt, Signal
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
    validationFinished = Signal(bool, str)

    def __init__(self, settings: QSettings | None = None, parent=None):
        super().__init__(parent)
        self.settings = settings or QSettings("LinkVideo", "LinkVideo.Helper")
        self.payload: LoginPayload | None = None
        self._checking = False
        self._shake_animation = None
        self._pending: tuple[str, str, bool] | None = None
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
        self.validationFinished.connect(self._on_validation_finished)

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
        sub = QLabel("Данные проверяются через RouterOS API до открытия программы.")
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
        self.error.setMinimumHeight(32)
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
        self.login.textChanged.connect(self._clear_login_error)
        self.password.textChanged.connect(self._clear_login_error)
        if self.login.text().strip():
            self.password.setFocus()
        else:
            self.login.setFocus()

    def _status(self, message: str, *, danger: bool = False):
        self.error.setObjectName("DangerText" if danger else "Muted")
        self.error.setText(message)
        self.error.style().unpolish(self.error)
        self.error.style().polish(self.error)

    def _clear_login_error(self, *_args):
        if self._checking:
            return
        self._status("")
        for field in (self.login, self.password):
            field.setProperty("error", False)
            field.style().unpolish(field)
            field.style().polish(field)

    def _shake_on_invalid_password(self):
        if not bool(self.settings.value("ui/animations_enabled", True, bool)):
            return
        if self._shake_animation is not None:
            self._shake_animation.stop()
        origin = self.pos()
        shake = QPropertyAnimation(self, b"pos", self)
        shake.setDuration(280)
        shake.setEasingCurve(QEasingCurve.Type.InOutSine)
        offsets = (0, -7, 7, -5, 5, -2, 2, 0)
        for index, offset in enumerate(offsets):
            shake.setKeyValueAt(index / (len(offsets) - 1), QPoint(origin.x() + offset, origin.y()))
        self._shake_animation = shake
        shake.start()

    def _toggle_password(self, visible: bool):
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self.show_password.setText("Скрыть" if visible else "Показать")

    def _submit(self):
        if self._checking:
            return
        user = self.login.text().strip()
        password = self.password.text()
        if not user or not password:
            self._status("Введите логин и пароль MikroTik.", danger=True)
            return

        from linkvideo_vpn_helper.services.server_registry import ServerRegistry
        hosts = ServerRegistry(self.settings).hosts()
        if not hosts:
            hosts = [server.host for server in ServerRegistry.defaults()]
        self._pending = (user, password, self.remember.isChecked())
        self._checking = True
        self._status("Проверяю доступ на VPN-серверах…")
        self.submit.setText("Проверка входа…")
        for widget in (self.login, self.password, self.remember, self.submit):
            widget.setEnabled(False)

        def worker():
            from linkvideo_vpn_helper.services.authentication import AuthenticationFailure, verify_credentials
            try:
                verify_credentials(user, password, hosts)
            except AuthenticationFailure as exc:
                self.validationFinished.emit(False, str(exc))
            except Exception:
                self.validationFinished.emit(False, "Произошла ошибка при проверке входа. Повторите попытку.")
            else:
                self.validationFinished.emit(True, "")

        threading.Thread(target=worker, name="lv-login-check", daemon=True).start()

    def _on_validation_finished(self, success: bool, error: str):
        self._checking = False
        self.submit.setText("Войти в LinkVideo.Helper")
        for widget in (self.login, self.password, self.remember, self.submit):
            widget.setEnabled(True)
        if not success or self._pending is None:
            self._pending = None
            message = error or "Не удалось проверить учётную запись."
            credential_rejected = message.startswith("Логин или пароль")
            self._status(message, danger=True)
            if credential_rejected:
                for field in (self.login, self.password):
                    field.setProperty("error", True)
                    field.style().unpolish(field)
                    field.style().polish(field)
                self.password.selectAll()
                self._shake_on_invalid_password()
            self.password.setFocus()
            return
        user, password, remember = self._pending
        self._pending = None
        # Store credentials only AFTER RouterOS has verified them. An invalid
        # saved password will never silently unlock the application on restart.
        self.settings.setValue("remember", remember)
        if remember:
            self.settings.setValue("username", user)
            self.settings.setValue("password", password)
        else:
            self.settings.remove("username")
            self.settings.remove("password")
        self.payload = LoginPayload(user, password)
        self.accept()
