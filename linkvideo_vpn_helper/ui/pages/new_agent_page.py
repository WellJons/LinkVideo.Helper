from __future__ import annotations

"""Camera settings for LD devices found through authenticated agent-vcore."""

import threading
from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QAbstractSpinBox, QBoxLayout, QComboBox, QDialog, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.agent_service import AgentCamera, AgentService
from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.agent_b2o_preview import load_b2o_preview_for_serial
from linkvideo_vpn_helper.ui.components import (
    Card, PageHeader, StatusPill, TaskStatus, WorkflowStep, button_feedback, build_page_scaffold,
)
from linkvideo_vpn_helper.ui.dialogs import ConfirmDialog


class NewAgentPage(QWidget):
    operationReady = Signal(int, str, object)
    cameraPreviewReady = Signal(int, str, object)
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
        self._preview_request = 0
        self._image_enhance = None
        self._video_streams = None
        self.operationReady.connect(self._on_ready)
        self.cameraPreviewReady.connect(self._on_preview_ready)
        self._build()

    def _build(self):
        scroll, canvas, outer = build_page_scaffold(
            self, max_width=1360, min_width=760, margins=28, spacing=16
        )
        self.page_scroll = scroll
        self.page_canvas = canvas
        outer.addWidget(PageHeader(
            "Новый Агент",
            "Поиск LD-камер по серийному номеру и управление настройками через agent-vcore."
        ))

        self.workspace = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.workspace.setSpacing(16)
        form = Card(kind="hero")
        root = QVBoxLayout(form)
        root.setContentsMargins(22, 20, 22, 22)
        root.setSpacing(15)

        root.addWidget(WorkflowStep(
            1, "Поиск камеры",
            "Укажите серийный номер LD-камеры. Сервер агента определится автоматически."
        ))
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

        self.camera_card = Card(kind="success")
        cam = QVBoxLayout(self.camera_card)
        cam.setContentsMargins(16, 14, 16, 14)
        cam.setSpacing(9)
        cam_top = QHBoxLayout()
        cam_title = QLabel("Найденная камера")
        cam_title.setObjectName("CardTitle")
        self.camera_pill = StatusPill("Камера найдена", "success")
        cam_top.addWidget(cam_title, 1)
        cam_top.addWidget(self.camera_pill)
        cam.addLayout(cam_top)
        self.info = QLabel("")
        self.info.setObjectName("Value")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cam.addWidget(self.info)
        root.addWidget(self.camera_card)

        self.actions_card = QWidget()
        self.actions_card.setObjectName("NewAgentActionsCard")
        work = QVBoxLayout(self.actions_card)
        work.setContentsMargins(0, 4, 0, 0)
        work.setSpacing(12)
        work.addWidget(WorkflowStep(
            2, "Настройки камеры",
            "Выберите параметр. Текущее значение загрузится перед изменением."
        ))
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
        tz_label = QLabel("Часовой пояс камеры")
        tz_label.setObjectName("TinyMuted")
        time_l.addWidget(tz_label)
        self.timezone = QComboBox()
        self.timezone.setObjectName("NewAgentTimezone")
        for offset in range(-12, 15):
            value = f"UTC{offset:+d} · {'Местное время' if offset == 7 else 'часовой пояс'}"
            self.timezone.addItem(value, offset)
        self.timezone.setCurrentIndex(self.timezone.findData(7))
        self.timezone.setMinimumHeight(44)
        self.timezone.setToolTip("Укажите часовой пояс, который будет установлен на камере")
        time_l.addWidget(self.timezone)
        ntp = QLabel("Синхронизация с ntp.goodline.info · каждые 3600 секунд")
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
        # The combo stores the API value for backwards compatibility;
        # users select orientation through large, clearly labelled tiles.
        self.rotation_combo = QComboBox()
        self.rotation_combo.setObjectName("NewAgentRotation")
        for label, code in self.ROTATIONS:
            self.rotation_combo.addItem(label, code)
        self.rotation_combo.hide()
        rot_l.addWidget(self.rotation_combo)
        options = QGridLayout()
        options.setHorizontalSpacing(9)
        options.setVerticalSpacing(9)
        self.rotation_buttons = []
        for index, title in enumerate((
            "Обычное", "Отразить по вертикали",
            "Отразить по горизонтали", "Повернуть на 180°",
        )):
            tile = QPushButton(title)
            tile.setObjectName(f"NewAgentRotationChoice{index}")
            tile.setProperty("themeChoice", "true")
            tile.setProperty("active", "false")
            tile.setMinimumHeight(46)
            tile.setCheckable(True)
            tile.clicked.connect(
                lambda checked=False, idx=index: self.rotation_combo.setCurrentIndex(idx)
            )
            options.addWidget(tile, index // 2, index % 2)
            self.rotation_buttons.append(tile)
        self.rotation_combo.currentIndexChanged.connect(self._sync_rotation_buttons)
        rot_l.addLayout(options)
        self._sync_rotation_buttons()
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
        self.bitrate.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.bitrate.setMinimumHeight(44)
        bitrate_l.addWidget(self.bitrate)
        preset_label = QLabel("Быстрый выбор битрейта")
        preset_label.setObjectName("TinyMuted")
        bitrate_l.addWidget(preset_label)
        presets = QGridLayout()
        presets.setSpacing(7)
        self.bitrate_presets = []
        for index, rate in enumerate((512, 1024, 2048, 4096, 8192)):
            preset = QPushButton(f"{rate // 1024:g} Мбит/с" if rate >= 1024 else "512 Кбит/с")
            preset.setObjectName(f"NewAgentBitratePreset{rate}")
            preset.setProperty("themeChoice", "true")
            preset.setProperty("active", "false")
            preset.setCheckable(True)
            preset.setMinimumHeight(40)
            preset.clicked.connect(lambda checked=False, value=rate: self.bitrate.setValue(value))
            presets.addWidget(preset, index // 3, index % 3)
            self.bitrate_presets.append((rate, preset))
        bitrate_l.addLayout(presets)
        self.bitrate.valueChanged.connect(self._sync_bitrate_presets)
        self._sync_bitrate_presets()
        work.addWidget(self.bitrate_row)

        button_row = QHBoxLayout()
        self.refresh_setting_btn = QPushButton("Обновить настройки")
        self.refresh_setting_btn.setObjectName("NewAgentReloadSettings")
        self.refresh_setting_btn.clicked.connect(self._refresh_selected_setting)
        button_row.addWidget(self.refresh_setting_btn)
        button_row.addStretch(1)
        self.ok_btn = QPushButton("Применить настройки")
        self.ok_btn.setObjectName("NewAgentApply")
        self.ok_btn.setProperty("role", "primary")
        self.ok_btn.setMinimumHeight(46)
        self.ok_btn.clicked.connect(self._execute_selected)
        button_row.addWidget(self.ok_btn)
        work.addLayout(button_row)
        root.addWidget(self.actions_card)

        self.status = TaskStatus()
        self.status.setObjectName("NewAgentStatus")
        self.status.hide()
        root.addWidget(self.status)
        root.addStretch(1)
        self.workspace.addWidget(form, 6)

        self.result = Card()
        side = QVBoxLayout(self.result)
        side.setContentsMargins(22, 20, 22, 22)
        side.setSpacing(12)
        result_title = QLabel("Статус и данные")
        result_title.setObjectName("SectionTitle")
        side.addWidget(result_title)
        result_hint = QLabel(
            "Здесь отображаются найденная камера, её сервер и результат последнего действия."
        )
        result_hint.setObjectName("Muted")
        result_hint.setWordWrap(True)
        side.addWidget(result_hint)

        self.preview_label = QLabel("Превью появится после поиска")
        self.preview_label.setObjectName("NewAgentCameraPreview")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setWordWrap(True)
        self.preview_label.setMinimumHeight(190)
        self.preview_label.setMaximumHeight(245)
        self.preview_label.setStyleSheet(
            "QLabel#NewAgentCameraPreview { background: palette(alternate-base);"
            " color: palette(mid); border: 1px solid palette(midlight);"
            " border-radius: 10px; padding: 7px; }"
        )
        self.preview_label.hide()
        side.addWidget(self.preview_label)
        self.preview_btn = QPushButton("Обновить кадр")
        self.preview_btn.setObjectName("NewAgentRefreshPreview")
        self.preview_btn.setEnabled(False)
        self.preview_btn.hide()
        self.preview_btn.clicked.connect(self._start_preview)
        side.addWidget(self.preview_btn, 0, Qt.AlignmentFlag.AlignRight)

        self.result_empty = Card(subtle=True)
        self.result_empty.setObjectName("CreateResultEmpty")
        empty = QVBoxLayout(self.result_empty)
        empty.setContentsMargins(14, 30, 14, 30)
        empty.setSpacing(9)
        empty.addStretch(1)
        self.result_symbol = QLabel("◇")
        self.result_symbol.setObjectName("CreateEmptySymbol")
        self.result_symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.addWidget(self.result_symbol)
        self.side_result_title = QLabel("Камера ещё не выбрана")
        self.side_result_title.setObjectName("CardTitle")
        self.side_result_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.side_result_title.setWordWrap(True)
        empty.addWidget(self.side_result_title)
        self.side_result_hint = QLabel("Введите серийный номер и нажмите «Найти камеру»")
        self.side_result_hint.setObjectName("TinyMuted")
        self.side_result_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.side_result_hint.setWordWrap(True)
        empty.addWidget(self.side_result_hint)
        empty.addStretch(1)
        self.result_empty.setMinimumHeight(230)
        side.addWidget(self.result_empty, 1)

        self.result_summary = Card(subtle=True)
        summary_l = QVBoxLayout(self.result_summary)
        summary_l.setContentsMargins(16, 15, 16, 15)
        summary_l.setSpacing(8)
        self.summary_status = StatusPill("Камера подключена", "success")
        summary_l.addWidget(self.summary_status, 0, Qt.AlignmentFlag.AlignLeft)
        self.summary_data = QLabel("")
        self.summary_data.setObjectName("Value")
        self.summary_data.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.summary_data.setWordWrap(True)
        summary_l.addWidget(self.summary_data)
        side.addWidget(self.result_summary, 1)
        self.copy_btn = QPushButton("Копировать данные")
        self.copy_btn.setObjectName("NewAgentCopy")
        self.copy_btn.clicked.connect(self._copy_data)
        self.copy_btn.setEnabled(False)
        side.addWidget(self.copy_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.workspace.addWidget(self.result, 4)

        outer.addLayout(self.workspace)
        outer.addStretch(1)
        self.action_combo.currentIndexChanged.connect(self._action_changed)
        self.camera_card.hide()
        self.actions_card.hide()
        self.result_summary.hide()
        self._action_changed()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "workspace"):
            direction = (
                QBoxLayout.Direction.TopToBottom if self.width() < 1060
                else QBoxLayout.Direction.LeftToRight
            )
            self.workspace.setDirection(direction)

    def _start_preview(self):
        if not self.camera:
            return
        token = ""
        if self.settings is not None:
            token = str(self.settings.value("b2o/token", "", str) or "").strip()
        self._preview_request += 1
        generation = self._preview_request
        serial = self.camera.serial_number
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText("Загрузка превью…")
        self.preview_label.show()
        self.preview_btn.show()
        self.preview_btn.setEnabled(True)
        if not token:
            self.preview_label.setText(
                "Для превью войдите в B2O в разделе «Скачать архив»."
            )
            return

        def work():
            try:
                # B2O authorization is reused; only an exact serial match is accepted.
                result = load_b2o_preview_for_serial(
                    serial, token, B2OService(self.settings)
                )
            except Exception:
                result = None
            self.cameraPreviewReady.emit(generation, serial, result)
        self._spawn(work)

    def _on_preview_ready(self, request_id, serial, payload):
        if (request_id != self._preview_request or self.camera is None
                or self.camera.serial_number != serial):
            return
        if payload is not None:
            country, camera_id, raw = payload
            if isinstance(raw, bytes):
                pixmap = QPixmap()
                if pixmap.loadFromData(raw):
                    self.preview_label.setPixmap(pixmap.scaled(
                        self.preview_label.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    ))
                    self.preview_label.setToolTip(
                        f"B2O: {country} · ID камеры {camera_id}"
                    )
                    self.preview_label.setText("")
                    return
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText(
            "Превью недоступно. Проверьте авторизацию B2O и наличие камеры у оператора."
        )

    def _copy_data(self):
        if not self.camera:
            return
        camera = self.camera
        text = (
            f"Серийный номер: {camera.serial_number}\n"
            f"Agent-vcore: {camera.server}\n"
            f"Устройство: {camera.vendor or '—'} · {camera.model or '—'}\n"
            f"Версия агента: {camera.version_text}\n"
            f"Подключение: {camera.connect_time or '—'}"
        )
        QGuiApplication.clipboard().setText(text)
        button_feedback(self.copy_btn)

    def _sync_actions(self):
        found = self.camera is not None
        action = self.action_combo.currentData() or ""
        self.find_btn.setEnabled(not self._busy)
        self.serial.setEnabled(not self._busy)
        self.camera_card.setVisible(found)
        self.actions_card.setVisible(found)
        self.copy_btn.setEnabled(found)
        self.action_combo.setEnabled(found and not self._busy)
        self.refresh_setting_btn.setEnabled(
            found and not self._busy and action in ("rotation", "bitrate")
        )
        self.timezone.setEnabled(found and not self._busy)
        self.rotation_combo.setEnabled(found and not self._busy)
        for tile in self.rotation_buttons:
            tile.setEnabled(found and not self._busy)
        self.bitrate.setEnabled(found and not self._busy)
        for _, preset in self.bitrate_presets:
            preset.setEnabled(found and not self._busy)
        ready = found and not self._busy and bool(action)
        if action == "rotation":
            ready = ready and self._image_enhance is not None
        if action == "bitrate":
            ready = ready and self._video_streams is not None
        self.ok_btn.setEnabled(ready)

    def _sync_rotation_buttons(self, *_):
        selected = self.rotation_combo.currentIndex()
        for index, tile in enumerate(self.rotation_buttons):
            active = index == selected
            tile.setChecked(active)
            tile.setProperty("active", "true" if active else "false")
            tile.style().unpolish(tile)
            tile.style().polish(tile)

    def _sync_bitrate_presets(self, *_):
        current = self.bitrate.value()
        for rate, tile in self.bitrate_presets:
            active = current == rate
            tile.setChecked(active)
            tile.setProperty("active", "true" if active else "false")
            tile.style().unpolish(tile)
            tile.style().polish(tile)

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

    def _refresh_selected_setting(self):
        if self.camera and not self._busy:
            action = self.action_combo.currentData()
            if action in ("rotation", "bitrate"):
                self._load_setting(action)

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
        self._preview_request += 1
        self.preview_label.hide()
        self.preview_btn.hide()
        self.preview_btn.setEnabled(False)
        request_id = self._generation
        self.camera = None
        self._image_enhance = None
        self._video_streams = None
        self.action_combo.blockSignals(True)
        self.action_combo.setCurrentIndex(0)
        self.action_combo.blockSignals(False)
        self.result_summary.hide()
        self.result_empty.show()
        self.result_symbol.setText("…")
        self.side_result_title.setText("Поиск камеры")
        self.side_result_hint.setText("Запрашиваю список камер на agent-vcore")
        self._set_busy(True, "Поиск камеры", "Проверяю список agent-vcore.")
        def work():
            try:
                payload = self.service.find_camera(serial)
                self.operationReady.emit(request_id, "found", payload)
            except Exception as exc:
                label = "Камера не найдена" if isinstance(exc, LookupError) else "Поиск не завершён"
                self.operationReady.emit(request_id, "error", (label, str(exc)))
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
            "time": self.timezone.currentData(),
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
                fresh = None
                if action == "time":
                    replies = self.service.configure_time(camera, value)
                    detail = "Time → NTP → SyncMode · HTTP " + ", ".join(
                        str(r.status_code) for r in replies
                    )
                elif action == "rotation":
                    before, after = self.service.set_image_rotation(camera, old_image, value)
                    detail = f"Ориентация: {self._rotation_label(before)} → {self._rotation_label(after)}"
                    try:
                        fresh = self.service.get_image_enhance(camera)
                    except Exception:
                        fresh = None  # A read error does not undo a confirmed write.
                else:
                    before, after = self.service.set_bitrate(camera, old_video, value)
                    detail = f"Битрейт: {before} → {after} Кбит/с"
                    try:
                        fresh = self.service.get_video_streams(camera)
                    except Exception:
                        fresh = None
                self.operationReady.emit(request_id, "done", (title, detail, action, fresh))
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
            self.result_empty.hide()
            self.result_summary.show()
            self.summary_status.set_status("Камера найдена", "success")
            self.summary_data.setText(
                f"Сервер\n{c.server}\n\n"
                f"Устройство\n{c.vendor or '—'} · {c.model or '—'}\n\n"
                f"Статус\nПодключена\n\n"
                f"Версия агента\n{c.version_text}"
            )
            self.status.done("Камера найдена", c.serial_number)
            self._start_preview()
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
            title, detail, action, fresh = payload
            self.status.done(title + " · выполнено", detail)
            self.summary_status.set_status("Настройки применены", "success")
            if action == "rotation":
                self._image_enhance = fresh
                if isinstance(fresh, dict):
                    rotation = int(fresh.get("ImageRotation", -1))
                    self.rotation_current.setText(
                        "Сейчас: " + self._rotation_label(rotation)
                    )
                    idx = self.rotation_combo.findData(rotation)
                    if idx >= 0:
                        self.rotation_combo.setCurrentIndex(idx)
                self._start_preview()
            elif action == "bitrate":
                self._video_streams = fresh
                if isinstance(fresh, dict):
                    try:
                        rate = int(self.service._main_video_encode_info(fresh)["BitRate"])
                        self.bitrate_current.setText(f"Сейчас: {rate} Кбит/с")
                        self.bitrate.setValue(rate)
                    except (TypeError, KeyError, ValueError):
                        self._video_streams = None
            if action in ("rotation", "bitrate") and fresh is None:
                # Re-read current state without repeating camera discovery.
                QTimer.singleShot(0, lambda name=action: self._load_setting(name))
        else:
            title, detail = payload
            self.status.error(title, detail)
            if self.camera is None:
                self.result_summary.hide()
                self.result_empty.show()
                self.result_symbol.setText("×")
                self.side_result_title.setText(title)
                self.side_result_hint.setText(detail)
            else:
                self.summary_status.set_status("Ошибка операции", "danger")
        self._sync_actions()
