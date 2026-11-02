from __future__ import annotations

"""Camera information panel; no inline video or preview widgets."""

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.camera_diagnostics import (
    CameraLookupError, inspect_camera,
)
from linkvideo_vpn_helper.ui.components import Card, StatusPill


class CameraDiagnosticsPanel(QWidget):
    resultReady = Signal(int, object, str)

    def __init__(self, settings, parent=None, *, inspector=inspect_camera,
                 work_account=None):
        super().__init__(parent)
        self._settings = settings
        self._inspector = inspector
        self.work_account = work_account
        self._request = 0
        self._waiting_for_auth = False
        self._build()
        self.resultReady.connect(self._on_result)
        if self.work_account is not None:
            self.work_account.b2oChanged.connect(self._on_b2o_changed)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 18)
        layout.setSpacing(14)

        hero = Card(kind="hero")
        h = QVBoxLayout(hero)
        h.setSpacing(12)
        title = QLabel("Информация по камере")
        title.setObjectName("SectionTitle")
        h.addWidget(title)
        info = QLabel(
            "Поиск камеры в админке по ID или серийному номеру. "
            "VPN-клиент необязателен. Используется вход «Админка»."
        )
        info.setWordWrap(True)
        info.setObjectName("Muted")
        h.addWidget(info)

        form = QFormLayout()
        self.camera_id = QLineEdit()
        self.camera_id.setObjectName("B2OCameraDiagnosticId")
        self.camera_id.setPlaceholderText("ID или серийный номер камеры")
        self.camera_id.returnPressed.connect(self._check)
        form.addRow("Камера", self.camera_id)
        self.operator = QComboBox()
        self.operator.setObjectName("B2OCameraDiagnosticOperator")
        for label, value in (("Россия", 241), ("Беларусь", 1741), ("Казахстан", 1721)):
            self.operator.addItem(label, value)
        form.addRow("Страна", self.operator)
        h.addLayout(form)

        actions = QHBoxLayout()
        self.run_button = QPushButton("Получить информацию")
        self.run_button.setObjectName("B2OCameraDiagnosticRun")
        self.run_button.setProperty("role", "primary")
        self.run_button.clicked.connect(self._check)
        actions.addWidget(self.run_button)
        self.status = StatusPill("Ожидание", "neutral")
        actions.addWidget(self.status)
        actions.addStretch(1)
        h.addLayout(actions)
        layout.addWidget(hero)

        result_card = Card()
        r = QVBoxLayout(result_card)
        r.setSpacing(9)
        result_title = QLabel("Данные камеры")
        result_title.setObjectName("CardTitle")
        r.addWidget(result_title)
        self.summary = QLabel("Введите ID камеры и нажмите «Получить информацию».")
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        r.addWidget(self.summary)
        self.network_note = QLabel(
            "ICMP и TCP проверяются с рабочего компьютера. "
            "Доступность порта не подтверждает работу RTSP-потока."
        )
        self.network_note.setObjectName("TinyMuted")
        self.network_note.setWordWrap(True)
        self.network_note.hide()
        r.addWidget(self.network_note)
        layout.addWidget(result_card)
        layout.addStretch(1)

    def _check(self):
        if not self.run_button.isEnabled():
            return
        camera_id = self.camera_id.text().strip()
        if not camera_id or len(camera_id) > 128:
            self.status.set_status("Нужен ID", "warning")
            self.summary.setText("Введите ID или серийный номер камеры.")
            return
        if not B2OService(self._settings).token():
            account = self.work_account
            if account is None:
                self.status.set_status("Нужен вход", "warning")
                self.summary.setText("Войдите в аккаунт «Админка» в боковой панели.")
                return
            if not account.has_credentials:
                self.status.set_status("Нужен вход", "warning")
                self.summary.setText("Введите логин и пароль в разделе «Админка».")
                account.loginRequested.emit()
                return
            self._waiting_for_auth = True
            self.run_button.setEnabled(False)
            self.status.set_status("Подключение", "warning")
            self.summary.setText("Проверяю доступ учётной записи админки…")
            account.ensure_b2o()
            return

        self._waiting_for_auth = False
        self._request += 1
        request = self._request
        operator = int(self.operator.currentData())
        self.run_button.setEnabled(False)
        self.status.set_status("Проверяю", "warning")
        self.summary.setText("Запрашиваю данные камеры в админке…")
        self.network_note.hide()

        def worker():
            try:
                result = self._inspector(
                    B2OService(self._settings), camera_id, operator,
                    with_preview=False,
                )
                self.resultReady.emit(request, result, "")
            except CameraLookupError as exc:
                self.resultReady.emit(request, None, str(exc))
            except Exception:
                self.resultReady.emit(
                    request, None,
                    "Не удалось получить данные камеры. Проверьте ID, страну и права админки.",
                )
        threading.Thread(target=worker, daemon=True,
                         name="b2o-camera-information").start()

    def _on_b2o_changed(self, ok: bool, error: str):
        if not self._waiting_for_auth:
            return
        self._waiting_for_auth = False
        self.run_button.setEnabled(True)
        if ok:
            self._check()
        else:
            self.status.set_status("Нет доступа", "danger")
            self.summary.setText(
                "Не удалось подключиться к админке. Проверьте аккаунт в боковой панели."
            )

    def _on_result(self, request, result, error):
        if request != self._request:
            return
        self.run_button.setEnabled(True)
        if error:
            self.status.set_status("Ошибка", "danger")
            self.summary.setText(error)
            self.network_note.hide()
            return

        self.status.set_status("Получено", "success")
        rows = [
            ("Камера", result.camera_id),
            ("Страна", result.operator),
            ("Название", result.name),
            ("Статус", result.status),
            ("Серийный номер", result.serial),
            ("Модель", result.model),
            ("Аккаунт", result.account_id),
            ("Сервер", result.server),
            ("IP камеры", result.ip),
            ("Глубина архива", (result.archive_depth + " дн.")
             if result.archive_depth.isdigit() else result.archive_depth),
        ]
        rows = [f"{label}: {value}" for label, value in rows if value]
        rows.extend([result.ping_text, result.tcp_text])
        self.summary.setText("\n".join(rows))
        self.network_note.show()
