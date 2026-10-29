from __future__ import annotations

"""Camera settings for LD devices found through authenticated agent-vcore."""

import threading
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.agent_service import AgentCamera, AgentService
from linkvideo_vpn_helper.ui.components import (
    Card, PageHeader, StatusPill, TaskStatus, build_page_scaffold,
)
from linkvideo_vpn_helper.ui.dialogs import ConfirmDialog


class NewAgentPage(QWidget):
    operationReady = Signal(int, str, object)
    ACTIONS = (
        ("Выберите действие", ""),
        ("Настроить дату, время и NTP", "time"),
        ("Изменить битрейт основного потока", "bitrate"),
        ("Ориентация изображения", "rotation"),
    )
    ROTATIONS = (
        ("Обычное изображение (0)", 0),
        ("Вертикальное отражение (1)", 1),
        ("Горизонтальное отражение (2)", 2),
        ("Поворот на 180° (3)", 3),
    )

    def __init__(self, settings, credentials, parent=None, *, service=None):
        super().__init__(parent)
        self.settings = settings
        self.service = service or AgentService(settings, credentials)
        self.camera: AgentCamera | None = None
        self._busy = False
        self._generation = 0
        self._image_enhance = None
        self._video_streams = None
        self.operationReady.connect(self._on_ready)
        self._build()

    def _build(self):
        _, _, outer = build_page_scaffold(
            self, max_width=1190, min_width=760, margins=26, spacing=14
        )
        outer.addWidget(PageHeader(
            "Новый Агент",
            "Поиск LD-камер по серийному номеру и управление настройками через agent-vcore."
        ))

        search = Card(kind="hero")
        search.setObjectName("NewAgentSearchCard")
        root = QVBoxLayout(search)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)
        heading = QLabel("Найти камеру")
        heading.setObjectName("SectionTitle")
        root.addWidget(heading)
        hint = QLabel("Укажите serial_number камеры. Сервер агента определится автоматически.")
        hint.setObjectName("Muted")
        root.addWidget(hint)
        row = QHBoxLayout()
        row.setSpacing(9)
        self.serial = QLineEdit()
        self.serial.setObjectName("NewAgentSerial")
        self.serial.setMinimumHeight(44)
        self.serial.setClearButtonEnabled(True)
        self.serial.setPlaceholderText("Серийный номер камеры LD")
        self.serial.returnPressed.connect(self._find)
        self.find_btn = QPushButton("Найти камеру")
        self.find_btn.setObjectName("NewAgentFind")
        self.find_btn.setProperty("role", "primary")
        self.find_btn.setMinimumHeight(44)
        self.find_btn.clicked.connect(self._find)
        row.addWidget(self.serial, 1)
        row.addWidget(self.find_btn)
        root.addLayout(row)
        outer.addWidget(search)

        self.camera_card = Card()
        self.camera_card.setObjectName("NewAgentCameraCard")
        cam = QVBoxLayout(self.camera_card)
        cam.setContentsMargins(20, 16, 20, 16)
        cam.setSpacing(10)
        cam_top = QHBoxLayout()
        cam_title = QLabel("Найденная камера")
        cam_title.setObjectName("SectionTitle")
        self.camera_pill = StatusPill("Подключена", "success")
        cam_top.addWidget(cam_title, 1)
        cam_top.addWidget(self.camera_pill)
        cam.addLayout(cam_top)
        self.info = QLabel("")
        self.info.setObjectName("Value")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cam.addWidget(self.info)
        outer.addWidget(self.camera_card)

        self.actions_card = Card(kind="hero")
        self.actions_card.setObjectName("NewAgentActionsCard")
        work = QVBoxLayout(self.actions_card)
        work.setContentsMargins(20, 17, 20, 19)
        work.setSpacing(12)
        heading = QLabel("Настройки камеры")
        heading.setObjectName("SectionTitle")
        work.addWidget(heading)
        caption = QLabel("Выберите параметр. Текущее значение загрузится до изменения.")
        caption.setObjectName("Muted")
        caption.setWordWrap(True)
        work.addWidget(caption)
        self.action_combo = QComboBox()
        self.action_combo.setObjectName("NewAgentAction")
        self.action_combo.setMinimumHeight(43)
        for label, value in self.ACTIONS:
            self.action_combo.addItem(label, value)
        work.addWidget(self.action_combo)

        self.time_row = Card(subtle=True)
        time_l = QVBoxLayout(self.time_row)
        time_l.setContentsMargins(14, 12, 14, 12)
        time_l.setSpacing(8)
        time_l.addWidget(QLabel("Часовой пояс камеры"))
        self.timezone = QSpinBox()
        self.timezone.setObjectName("NewAgentTimezone")
        self.timezone.setRange(-12, 14)
        self.timezone.setValue(7)
        self.timezone.setSuffix(" UTC")
        self.timezone.setMinimumHeight(43)
        time_l.addWidget(self.timezone)
        ntp = QLabel("Синхронизация времени с ntp.goodline.info · каждые 3600 секунд")
        ntp.setObjectName("TinyMuted")
        ntp.setWordWrap(True)
        time_l.addWidget(ntp)
        work.addWidget(self.time_row)

        self.rotation_row = Card(subtle=True)
        rot_l = QVBoxLayout(self.rotation_row)
        rot_l.setContentsMargins(14, 12, 14, 12)
        rot_l.setSpacing(9)
        self.rotation_current = QLabel("Получение текущей ориентации…")
        self.rotation_current.setObjectName("Muted")
        rot_l.addWidget(self.rotation_current)
        self.rotation_combo = QComboBox()
        self.rotation_combo.setObjectName("NewAgentRotation")
        for label, code in self.ROTATIONS:
            self.rotation_combo.addItem(label, code)
        self.rotation_combo.setMinimumHeight(43)
        rot_l.addWidget(self.rotation_combo)
        work.addWidget(self.rotation_row)

        self.bitrate_row = Card(subtle=True)
        bitrate_l = QVBoxLayout(self.bitrate_row)
        bitrate_l.setContentsMargins(14, 12, 14, 12)
        bitrate_l.setSpacing(8)
        self.bitrate_current = QLabel("Получение текущего битрейта…")
        self.bitrate_current.setObjectName("Muted")
        bitrate_l.addWidget(self.bitrate_current)
        self.bitrate = QSpinBox()
        self.bitrate.setObjectName("NewAgentBitrate")
        self.bitrate.setRange(1, 100000)
        self.bitrate.setValue(1024)
        self.bitrate.setSuffix(" Кбит/с")
        self.bitrate.setMinimumHeight(43)
        bitrate_l.addWidget(self.bitrate)
        work.addWidget(self.bitrate_row)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.ok_btn = QPushButton("Применить настройки")
        self.ok_btn.setObjectName("NewAgentApply")
        self.ok_btn.setProperty("role", "primary")
        self.ok_btn.setMinimumHeight(43)
        self.ok_btn.clicked.connect(self._execute_selected)
        button_row.addWidget(self.ok_btn)
        work.addLayout(button_row)
        outer.addWidget(self.actions_card)

        self.status = TaskStatus()
        self.status.setObjectName("NewAgentStatus")
        self.status.hide()
        outer.addWidget(self.status)
        outer.addStretch(1)

        self.action_combo.currentIndexChanged.connect(self._action_changed)
        self.camera_card.hide()
        self.actions_card.hide()
        self._action_changed()

    def _sync_actions(self):
        found = self.camera is not None
        action = self.action_combo.currentData() or ""
        self.find_btn.setEnabled(not self._busy)
        self.serial.setEnabled(not self._busy)
        self.camera_card.setVisible(found)
        self.actions_card.setVisible(found)
        self.action_combo.setEnabled(found and not self._busy)
        self.timezone.setEnabled(found and not self._busy)
        self.rotation_combo.setEnabled(found and not self._busy)
        self.bitrate.setEnabled(found and not self._busy)
        ready = found and not self._busy and bool(action)
        if action == "rotation":
            ready = ready and self._image_enhance is not None
        if action == "bitrate":
            ready = ready and self._video_streams is not None
        self.ok_btn.setEnabled(ready)

    def _action_changed(self, *_):
        action = self.action_combo.currentData() or ""
        self.time_row.setVisible(action == "time")
        self.rotation_row.setVisible(action == "rotation")
        self.bitrate_row.setVisible(action == "bitrate")
        if self.camera and not self._busy:
            if action == "rotation":
                self._image_enhance = None
                self.rotation_current.setText("Получаю текущую ориентацию…")
                self._load_setting("rotation")
            elif action == "bitrate":
                self._video_streams = None
                self.bitrate_current.setText("Получаю текущий битрейт…")
                self._load_setting("bitrate")
        self._sync_actions()

    def _set_busy(self, busy: bool, title="", detail=""):
        self._busy = busy
        if busy:
            self.status.show()
            self.status.busy_inline(title, detail)
        self._sync_actions()

    def _spawn(self, task):
        threading.Thread(target=task, name="new-agent-camera-operation", daemon=True).start()

    def _find(self):
        serial = self.serial.text().strip()
        if self._busy:
            return
        if not serial:
            self.status.show()
            self.status.warning("Не указан серийный номер", "Введите serial_number камеры LD.")
            return
        self._generation += 1
        request_id = self._generation
        self.camera = None
        self._image_enhance = None
        self._video_streams = None
        self.action_combo.blockSignals(True)
        self.action_combo.setCurrentIndex(0)
        self.action_combo.blockSignals(False)
        self._set_busy(True, "Поиск камеры", "Проверяю список agent-vcore.")
        def work():
            try:
                payload = self.service.find_camera(serial)
                self.operationReady.emit(request_id, "found", payload)
            except Exception as exc:
                self.operationReady.emit(request_id, "error", ("Камера не найдена", str(exc)))
        self._spawn(work)

    def _load_setting(self, action):
        if not self.camera or self._busy:
            return
        camera = self.camera
        request_id = self._generation
        self._set_busy(True, "Чтение настроек", camera.server)
        def work():
            try:
                result = (self.service.get_image_enhance(camera) if action == "rotation"
                          else self.service.get_video_streams(camera))
                self.operationReady.emit(request_id, action + "_loaded", result)
            except Exception as exc:
                self.operationReady.emit(request_id, "error", ("Ошибка чтения настроек", str(exc)))
        self._spawn(work)

    def _execute_selected(self):
        if not self.camera or self._busy:
            return
        action = self.action_combo.currentData() or ""
        args = {
            "time": self.timezone.value(),
            "rotation": self.rotation_combo.currentData(),
            "bitrate": self.bitrate.value(),
        }
        if action not in ("time", "rotation", "bitrate"):
            return
        camera = self.camera
        title = {"time": "Настроить время и NTP",
                 "rotation": "Изменить ориентацию",
                 "bitrate": "Изменить битрейт"}[action]
        value = args[action]
        if action == "rotation" and self._image_enhance is None:
            return
        if action == "bitrate" and self._video_streams is None:
            return
        check = ConfirmDialog(
            "Подтвердить настройки камеры",
            f"Камера: {camera.serial_number}\n"
            f"Agent-vcore: {camera.server}\n"
            f"Действие: {title}\n"
            f"Выбранное значение: {value}\n\n"
            "Настройки будут записаны на реальную камеру.",
            "Применить", False, self,
        )
        if check.exec() != QDialog.DialogCode.Accepted:
            return
        request_id = self._generation
        old_image = self._image_enhance
        old_video = self._video_streams
        self._set_busy(True, title, camera.serial_number)
        def work():
            try:
                if action == "time":
                    replies = self.service.configure_time(camera, value)
                    detail = "Time → NTP → SyncMode · HTTP " + ", ".join(
                        str(r.status_code) for r in replies
                    )
                elif action == "rotation":
                    before, after = self.service.set_image_rotation(camera, old_image, value)
                    detail = f"Ориентация: {self._rotation_label(before)} → {self._rotation_label(after)}"
                else:
                    before, after = self.service.set_bitrate(camera, old_video, value)
                    detail = f"Битрейт: {before} → {after} Кбит/с"
                self.operationReady.emit(request_id, "done", (title, detail))
            except Exception as exc:
                self.operationReady.emit(request_id, "error", ("Настройки не применены", str(exc)))
        self._spawn(work)

    @classmethod
    def _rotation_label(cls, value):
        for label, number in cls.ROTATIONS:
            if number == int(value):
                return label
        return str(value)

    def _on_ready(self, request_id, kind, payload):
        if request_id != self._generation:
            return
        self._busy = False
        if kind == "found":
            self.camera = payload
            c = payload
            self.camera_pill.set_status("Камера найдена", "success")
            self.info.setText(
                f"Серийный номер: {c.serial_number}\n"
                f"Agent-vcore: {c.server}\n"
                f"Устройство: {c.vendor or '—'} · {c.model or '—'}\n"
                f"Версия агента: {c.version_text} · Подключение: {c.connect_time or '—'}"
            )
            self.status.done("Камера найдена", c.serial_number)
        elif kind == "rotation_loaded":
            self._image_enhance = payload
            value = int(payload.get("ImageRotation", -1))
            self.rotation_current.setText("Сейчас: " + self._rotation_label(value))
            idx = self.rotation_combo.findData(value)
            if idx >= 0:
                self.rotation_combo.setCurrentIndex(idx)
            self.status.done("Настройки изображения получены", self._rotation_label(value))
        elif kind == "bitrate_loaded":
            self._video_streams = payload
            try:
                value = str(self.service._main_video_encode_info(payload).get("BitRate", ""))
                self.bitrate_current.setText(f"Сейчас: {value} Кбит/с")
                self.bitrate.setValue(int(value))
                self.status.done("Настройки потока получены", f"{value} Кбит/с")
            except (TypeError, ValueError, KeyError) as exc:
                self._video_streams = None
                self.status.error("Ошибка чтения битрейта", str(exc))
        elif kind == "done":
            title, detail = payload
            self.status.done(title + " · выполнено", detail)
            if self.action_combo.currentData() == "rotation":
                self._image_enhance = None
            if self.action_combo.currentData() == "bitrate":
                self._video_streams = None
        else:
            title, detail = payload
            self.status.error(title, detail)
        self._sync_actions()
