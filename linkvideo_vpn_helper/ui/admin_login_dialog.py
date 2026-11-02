from __future__ import annotations

"""Administrative credentials dialog styled like the main MikroTik login."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)


class AdminLoginDialog(QDialog):
    def __init__(self, username: str = "", *, remember: bool = True, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Вход в админку")
        self.setObjectName("AppRoot")
        self.setMinimumWidth(440)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        card = QFrame(self)
        card.setObjectName("LoginForm")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(12)

        title = QLabel("Вход в админку")
        title.setObjectName("LoginTitle")
        layout.addWidget(title)
        subtitle = QLabel("Используйте учётную запись админки. Данные MikroTik здесь не применяются.")
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        layout.addSpacing(10)

        label_user = QLabel("Логин")
        label_user.setObjectName("CardTitle")
        layout.addWidget(label_user)
        self.login = QLineEdit(str(username or ""))
        self.login.setObjectName("LoginField")
        self.login.setPlaceholderText("Логин админки")
        self.login.setMinimumHeight(46)
        layout.addWidget(self.login)

        label_password = QLabel("Пароль")
        label_password.setObjectName("CardTitle")
        layout.addWidget(label_password)
        row = QHBoxLayout()
        row.setSpacing(7)
        self.password = QLineEdit()
        self.password.setObjectName("LoginField")
        self.password.setPlaceholderText("Пароль")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setMinimumHeight(46)
        self.show_password = QPushButton("Показать")
        self.show_password.setProperty("role", "soft")
        self.show_password.setCheckable(True)
        self.show_password.setMinimumHeight(46)
        self.show_password.toggled.connect(self._toggle_password)
        row.addWidget(self.password, 1)
        row.addWidget(self.show_password)
        layout.addWidget(label_password)
        layout.addLayout(row)

        self.remember = QCheckBox("Запомнить данные на этом компьютере")
        self.remember.setChecked(bool(remember))
        layout.addWidget(self.remember)
        hint = QLabel("После успешного входа пароль будет защищён Windows DPAPI.")
        hint.setObjectName("TinyMuted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.setProperty("role", "soft")
        cancel.clicked.connect(self.reject)
        self.submit = QPushButton("Войти")
        self.submit.setProperty("role", "primary")
        self.submit.setMinimumHeight(46)
        self.submit.clicked.connect(self._submit)
        button_row.addWidget(cancel)
        button_row.addWidget(self.submit)
        layout.addLayout(button_row)

        self.error = QLabel("")
        self.error.setObjectName("DangerText")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        outer.addWidget(card)
        self.password.returnPressed.connect(self._submit)
        self.login.returnPressed.connect(lambda: self.password.setFocus())
        (self.password if self.login.text().strip() else self.login).setFocus()

    def _toggle_password(self, visible: bool) -> None:
        self.password.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )
        self.show_password.setText("Скрыть" if visible else "Показать")

    def _submit(self) -> None:
        if not self.login.text().strip() or not self.password.text():
            self.error.setText("Введите логин и пароль админки.")
            self.error.show()
            return
        self.accept()

    def credentials(self) -> tuple[str, str, bool]:
        return self.login.text().strip(), self.password.text(), self.remember.isChecked()
