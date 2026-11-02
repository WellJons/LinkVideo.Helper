from __future__ import annotations

"""Unified camera search with thumbnail cards and a separate detail dialog."""

from concurrent.futures import ThreadPoolExecutor
import threading

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.b2o_camera_preview import fetch_camera_preview
from linkvideo_vpn_helper.services.camera_lookup import (
    CameraChoice, CameraSearchError, search_all_cameras,
)
from linkvideo_vpn_helper.ui.camera_detail_dialog import CameraDetailDialog
from linkvideo_vpn_helper.ui.components import Card, StatusPill


class CameraDiagnosticsPanel(QWidget):
    resultReady = Signal(int, object, str)
    thumbnailReady = Signal(int, int, object)

    def __init__(self, settings, parent=None, *, inspector=None, work_account=None,
                 customer_search=search_all_cameras, preview_loader=fetch_camera_preview):
        super().__init__(parent)
        self._settings = settings
        self.work_account = work_account
        self._customer_search = customer_search
        self._preview_loader = preview_loader
        self._request = 0
        self._waiting_for_auth = False
        self._active_card = None
        self._thumb_labels = {}
        self._build()
        self.resultReady.connect(self._on_result)
        self.thumbnailReady.connect(self._on_thumbnail)
        if self.work_account is not None:
            self.work_account.b2oChanged.connect(self._on_b2o_changed)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(0)
        self.view_stack = QStackedWidget(self)
        self.view_stack.setObjectName("CameraInformationPages")
        self.search_view = QWidget(self)
        layout = QVBoxLayout(self.search_view)
        layout.setContentsMargins(0, 6, 0, 8)
        layout.setSpacing(14)

        search_card = Card(kind="hero")
        controls = QVBoxLayout(search_card)
        controls.setSpacing(11)
        title = QLabel("Информация по камере")
        title.setObjectName("SectionTitle")
        controls.addWidget(title)
        caption = QLabel(
            "Поиск по логину клиента, ID или серийному номеру — автоматически. "
            "Выберите камеру из списка, чтобы открыть подробную диагностику."
        )
        caption.setObjectName("Muted")
        caption.setWordWrap(True)
        controls.addWidget(caption)
        fields = QFormLayout()
        self.camera_id = QLineEdit()
        self.camera_id.setObjectName("B2OCameraDiagnosticId")
        self.camera_id.setPlaceholderText("Логин клиента, ID или серийный номер")
        self.camera_id.returnPressed.connect(self._check)
        fields.addRow("Поиск", self.camera_id)
        controls.addLayout(fields)
        buttons = QHBoxLayout()
        self.run_button = QPushButton("Найти камеры")
        self.run_button.setObjectName("B2OCameraDiagnosticRun")
        self.run_button.setProperty("role", "primary")
        self.run_button.clicked.connect(self._check)
        buttons.addWidget(self.run_button)
        self.status = StatusPill("Ожидание", "neutral")
        buttons.addWidget(self.status)
        buttons.addStretch(1)
        controls.addLayout(buttons)
        layout.addWidget(search_card)

        results = Card()
        body = QVBoxLayout(results)
        body.setSpacing(10)
        label = QLabel("Камеры")
        label.setObjectName("CardTitle")
        body.addWidget(label)
        self.summary = QLabel("Введите логин клиента, ID или серийный номер камеры.")
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        body.addWidget(self.summary)
        self.list = QListWidget()
        self.list.setObjectName("CameraSearchResultList")
        self.list.setSpacing(8)
        # Show complete rows, not a 190px viewport with the second camera
        # half-hidden. The page scaffold provides scrolling for long lists.
        self.list.setMinimumHeight(260)
        self.list.setMaximumHeight(16777215)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemClicked.connect(self._open_camera)
        self.list.hide()
        body.addWidget(self.list)
        layout.addWidget(results)
        layout.addStretch(1)
        self.view_stack.addWidget(self.search_view)
        root.addWidget(self.view_stack, 1)

    def _check(self):
        if not self.run_button.isEnabled():
            return
        query = self.camera_id.text().strip()
        if not query or len(query) > 100:
            self.status.set_status("Уточните запрос", "warning")
            self.summary.setText("Введите логин, ID или серийный номер.")
            return
        account = self.work_account
        if account is not None and not account.has_credentials:
            self.status.set_status("Нужен вход", "warning")
            self.summary.setText("Войдите в учётную запись «Админка».")
            account.loginRequested.emit()
            return
        if account is not None and not B2OService(self._settings).token():
            self._waiting_for_auth = True
            self.run_button.setEnabled(False)
            self.status.set_status("Подключение", "warning")
            self.summary.setText("Проверяю авторизацию админки…")
            if not account.b2o_busy:
                account.ensure_b2o()
            return

        self._waiting_for_auth = False
        self._request += 1
        request = self._request
        self.list.clear()
        self.list.hide()
        self._thumb_labels.clear()
        self.run_button.setEnabled(False)
        self.status.set_status("Поиск", "warning")
        self.summary.setText("Ищу камеры и проверяю реальные совпадения…")

        def worker():
            try:
                b2o = B2OService(self._settings)
                credentials = account.credentials if account is not None else None
                cameras = self._customer_search(query, b2o, credentials)
                self.resultReady.emit(request, tuple(cameras), "")
            except CameraSearchError as exc:
                self.resultReady.emit(request, (), str(exc))
            except Exception:
                self.resultReady.emit(
                    request, (), "Не удалось выполнить поиск. "
                    "Проверьте доступ к админке и соединение.",
                )

        threading.Thread(target=worker, daemon=True,
                         name="camera-unified-lookup").start()

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

    def _camera_row(self, choice: CameraChoice, index: int) -> QWidget:
        # Same compact image/name/status treatment as Clients and Tariffs.
        row = Card(subtle=True)
        row.setObjectName("CameraSearchRow")
        content = QHBoxLayout(row)
        content.setContentsMargins(12, 10, 12, 10)
        content.setSpacing(13)

        thumb = QLabel("Превью\nзагружается…")
        thumb.setObjectName("CameraSearchThumbnail")
        thumb.setFixedSize(160, 92)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setWordWrap(True)
        thumb.setStyleSheet(
            "QLabel#CameraSearchThumbnail { background: palette(alternate-base); "
            "border: 1px solid palette(mid); border-radius: 8px; }"
        )
        self._thumb_labels[index] = thumb
        content.addWidget(thumb)

        middle = QVBoxLayout()
        middle.setSpacing(5)
        name = QLabel(choice.label or choice.camera_id)
        name.setObjectName("Value")
        name.setWordWrap(True)
        middle.addWidget(name)
        number, labelled = B2OService.normalize_camera_id(
            choice.camera_id, choice.operator_id
        )
        identifiers = QLabel(f"ID: {labelled}   ·   {choice.country}")
        identifiers.setObjectName("TinyMuted")
        identifiers.setWordWrap(True)
        middle.addWidget(identifiers)
        if choice.customer_login:
            customer = QLabel(f"Клиент: {choice.customer_login}")
            customer.setObjectName("Muted")
            middle.addWidget(customer)
        if choice.tariff:
            tariff = QLabel(f"Тариф: {choice.tariff}")
            tariff.setObjectName("TinyMuted")
            tariff.setWordWrap(True)
            middle.addWidget(tariff)
        middle.addStretch(1)
        content.addLayout(middle, 1)

        side = QVBoxLayout()
        if choice.status:
            active = choice.status.casefold() in ("online", "активный", "активен")
            status = StatusPill(choice.status,
                                "success" if active else "neutral")
            side.addWidget(status, 0, Qt.AlignmentFlag.AlignRight)
        side.addStretch(1)
        open_btn = QPushButton("Подробнее")
        open_btn.setObjectName("CameraSearchOpen")
        open_btn.setProperty("role", "soft")
        open_btn.setMinimumHeight(36)
        open_btn.clicked.connect(
            lambda _checked=False, selected=choice: self._show_camera(selected)
        )
        side.addWidget(open_btn)
        content.addLayout(side)
        return row

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
                "Не найдено подтверждённых камер. Проверьте логин, "
                "ID/серийный номер и права учётной записи."
            )
            return
        cameras = tuple(c for c in cameras if isinstance(c, CameraChoice))
        self.status.set_status("Найдено", "success")
        self.summary.setText(
            f"Найдено камер: {len(cameras)}. Нажмите «Подробнее» "
            "или выберите строку камеры."
        )
        self.list.clear()
        self._thumb_labels.clear()
        for index, choice in enumerate(cameras):
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 116))
            item.setData(Qt.ItemDataRole.UserRole, choice)
            self.list.addItem(item)
            self.list.setItemWidget(item, self._camera_row(choice, index))
        # Keep the list readable while isolating it from the VPN tab's
        # layout. For long results use QListWidget's own vertical scroll
        # instead of inflating the entire shared QTabWidget to 2000+ px.
        self.list.setFixedHeight(min(510, max(166, len(cameras) * 132 + 18)))
        self.list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.list.show()
        # Fetch at most 24 thumbnails, max three at once, without freezing Qt.
        token = B2OService(self._settings).token()
        if token:
            threading.Thread(
                target=self._load_thumbnails,
                args=(request, cameras[:24], token),
                name="camera-list-preview", daemon=True,
            ).start()
        else:
            for thumb in self._thumb_labels.values():
                thumb.setText("Нет входа в админку")

    def _load_thumbnails(self, request, cameras, token):
        def fetch(index, camera):
            try:
                number, _ = B2OService.normalize_camera_id(
                    camera.camera_id, camera.operator_id
                )
                data = self._preview_loader(camera.country, number, token)
                return index, data
            except Exception:
                return index, None
        with ThreadPoolExecutor(max_workers=3) as pool:
            for index, blob in pool.map(
                lambda indexed: fetch(*indexed), enumerate(cameras)
            ):
                self.thumbnailReady.emit(request, index, blob)

    def _on_thumbnail(self, request, index, blob):
        if request != self._request:
            return
        thumb = self._thumb_labels.get(index)
        if thumb is None:
            return
        pixmap = QPixmap()
        if isinstance(blob, bytes) and pixmap.loadFromData(blob):
            thumb.setPixmap(pixmap.scaled(
                thumb.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
            thumb.setText("")
        else:
            thumb.setPixmap(QPixmap())
            thumb.setText("Нет превью")

    def _open_camera(self, item):
        camera = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(camera, CameraChoice):
            self._show_camera(camera)

    def _show_camera(self, camera):
        # Match VPN client navigation: replace search results in the same
        # workspace, not a floating window with an independent taskbar entry.
        previous = self._active_card
        card = CameraDetailDialog(camera, self._settings, self.view_stack)
        card.backRequested.connect(self._back_to_results)
        self._active_card = card
        self.view_stack.addWidget(card)
        self.view_stack.setCurrentWidget(card)
        if previous is not None:
            previous.deactivate()
            self.view_stack.removeWidget(previous)
            previous.deleteLater()

    def _back_to_results(self):
        self.view_stack.setCurrentWidget(self.search_view)
        # Keep found cameras and their previews intact when navigating back.
        if self._active_card is not None:
            card = self._active_card
            self._active_card = None
            card.deactivate()
            self.view_stack.removeWidget(card)
            card.deleteLater()
