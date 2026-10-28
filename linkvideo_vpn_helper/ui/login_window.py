from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout,
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
        self._verification_signals = _LoginVerificationSignals(self)
        self._verification_signals.finished.connect(self._on_verified)
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
        if (
            self.remember.isChecked()
            and self.login.text().strip()
            and self.password.text()
        ):
            QTimer.singleShot(0, self._auto_verify_saved)

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
        sub = QLabel("Вход подтверждается по учётной записи MikroTik в списке /user.")
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
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        fl.addWidget(self.status)
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

    def _auto_verify_saved(self) -> None:
        # Saved credentials are never an authorization token. Verify them on
        # every startup and show this same login form if they are stale.
        if not self._pending and self.isVisible():
            self._submit(automatic=True)

    def _set_pending(self, pending: bool) -> None:
        self._pending = pending
        for control in (self.login, self.password, self.remember, self.submit, self.show_password):
            control.setEnabled(not pending)
        self.submit.setText("Проверяю вход…" if pending else "Войти в LinkVideo.Helper")

    def _submit(self, automatic: bool = False) -> None:
        if self._pending:
            return
        user = self.login.text().strip()
        password = self.password.text()
        if not user or not password:
            self.error.setText("Введите логин и пароль MikroTik.")
            return

        self.error.clear()
        self.status.setText("Проверяю пароль, пользователя и права в MikroTik…")
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
        self.status.clear()

        if failure is not None or verified is None:
            if self._automatic_attempt and isinstance(failure, LoginRejected):
                # Prevent repeated startup attempts using a rejected saved
                # password. Keep the username visible for correction.
                self.settings.remove("password")
            self.error.setText(str(failure or "Не удалось подтвердить вход."))
            self.password.setFocus()
            self.password.selectAll()
            return

        username = self.login.text().strip()
        password = self.password.text()
        if username != verified.username:
            self.error.setText("Данные входа изменились во время проверки. Повторите попытку.")
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
