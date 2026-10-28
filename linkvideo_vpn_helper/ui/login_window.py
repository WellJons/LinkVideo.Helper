from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading

from PySide6.QtCore import QEasingCurve, QObject, QPoint, QPropertyAnimation, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.routeros_login_verification import (
    LoginRejected, LoginUnavailable, verify_routeros_user,
)
from linkvideo_vpn_helper.services.server_registry import ServerRegistry
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


class _LoginVerificationSignals(QObject):
    finished = Signal(int, object, object)


class LoginWindow(QDialog):
    def __init__(self, settings: QSettings | None = None, parent=None):
        super().__init__(parent)
        self.settings = settings or QSettings("LinkVideo", "LinkVideo.Helper")
        self.payload: LoginPayload | None = None
        self._attempt = 0
        self._pending = False
        self._automatic_attempt = False
        self._shake_animation = None
        self._verification_signals = _LoginVerificationSignals(self)
        self._verification_signals.finished.connect(self._on_verified)
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
        if (
            self.remember.isChecked()
            and self.login.text().strip()
            and self.password.text()
        ):
            QTimer.singleShot(0, self._auto_verify_saved)

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
        if self._pending:
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

    def _auto_verify_saved(self) -> None:
        # Saved credentials are never an authorization token. Verify them on
        # every startup and show this same login form if they are stale.
        if not self._pending and self.isVisible():
            self._submit(automatic=True)

    def _set_pending(self, pending: bool) -> None:
        self._pending = pending
        for control in (self.login, self.password, self.remember, self.submit, self.show_password):
            control.setEnabled(not pending)
        self.submit.setText("Проверяю вход…" if pending else "Войти")

    def _submit(self, automatic: bool = False) -> None:
        if self._pending:
            return
        user = self.login.text().strip()
        password = self.password.text()
        if not user or not password:
            self._status("Введите логин и пароль MikroTik.", danger=True)
            return

        self._status("Проверяю пароль, пользователя и права в MikroTik…")
        self._automatic_attempt = automatic
        self._attempt += 1
        attempt = self._attempt
        self._set_pending(True)
        # No queries or DNS lookups on the Qt UI thread.
        # Authentication hosts must not come from user-editable custom
        # server settings: a hostile custom IP could capture passwords.
        hosts = [item.host for item in ServerRegistry.defaults()]

        def worker() -> None:
            try:
                verified = verify_routeros_user(user, password, hosts)
                self._verification_signals.finished.emit(attempt, verified, None)
            except (LoginRejected, LoginUnavailable) as exc:
                self._verification_signals.finished.emit(attempt, None, exc)
            except Exception:
                # Never display passwords, raw protocol frames, or internals.
                self._verification_signals.finished.emit(
                    attempt, None,
                    LoginUnavailable("Проверка MikroTik завершилась ошибкой. Повторите попытку."),
                )

        threading.Thread(target=worker, daemon=True, name="lv-routeros-login").start()

    def _on_verified(self, attempt: int, verified, failure) -> None:
        if attempt != self._attempt:
            return
        self._set_pending(False)
        self._status("")

        if failure is not None or verified is None:
            if self._automatic_attempt and isinstance(failure, LoginRejected):
                # Prevent repeated startup attempts using a rejected saved
                # password. Keep the username visible for correction.
                self.settings.remove("password")
            self._status(str(failure or "Не удалось подтвердить вход."), danger=True)
            if isinstance(failure, LoginRejected):
                for field in (self.login, self.password):
                    field.setProperty("error", True)
                    field.style().unpolish(field)
                    field.style().polish(field)
                self._shake_on_invalid_password()
            self.password.setFocus()
            self.password.selectAll()
            return

        username = self.login.text().strip()
        password = self.password.text()
        if username != verified.username:
            self._status("Данные входа изменились во время проверки. Повторите попытку.", danger=True)
            return

        remember = self.remember.isChecked()
        self.settings.setValue("remember", remember)
        if remember:
            self.settings.setValue("username", username)
            self.settings.setValue("password", password)
        else:
            self.settings.remove("username")
            self.settings.remove("password")
        self.settings.sync()
        self.payload = LoginPayload(username, password)
        self.accept()
