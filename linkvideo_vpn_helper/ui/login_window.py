from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.theme import get_theme_style
from linkvideo_vpn_helper.version import APP_VERSION


@dataclass(slots=True)
class LoginPayload:
    username: str
    password: str


class LoginIllustration(QWidget):
    """Subtle vector waves matching the desktop brand without image assets."""

    def __init__(self, theme_key: str, parent=None):
        super().__init__(parent)
        from linkvideo_vpn_helper.theme import colors
        self.palette_colors = colors(theme_key)
        self.setObjectName("LoginIllustration")

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        c = self.palette_colors
        painter.fillRect(self.rect(), QColor(c["bg"]))
        if w < 5 or h < 5:
            return

        # Curved waves are intentionally low-contrast behind the login brand.
        accent = QColor(c["accent"])
        waves = (
            (0.60, 0.84, 0.96, 0.30),
            (0.74, 0.92, 1.04, 0.20),
            (0.86, 0.96, 1.10, 0.13),
        )
        for start, upper, end, opacity in waves:
            shape = QPainterPath()
            shape.moveTo(0, h * start)
            shape.cubicTo(w * .21, h * (start + .06), w * .48, h * upper, w, h * end)
            shape.lineTo(w, h)
            shape.lineTo(0, h)
            shape.closeSubpath()
            top = QColor(accent)
            top.setAlphaF(opacity)
            bottom = QColor(accent)
            bottom.setAlphaF(opacity * .12)
            gradient = QLinearGradient(0, h * start, w, h)
            gradient.setColorAt(0, top)
            gradient.setColorAt(1, bottom)
            painter.fillPath(shape, gradient)


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
        self.resize(1080, 660)
        self.setMinimumSize(730, 550)
        self.setObjectName("AppRoot")
        self.setStyleSheet(get_theme_style(str(self.settings.value("ui/theme_v2", "system", str) or "system")))
        self._build()
        self.validationFinished.connect(self._on_validation_finished)

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        theme_key = str(self.settings.value("ui/theme_v2", "linkvideo_2026", str) or "linkvideo_2026")
        brand = LoginIllustration(theme_key)
        self.brand_panel = brand
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(46, 44, 34, 28)
        brand_layout.setSpacing(8)
        brand_layout.addStretch(2)

        mark = QLabel("LV")
        mark.setObjectName("BrandMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(82, 82)
        if self._icon_path.exists():
            mark.setPixmap(QIcon(str(self._icon_path)).pixmap(82, 82))
        brand_layout.addWidget(mark, 0, Qt.AlignmentFlag.AlignHCenter)

        title = QLabel("LinkVideo.Helper")
        title.setObjectName("LoginBrandTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_layout.addWidget(title)

        tagline = QLabel("Рабочие инструменты")
        tagline.setObjectName("LoginBrandSubtitle")
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_layout.addWidget(tagline)
        brand_layout.addStretch(3)

        version = QLabel(f"v{APP_VERSION}")
        version.setObjectName("LoginVersion")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_layout.addWidget(version)
        root.addWidget(brand, 5)

        stage = QWidget()
        stage.setObjectName("LoginStage")
        stage_l = QVBoxLayout(stage)
        stage_l.setContentsMargins(28, 28, 28, 28)
        stage_l.addStretch(1)

        form = QFrame()
        form.setObjectName("LoginForm")
        form.setMaximumWidth(480)
        form.setMinimumWidth(370)
        fl = QVBoxLayout(form)
        fl.setContentsMargins(32, 30, 32, 28)
        fl.setSpacing(11)

        heading = QLabel("Вход в программу")
        heading.setObjectName("LoginTitle")
        fl.addWidget(heading)
        sub = QLabel("Введите данные для доступа к MikroTik")
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        fl.addWidget(sub)
        fl.addSpacing(14)

        user_label = QLabel("Логин")
        user_label.setObjectName("CardTitle")
        self.login = QLineEdit(str(self.settings.value("username", "", str) or ""))
        self.login.setObjectName("LoginField")
        self.login.setPlaceholderText("Логин MikroTik")
        self.login.setMinimumHeight(46)
        fl.addWidget(user_label)
        fl.addWidget(self.login)

        pw_label = QLabel("Пароль")
        pw_label.setObjectName("CardTitle")
        fl.addSpacing(5)
        fl.addWidget(pw_label)
        pw_row = QHBoxLayout()
        pw_row.setSpacing(7)
        self.password = QLineEdit(str(self.settings.value("password", "", str) or ""))
        self.password.setObjectName("LoginField")
        self.password.setPlaceholderText("Введите пароль")
        self.password.setMinimumHeight(46)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_password = QPushButton("Показать")
        self.show_password.setProperty("role", "soft")
        self.show_password.setCheckable(True)
        self.show_password.setMinimumHeight(46)
        self.show_password.setToolTip("Показать или скрыть пароль")
        self.show_password.toggled.connect(self._toggle_password)
        pw_row.addWidget(self.password, 1)
        pw_row.addWidget(self.show_password)
        fl.addLayout(pw_row)
        fl.addSpacing(5)

        self.remember = QCheckBox("Запомнить данные на этом компьютере")
        self.remember.setChecked(bool(self.settings.value("remember", True, bool)))
        fl.addWidget(self.remember)
        fl.addSpacing(12)

        self.submit = QPushButton("Войти")
        self.submit.setProperty("role", "primary")
        self.submit.setMinimumHeight(48)
        self.submit.clicked.connect(self._submit)
        fl.addWidget(self.submit)

        self.status_message = QLabel("")
        self.status_message.setObjectName("TinyMuted")
        self.status_message.setWordWrap(True)
        self.status_message.hide()
        fl.addWidget(self.status_message)

        self.error_banner = QFrame()
        self.error_banner.setObjectName("LoginError")
        error_l = QHBoxLayout(self.error_banner)
        error_l.setContentsMargins(12, 10, 12, 10)
        error_l.setSpacing(8)
        error_icon = QLabel("!")
        error_icon.setFixedWidth(15)
        error_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error = QLabel("")
        self.error.setObjectName("DangerText")
        self.error.setWordWrap(True)
        error_l.addWidget(error_icon)
        error_l.addWidget(self.error, 1)
        self.error_banner.hide()
        fl.addWidget(self.error_banner)
        stage_l.addWidget(form, 0, Qt.AlignmentFlag.AlignHCenter)
        stage_l.addStretch(1)
        root.addWidget(stage, 6)

        self.password.returnPressed.connect(self._submit)
        self.login.returnPressed.connect(lambda: self.password.setFocus())
        self.login.textChanged.connect(self._clear_login_error)
        self.password.textChanged.connect(self._clear_login_error)
        if self.login.text().strip():
            self.password.setFocus()
        else:
            self.login.setFocus()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "brand_panel"):
            self.brand_panel.setVisible(self.width() >= 875)

    def _status(self, message: str, *, danger: bool = False):
        self.error.setText(message if danger else "")
        self.error_banner.setVisible(bool(message) and danger)
        self.status_message.setText(message if not danger else "")
        self.status_message.setVisible(bool(message) and not danger)

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
