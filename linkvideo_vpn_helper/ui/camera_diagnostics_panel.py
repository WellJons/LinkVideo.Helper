from __future__ import annotations

"""Camera lookup is a lightweight selector; full diagnostics live in a dialog."""

import threading
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.camera_lookup import (
    CameraChoice, CameraSearchError, direct_choice, looks_like_camera_identifier,
    search_by_login,
)
from linkvideo_vpn_helper.ui.camera_detail_dialog import CameraDetailDialog
from linkvideo_vpn_helper.ui.components import Card, StatusPill


class CameraDiagnosticsPanel(QWidget):
    resultReady = Signal(int, object, str)

    def __init__(self, settings, parent=None, *, inspector=None, work_account=None,
                 customer_search=search_by_login):
        super().__init__(parent)
        self._settings = settings
        self.work_account = work_account
        self._customer_search = customer_search
        self._request = 0
        self._waiting_for_auth = False
        self._active_card = None
        self._build()
        self.resultReady.connect(self._on_result)
        if self.work_account is not None:
            self.work_account.b2oChanged.connect(self._on_b2o_changed)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 18)
        layout.setSpacing(14)

        card = Card(kind="hero")
        controls = QVBoxLayout(card)
        controls.setSpacing(11)
        title = QLabel("Информация по камере")
        title.setObjectName("SectionTitle")
        controls.addWidget(title)
        caption = QLabel(
            "Найдите камеры клиента по логину или укажите ID/серийный номер. "
            "Выберите камеру, чтобы открыть превью и диагностику."
        )
        caption.setObjectName("Muted")
        caption.setWordWrap(True)
        controls.addWidget(caption)

        fields = QFormLayout()
        self.camera_id = QLineEdit()
        self.camera_id.setObjectName("B2OCameraDiagnosticId")
        self.camera_id.setPlaceholderText("Логин клиента, ID или серийный номер камеры")
        self.camera_id.returnPressed.connect(self._check)
        fields.addRow("Поиск", self.camera_id)

        self.mode = QComboBox()
        self.mode.setObjectName("CameraLookupMode")
        self.mode.addItem("Автоматически", "auto")
        self.mode.addItem("Логин клиента", "login")
        self.mode.addItem("ID / серийный номер", "camera")
        fields.addRow("Искать по", self.mode)

        self.operator = QComboBox()
        self.operator.setObjectName("B2OCameraDiagnosticOperator")
        for name, number in (("Россия", 241), ("Беларусь", 1741),
                             ("Казахстан", 1721)):
            self.operator.addItem(name, number)
        fields.addRow("Страна", self.operator)
        controls.addLayout(fields)

        actions = QHBoxLayout()
        self.run_button = QPushButton("Найти камеры")
        self.run_button.setObjectName("B2OCameraDiagnosticRun")
        self.run_button.setProperty("role", "primary")
        self.run_button.clicked.connect(self._check)
        actions.addWidget(self.run_button)
        self.status = StatusPill("Ожидание", "neutral")
        actions.addWidget(self.status)
        actions.addStretch(1)
        controls.addLayout(actions)
        layout.addWidget(card)

        results = Card()
        body = QVBoxLayout(results)
        label = QLabel("Результаты поиска")
        label.setObjectName("CardTitle")
        body.addWidget(label)
        self.summary = QLabel("Введите логин клиента или ID камеры.")
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        body.addWidget(self.summary)
        self.list = QListWidget()
        self.list.setObjectName("CameraSearchResultList")
        self.list.setSpacing(4)
        self.list.setMinimumHeight(190)
        self.list.setMaximumHeight(410)
        self.list.itemClicked.connect(self._open_camera)
        self.list.hide()
        body.addWidget(self.list)
        layout.addWidget(results)
        layout.addStretch(1)

    def _check(self):
        if not self.run_button.isEnabled():
            return
        query = self.camera_id.text().strip()
        if not query or len(query) > 100:
            self.status.set_status("Уточните запрос", "warning")
            self.summary.setText("Укажите логин, ID или серийный номер камеры.")
            return
        account = self.work_account
        if account is not None and not account.has_credentials:
            self.status.set_status("Нужен вход", "warning")
            self.summary.setText("Войдите в аккаунт «Админка».")
            account.loginRequested.emit()
            return
        # Search may use either CRM or B2O. B2O token is requested only when
        # absent and is shared with the archive/diagnostics pages.
        if account is not None and not B2OService(self._settings).token():
            if account.b2o_busy:
                self._waiting_for_auth = True
                self.run_button.setEnabled(False)
                return
            self._waiting_for_auth = True
            self.run_button.setEnabled(False)
            self.status.set_status("Подключение", "warning")
            self.summary.setText("Проверяю авторизацию админки…")
            account.ensure_b2o()
            return

        self._waiting_for_auth = False
        self._request += 1
        request = self._request
        mode = self.mode.currentData()
        operator = int(self.operator.currentData())
        self.list.clear()
        self.list.hide()
        self.run_button.setEnabled(False)
        self.status.set_status("Поиск", "warning")
        self.summary.setText("Ищу камеры…")

        if mode == "camera" or (mode == "auto" and looks_like_camera_identifier(query)):
            try:
                choice = direct_choice(query, operator)
                self.resultReady.emit(request, (choice,), "")
            except CameraSearchError as exc:
                self.resultReady.emit(request, (), str(exc))
            return

        def worker():
            try:
                from linkvideo_vpn_helper.services.agent_b2o_preview import (
                    b2o_camera_for_serial,
                )
                b2o = B2OService(self._settings)
                login = account.credentials if account is not None else None
                if mode == "camera":
                    cameras = (direct_choice(query, operator),)
                else:
                    cameras = self._customer_search(query, b2o, login)
                    # The auto mode also accepts exact alphanumeric camera serials.
                    if not cameras and mode == "auto" and b2o.token():
                        match = b2o_camera_for_serial(query, b2o.token(), b2o)
                        if match:
                            country, camera_id = match
                            op = {"Россия": 241, "Беларусь": 1741,
                                  "Казахстан": 1721}[country]
                            cameras = (direct_choice(camera_id, op),)
                self.resultReady.emit(request, tuple(cameras), "")
            except CameraSearchError as exc:
                self.resultReady.emit(request, (), str(exc))
            except Exception:
                self.resultReady.emit(
                    request, (), "Не удалось выполнить поиск. Проверьте "
                    "подключение админки и доступ к CRM/B2O.",
                )
        threading.Thread(target=worker, daemon=True,
                         name="camera-customer-lookup").start()

    def _on_b2o_changed(self, ok, error):
        if not self._waiting_for_auth:
            return
        self._waiting_for_auth = False
        self.run_button.setEnabled(True)
        if ok:
            self._check()
        else:
            self.status.set_status("Нет доступа", "danger")
            self.summary.setText("Не удалось подключить учётную запись админки.")

    def _on_result(self, request, cameras, error):
        if request != self._request:
            return
        self.run_button.setEnabled(True)
        if error:
            self.status.set_status("Ошибка", "danger")
            self.summary.setText(error)
            return
        if not cameras:
            self.status.set_status("Не найдено", "warning")
            self.summary.setText(
                "Камеры не найдены. Проверьте логин и права. "
                "Для России B2O должен возвращать логин владельца."
            )
            return
        self.status.set_status("Найдено", "success")
        self.summary.setText(
            f"Найдено камер: {len(cameras)}. Нажмите на камеру для подробной информации."
        )
        self.list.clear()
        for camera in cameras:
            if not isinstance(camera, CameraChoice):
                continue
            title = camera.label
            detail = " · ".join(
                part for part in (camera.country, camera.status, camera.tariff)
                if part
            )
            item = QListWidgetItem(
                f"{title}   •   {detail}" if detail else title
            )
            item.setData(Qt.ItemDataRole.UserRole, camera)
            self.list.addItem(item)
        self.list.show()

    def _open_camera(self, item):
        camera = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(camera, CameraChoice):
            return
        self._active_card = CameraDetailDialog(camera, self._settings, self)
        self._active_card.show()
        self._active_card.raise_()
        self._active_card.activateWindow()
