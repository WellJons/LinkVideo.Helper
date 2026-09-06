from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from linkvideo_vpn_helper.services.cloud_vpnsync import CloudVPNSyncClient
from linkvideo_vpn_helper.ui.components import Card, PageHeader, TaskStatus, build_page_scaffold


BUSINESS_TZ = timezone(timedelta(hours=7))


_SOURCE_LABELS = {
    "desktop": "Helper",
    "RouterOS listen": "MikroTik",
    "RouterOS event": "MikroTik → БД",
    "VPNSync startup": "VPNSync",
    "server": "Сервер",
}

_ACTION_LABELS = {
    "auth.login": "Вход на облачный сервер",
    "sync.snapshot": "Синхронизация состояния",
    "routeros.event": "Событие MikroTik /listen",
    "routeros.changed": "Изменение RouterOS",
    "routeros.added": "Добавление RouterOS",
    "routeros.removed": "Удаление RouterOS",
    "client.create": "Создание VPN-клиента",
    "client.delete": "Удаление VPN-клиента",
    "client.delete.preflight": "Проверка перед удалением",
    "client.disconnect": "Отключение VPN-сессии",
    "client.password_change": "Смена пароля",
    "client.enabled_change": "Включение / отключение клиента",
    "nat.add_ports": "Добавление NAT-портов",
    "nat.remove_port": "Удаление NAT-порта",
    "nat.enabled_change": "Включение / отключение NAT-порта",
    "nat.recreate": "Пересоздание NAT-порта",
    "created": "Создано",
    "changed": "Изменено",
    "deleted": "Удалено",
}


class VPNActivityPage(QWidget):
    rowsReady = Signal(object, object)
    streamWake = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.cloud = CloudVPNSyncClient(settings)
        self._loading = False
        self._stream_stop = threading.Event()
        self._stream_thread: threading.Thread | None = None
        self._build()
        self.rowsReady.connect(self._on_rows_ready)
        self.streamWake.connect(self._on_stream_wake)
        self._event_debounce = QTimer(self)
        self._event_debounce.setSingleShot(True)
        self._event_debounce.setInterval(650)
        self._event_debounce.timeout.connect(lambda: self.refresh(silent=True))

    def _build(self) -> None:
        self.page_scroll, canvas, root = build_page_scaffold(
            self, max_width=1540, min_width=800, margins=22, spacing=12
        )
        self.page_layout = root
        root.addWidget(PageHeader(
            "История VPN",
            "Единая история PostgreSQL: действия сотрудников, события MikroTik и автоматические синхронизации VPNSync. Время отображается в UTC+7.",
        ))

        controls = Card(subtle=True)
        cl = QHBoxLayout(controls)
        cl.setContentsMargins(16, 12, 16, 12)
        cl.setSpacing(9)
        self.login_filter = QLineEdit()
        self.login_filter.setPlaceholderText("Фильтр по логину VPN")
        self.login_filter.returnPressed.connect(self.refresh)
        self.source_filter = QComboBox()
        self.source_filter.addItem("Все источники", "")
        self.source_filter.addItem("Helper", "desktop")
        self.source_filter.addItem("MikroTik /listen", "RouterOS listen")
        self.source_filter.addItem("MikroTik → БД", "RouterOS event")
        self.source_filter.addItem("VPNSync startup", "VPNSync startup")
        self.source_filter.currentIndexChanged.connect(lambda _index: self.refresh())
        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.setProperty("role", "primary")
        self.refresh_button.clicked.connect(self.refresh)
        cl.addWidget(self.login_filter, 2)
        cl.addWidget(self.source_filter, 1)
        cl.addWidget(self.refresh_button)
        root.addWidget(controls)

        table_card = Card()
        tl = QVBoxLayout(table_card)
        tl.setContentsMargins(12, 12, 12, 12)
        tl.setSpacing(8)
        self.connection_note = QLabel("Ожидание подключения к облачному серверу…")
        self.connection_note.setObjectName("TinyMuted")
        self.connection_note.setWordWrap(True)
        tl.addWidget(self.connection_note)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "Время (+7)",
            "Источник",
            "Сервер",
            "Логин",
            "Действие",
            "Сотрудник",
            "Результат",
            "Детали",
        ])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        tl.addWidget(self.table)
        root.addWidget(table_card, 1)

        self.task = TaskStatus()
        self.task.hide()
        root.addWidget(self.task)

    @staticmethod
    def _time_text(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return "—"
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=BUSINESS_TZ)
            return parsed.astimezone(BUSINESS_TZ).strftime("%d.%m.%Y %H:%M:%S")
        except Exception:
            return text

    @staticmethod
    def _details_text(row: dict[str, Any]) -> str:
        details = row.get("details") or {}
        if not isinstance(details, dict):
            return str(details or "")
        summary = str(details.get("summary") or "").strip()
        if summary:
            return summary

        action = str(row.get("action") or "")
        if action == "routeros.event":
            path = str(details.get("path") or "").strip()
            item_id = str(
                details.get("routeros_item_id")
                or details.get("item_id")
                or ""
            ).strip()
            fields = [str(item) for item in list(details.get("fields") or []) if str(item or "")]
            field_text = ", ".join(fields[:8])
            if len(fields) > 8:
                field_text += f" +{len(fields) - 8}"
            parts = [value for value in (path, item_id, field_text) if value]
            return " · ".join(parts)

        if action == "sync.snapshot":
            values = []
            for key, label in (("clients", "клиентов"), ("active", "активно"), ("added", "+"), ("changed", "Δ"), ("deleted", "удалено")):
                if key in details:
                    values.append(f"{label} {details.get(key)}")
            return " · ".join(values)

        error_text = str(details.get("error") or "").strip()
        if error_text:
            return error_text
        server_text = str(details.get("server") or "").strip()
        return server_text

    def refresh(self, silent: bool = False) -> None:
        if self._loading:
            return
        config = self.cloud.store.load()
        if not config.username or not config.password:
            self.connection_note.setText("Облачный сервер не настроен. Укажите его в разделе «Настройки».")
            self.table.setRowCount(0)
            return
        self.cloud.set_config(config, save=False)
        self._loading = True
        self.refresh_button.setEnabled(False)
        if not silent:
            self.task.show()
            self.task.busy("Загружаю историю VPN", config.base_url)
        login = self.login_filter.text().strip()
        source = str(self.source_filter.currentData() or "")

        def worker():
            try:
                rows = self.cloud.activity(300, login=login, source=source)
                self.rowsReady.emit(rows, None)
            except Exception as exc:
                self.rowsReady.emit([], exc)

        threading.Thread(target=worker, daemon=True, name="vpnsync-activity-load").start()

    def _on_rows_ready(self, rows, error) -> None:
        self._loading = False
        self.refresh_button.setEnabled(True)
        if error is not None:
            self.connection_note.setText(f"Облачный сервер недоступен: {error}")
            self.task.show()
            self.task.error("Не удалось загрузить историю VPN", str(error))
            return

        records = list(rows or [])
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(records))
        for row_index, row in enumerate(records):
            source_raw = str(row.get("source") or "")
            action_raw = str(row.get("action") or "")
            values = [
                self._time_text(row.get("created_at")),
                _SOURCE_LABELS.get(source_raw, source_raw or "—"),
                str(row.get("server") or "—"),
                str(row.get("login") or "—"),
                _ACTION_LABELS.get(action_raw, action_raw or "—"),
                str(row.get("actor") or "—"),
                "Успешно" if bool(row.get("success", True)) else "Ошибка",
                self._details_text(row),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 6:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row_index, column, item)
        self.table.setSortingEnabled(True)
        self.connection_note.setText(
            f"Облачный сервер подключён · записей показано {len(records)} · push-обновление активно · время UTC+7"
        )
        if self.task.isVisible():
            self.task.done("История VPN обновлена", f"Записей: {len(records)}")

    def _on_stream_wake(self) -> None:
        self._event_debounce.start()

    def _start_stream(self) -> None:
        if self._stream_thread is not None and self._stream_thread.is_alive():
            return
        config = self.cloud.store.load()
        if not config.username or not config.password:
            return
        self.cloud.set_config(config, save=False)
        self._stream_stop.clear()

        def worker():
            for _event in self.cloud.iter_activity_events(self._stream_stop):
                if self._stream_stop.is_set():
                    return
                self.streamWake.emit()

        self._stream_thread = threading.Thread(
            target=worker,
            daemon=True,
            name="vpnsync-activity-stream",
        )
        self._stream_thread.start()

    def onActivated(self) -> None:
        self.refresh(silent=True)
        self._start_stream()

    def onDeactivated(self) -> None:
        self._stream_stop.set()

    def closeEvent(self, event) -> None:
        self._stream_stop.set()
        super().closeEvent(event)
