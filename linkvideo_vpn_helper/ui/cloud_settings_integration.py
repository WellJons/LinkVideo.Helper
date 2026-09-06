from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from linkvideo_vpn_helper.services.cloud_vpnsync import (
    CloudServerConfig,
    CloudVPNSyncClient,
)
from linkvideo_vpn_helper.ui.components import Card, StatusPill


class _CloudTestBridge(QObject):
    done = Signal(object, object)


def install_cloud_settings_ui() -> None:
    from linkvideo_vpn_helper.ui.pages.settings_page import SettingsPage

    if getattr(SettingsPage, "_cloud_settings_installed", False):
        return

    original_build = SettingsPage._build

    def build(self):
        original_build(self)
        client = CloudVPNSyncClient(self.settings)
        config = client.config
        self.cloud_client = client

        card = Card(kind="hero")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(11)

        top = QHBoxLayout()
        labels = QVBoxLayout()
        title = QLabel("Облачный сервер")
        title.setObjectName("SectionTitle")
        hint = QLabel(
            "Центральный LinkVideo.VPNSync: PostgreSQL, история и автоматическая синхронизация MikroTik. "
            "Сейчас можно использовать IP; после подключения домена достаточно заменить адрес и включить HTTPS."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        labels.addWidget(title)
        labels.addWidget(hint)
        self.cloud_status = StatusPill(
            "Сохранён" if config.username and config.password else "Не настроен",
            "info" if config.username and config.password else "neutral",
        )
        top.addLayout(labels, 1)
        top.addWidget(self.cloud_status, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        self.cloud_host = QLineEdit(config.host)
        self.cloud_host.setPlaceholderText("192.168.88.141 или vpn.linkvideo.ru")
        self.cloud_port = QSpinBox()
        self.cloud_port.setRange(1, 65535)
        self.cloud_port.setValue(int(config.port))
        self.cloud_user = QLineEdit(config.username)
        self.cloud_user.setPlaceholderText("Логин сотрудника")
        self.cloud_password = QLineEdit(config.password)
        self.cloud_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.cloud_password.setPlaceholderText("Пароль")

        form.addWidget(QLabel("IP / домен"), 0, 0)
        form.addWidget(self.cloud_host, 1, 0)
        form.addWidget(QLabel("Порт"), 0, 1)
        form.addWidget(self.cloud_port, 1, 1)
        form.addWidget(QLabel("Логин"), 2, 0)
        form.addWidget(self.cloud_user, 3, 0)
        form.addWidget(QLabel("Пароль"), 2, 1)
        form.addWidget(self.cloud_password, 3, 1)
        form.setColumnStretch(0, 3)
        form.setColumnStretch(1, 2)
        layout.addLayout(form)

        options = QHBoxLayout()
        self.cloud_tls = QCheckBox("HTTPS")
        self.cloud_tls.setChecked(bool(config.use_tls))
        self.cloud_remember = QCheckBox("Сохранять вход на этом компьютере")
        self.cloud_remember.setChecked(bool(config.remember))
        self.cloud_password_visible = QCheckBox("Показать пароль")
        self.cloud_password_visible.toggled.connect(
            lambda visible: self.cloud_password.setEchoMode(
                QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
            )
        )
        options.addWidget(self.cloud_tls)
        options.addWidget(self.cloud_remember)
        options.addWidget(self.cloud_password_visible)
        options.addStretch(1)
        layout.addLayout(options)

        note = QLabel(
            "Пароль облачного сервера сохраняется через Windows DPAPI, а не открытым текстом. "
            "Google Sheets остаётся только аварийным резервом и не является рабочей базой."
        )
        note.setObjectName("TinyMuted")
        note.setWordWrap(True)
        layout.addWidget(note)

        actions = QHBoxLayout()
        self.cloud_test_button = QPushButton("Проверить и сохранить")
        self.cloud_test_button.setProperty("role", "primary")
        self.cloud_save_button = QPushButton("Сохранить без проверки")
        self.cloud_save_button.setProperty("role", "ghost")
        actions.addStretch(1)
        actions.addWidget(self.cloud_save_button)
        actions.addWidget(self.cloud_test_button)
        layout.addLayout(actions)

        self._cloud_bridge = _CloudTestBridge(self)
        self._cloud_bridge.done.connect(lambda result, error: _cloud_test_done(self, result, error))
        self.cloud_test_button.clicked.connect(lambda: _cloud_test(self))
        self.cloud_save_button.clicked.connect(lambda: _cloud_save(self, show_status=True))

        self.cloud_card = card
        # Header=0, appearance/archive layout=1, cloud=2, VPN servers follows.
        self.page_layout.insertWidget(2, card)

    def _config(self) -> CloudServerConfig:
        return CloudServerConfig(
            host=self.cloud_host.text().strip(),
            port=int(self.cloud_port.value()),
            username=self.cloud_user.text().strip(),
            password=self.cloud_password.text(),
            use_tls=bool(self.cloud_tls.isChecked()),
            remember=bool(self.cloud_remember.isChecked()),
        )

    def _cloud_save(self, show_status: bool = False) -> CloudServerConfig | None:
        try:
            config = _config(self)
            self.cloud_client.set_config(config, save=True)
        except Exception as exc:
            self.task.show()
            self.task.error("Не удалось сохранить облачный сервер", str(exc))
            self.cloud_status.setText("Ошибка настроек")
            self.cloud_status.setProperty("tone", "danger")
            return None
        if show_status:
            self.task.show()
            self.task.done("Облачный сервер сохранён", config.base_url)
        self.cloud_status.setText("Сохранён")
        return config

    def _cloud_test(self) -> None:
        if getattr(self, "_cloud_testing", False):
            return
        try:
            config = _config(self)
            config.validate()
        except Exception as exc:
            self.task.show()
            self.task.error("Проверьте настройки облачного сервера", str(exc))
            return
        self._cloud_testing = True
        self.cloud_test_button.setEnabled(False)
        self.task.show()
        self.task.busy("Проверяю облачный сервер", config.base_url)

        def worker():
            try:
                probe = CloudVPNSyncClient(self.settings)
                probe.set_config(config, save=False)
                result = probe.test_connection()
                self._cloud_bridge.done.emit((config, probe, result), None)
            except Exception as exc:
                self._cloud_bridge.done.emit(None, exc)

        threading.Thread(target=worker, daemon=True, name="vpnsync-cloud-test").start()

    def _cloud_test_done(self, result, error) -> None:
        self._cloud_testing = False
        self.cloud_test_button.setEnabled(True)
        if error is not None:
            self.cloud_status.setText("Нет подключения")
            self.task.error("Облачный сервер недоступен", str(error))
            return
        config, probe, payload = result
        try:
            probe.set_config(config, save=True)
            self.cloud_client = probe
        except Exception as exc:
            self.task.error("Сервер отвечает, но настройки не сохранены", str(exc))
            return
        health = payload.get("health") or {}
        auth = payload.get("me") or {}
        self.cloud_status.setText("Подключено")
        self.task.done(
            "Облачный сервер подключён",
            f"{config.base_url} · {auth.get('username', config.username)} · "
            f"RouterOS {health.get('routeros_workers_alive', 0)}/{health.get('routeros_servers', 0)} · "
            f"время {health.get('business_timezone', 'UTC+3')}",
        )

    SettingsPage._build = build
    SettingsPage._cloud_settings_installed = True
