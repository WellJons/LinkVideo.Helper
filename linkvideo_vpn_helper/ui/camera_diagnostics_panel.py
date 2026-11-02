from __future__ import annotations

"""Standalone B2O camera check inside Search & Management.

The panel is independent of the VPN record. It requires a camera ID from
the administrator interface and an existing B2O login (Archive page).
No credentials or RTSP links are copied into the result.
"""
import threading
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)
from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.camera_diagnostics import inspect_camera
from linkvideo_vpn_helper.ui.components import Card, StatusPill


class CameraDiagnosticsPanel(QWidget):
    resultReady = Signal(int, object, str)

    def __init__(self, settings, parent=None, *, inspector=inspect_camera):
        super().__init__(parent)
        self._settings = settings
        self._inspector = inspector
        self._request = 0
        self._build()
        self.resultReady.connect(self._on_result)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 18)
        layout.setSpacing(14)
        hero = Card(kind="hero")
        h = QVBoxLayout(hero)
        title = QLabel("Диагностика камеры B2O")
        title.setObjectName("SectionTitle")
        h.addWidget(title)
        info = QLabel(
            "Проверка любой камеры из админки по ID — VPN-клиент необязателен. "
            "Нужен вход в B2O в разделе «Скачать архив». Пинг и TCP выполняются "
            "с вашего компьютера, не с тестового сервера."
        )
        info.setWordWrap(True)
        info.setObjectName("Muted")
        h.addWidget(info)
        form = QFormLayout()
        self.camera_id = QLineEdit()
        self.camera_id.setObjectName("B2OCameraDiagnosticId")
        self.camera_id.setPlaceholderText("ID камеры, например linkvideo_207728")
        self.camera_id.returnPressed.connect(self._check)
        form.addRow("ID камеры", self.camera_id)
        self.operator = QComboBox()
        self.operator.setObjectName("B2OCameraDiagnosticOperator")
        for label, value in (("Россия", 241), ("Беларусь", 1741), ("Казахстан", 1721)):
            self.operator.addItem(label, value)
        form.addRow("Оператор", self.operator)
        h.addLayout(form)
        actions = QHBoxLayout()
        self.run_button = QPushButton("Проверить камеру")
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
        r.setSpacing(12)
        self.summary = QLabel("Здесь появятся сервер, IP и результаты проверки.")
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        r.addWidget(self.summary)
        self.thumbnail = QLabel("Превью камеры")
        self.thumbnail.setObjectName("B2OCameraDiagnosticPreview")
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.setMinimumHeight(220)
        self.thumbnail.setMaximumHeight(340)
        self.thumbnail.setStyleSheet(
            "QLabel#B2OCameraDiagnosticPreview {"
            " background: palette(alternate-base); border: 1px solid palette(mid);"
            " border-radius: 12px; padding: 12px; }"
        )
        r.addWidget(self.thumbnail)
        note = QLabel(
            "RTSP на тестовом сервере: проверка пока не подключена. "
            "Открытый TCP-порт подтверждает только доступность порта, "
            "но не исправность RTSP-видеопотока."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        r.addWidget(note)
        layout.addWidget(result_card)
        layout.addStretch(1)

    def _check(self):
        if not self.run_button.isEnabled():
            return
        camera_id = self.camera_id.text().strip()
        if not camera_id or len(camera_id) > 128:
            self.summary.setText("Введите корректный ID камеры из админки B2O.")
            return
        self._request += 1
        request = self._request
        operator = int(self.operator.currentData())
        self.run_button.setEnabled(False)
        self.status.set_status("Проверяю", "warning")
        self.summary.setText("Запрашиваю карточку B2O и выполняю сетевую диагностику…")
        self.thumbnail.clear()
        self.thumbnail.setText("Получаю превью…")

        def worker():
            try:
                result = self._inspector(B2OService(self._settings), camera_id, operator)
                self.resultReady.emit(request, result, "")
            except Exception:
                # Never surface untrusted B2O URLs, credentials or raw API bodies.
                self.resultReady.emit(
                    request, None,
                    "Не удалось получить камеру. Проверьте ID, права B2O и авторизацию в «Скачать архив».",
                )
        threading.Thread(target=worker, daemon=True, name="b2o-camera-diag").start()

    def _on_result(self, request, result, error):
        if request != self._request:
            return
        self.run_button.setEnabled(True)
        if error:
            self.status.set_status("Ошибка", "danger")
            self.summary.setText(error)
            self.thumbnail.setText("Нет данных для превью")
            return
        self.status.set_status("Проверено", "success")
        self.summary.setText(
            f"Камера: {result.camera_id}\n"
            f"B2O / DVR: {result.server}\n"
            f"IP: {result.ip or 'не предоставлен'}\n"
            f"{result.ping_text}\n{result.tcp_text}"
        )
        picture = QPixmap()
        if result.preview and picture.loadFromData(result.preview):
            self.thumbnail.setPixmap(picture.scaled(
                480, 290, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            self.thumbnail.setText("Превью недоступно для этой камеры.")
