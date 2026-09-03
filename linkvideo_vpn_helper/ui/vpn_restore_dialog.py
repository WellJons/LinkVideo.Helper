from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from linkvideo_vpn_helper.services.vpn_restore_service import (
    DeletedVPNClient,
    VPNRestoreService,
)
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials
from linkvideo_vpn_helper.ui.components import TaskStatus
from linkvideo_vpn_helper.ui.dialogs import ConfirmDialog


class VPNRestoreDialog(QDialog):
    loaded = Signal(object, object)
    restored = Signal(object, object)

    def __init__(
        self,
        service: VPNRestoreService,
        credentials: SessionCredentials,
        servers: list[str],
        *,
        on_restored: Callable[[str, str], None] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.service = service
        self.credentials = credentials
        self.servers = list(servers)
        self.on_restored = on_restored
        self._records: list[DeletedVPNClient] = []
        self._busy = False

        self.setWindowTitle("Восстановление VPN-клиента")
        self.resize(980, 620)
        self.setMinimumSize(760, 500)

        self.loaded.connect(self._on_loaded)
        self.restored.connect(self._on_restored)
        self._build()
        self._load()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(12)

        title = QLabel("Восстановление удалённого VPN-клиента")
        title.setObjectName("SectionTitle")
        hint = QLabel(
            "Источник — сохранённая строка Google Sheets. Helper проверит логин, Remote Address, "
            "порты и профиль на конфликты и только после этого восстановит PPP Secret, профиль "
            "и NAT. Старый пароль сохраняется; новый пароль автоматически не генерируется."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Фильтр по логину или VPN-серверу")
        self.search.textChanged.connect(self._render)
        self.refresh_btn = QPushButton("Обновить список")
        self.refresh_btn.clicked.connect(self._load)
        controls.addWidget(self.search, 1)
        controls.addWidget(self.refresh_btn)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["VPN-сервер", "Логин", "Удалена", "Remote Address", "NAT / Порты", "Пароль"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._sync_selection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        self.task = TaskStatus()
        self.task.hide()

        buttons = QHBoxLayout()
        self.summary = QLabel("Загружаю удалённые записи…")
        self.summary.setObjectName("TinyMuted")
        buttons.addWidget(self.summary, 1)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.reject)
        self.restore_btn = QPushButton("Восстановить")
        self.restore_btn.setProperty("role", "primary")
        self.restore_btn.setEnabled(False)
        self.restore_btn.clicked.connect(self._restore_selected)
        buttons.addWidget(close_btn)
        buttons.addWidget(self.restore_btn)

        root.addWidget(title)
        root.addWidget(hint)
        root.addLayout(controls)
        root.addWidget(self.table, 1)
        root.addWidget(self.task)
        root.addLayout(buttons)

    def _set_busy(self, busy: bool):
        self._busy = bool(busy)
        self.refresh_btn.setEnabled(not busy)
        self.search.setEnabled(not busy)
        self.table.setEnabled(not busy)
        self.restore_btn.setEnabled(False if busy else self._selected() is not None)

    def _load(self):
        if self._busy:
            return
        self._set_busy(True)
        self.task.show()
        self.task.busy("Читаю резервную базу", "Получаю удалённые записи из Google Sheets")

        def worker():
            try:
                self.loaded.emit(self.service.list_deleted(self.servers), None)
            except Exception as exc:
                self.loaded.emit(None, exc)

        threading.Thread(target=worker, daemon=True, name="vpn-restore-list").start()

    def _on_loaded(self, records, error):
        self._set_busy(False)
        if error:
            self.task.show()
            self.task.error("Не удалось прочитать резервную базу", str(error))
            self.summary.setText("Ошибка чтения Google Sheets")
            return
        self._records = list(records or [])
        self.task.hide()
        self._render()

    def _filtered(self) -> list[DeletedVPNClient]:
        query = self.search.text().strip().lower()
        if not query:
            return list(self._records)
        return [
            item
            for item in self._records
            if query in item.login.lower() or query in item.server.lower()
        ]

    def _render(self):
        records = self._filtered()
        self.table.setRowCount(0)
        for record in records:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (
                record.server,
                record.login,
                record.deleted_at or "—",
                record.remote_address or "—",
                record.ports or "—",
                "Сохранён" if record.password_saved else "НЕТ",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, record)
                if column == 5 and not record.password_saved:
                    item.setToolTip("Без сохранённого пароля автоматическое восстановление запрещено")
                self.table.setItem(row, column, item)
        missing = sum(1 for item in records if not item.password_saved)
        suffix = f" · без пароля: {missing}" if missing else ""
        self.summary.setText(f"Удалённых записей: {len(records)}{suffix}")
        self._sync_selection()

    def _selected(self) -> DeletedVPNClient | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return value if isinstance(value, DeletedVPNClient) else None

    def _sync_selection(self):
        selected = self._selected()
        self.restore_btn.setEnabled(
            bool(selected and selected.password_saved and not self._busy)
        )

    def _restore_selected(self):
        record = self._selected()
        if record is None or self._busy:
            return
        if not record.password_saved:
            self.task.show()
            self.task.error(
                "Восстановление недоступно",
                "Для этой записи пароль не был сохранён. Helper не будет генерировать другой пароль автоматически.",
            )
            return

        details = (
            f"{record.server}\n"
            f"Логин: {record.login}\n"
            f"Remote Address: {record.remote_address or '—'}\n"
            f"Порты: {record.ports or '—'}\n\n"
            "Перед записью Helper проверит конфликты. Существующие объекты RouterOS "
            "не перезаписываются."
        )
        dialog = ConfirmDialog(
            "Восстановить VPN-клиента?",
            details,
            confirm_text="Восстановить",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._set_busy(True)
        self.task.show()
        self.task.busy("Восстанавливаю клиента", f"{record.login} · {record.server}")

        def worker():
            try:
                self.restored.emit(
                    self.service.restore(record.server, self.credentials, record.login),
                    None,
                )
            except Exception as exc:
                self.restored.emit(None, exc)

        threading.Thread(
            target=worker,
            daemon=True,
            name=f"vpn-restore-{record.login}",
        ).start()

    def _on_restored(self, result, error):
        self._set_busy(False)
        if error:
            self.task.show()
            self.task.error("Восстановление не выполнено", str(error))
            return

        self._records = [
            item
            for item in self._records
            if not (item.server == result.server and item.login == result.login)
        ]
        self._render()
        self.task.show()
        self.task.done(
            "VPN-клиент восстановлен",
            f"{result.login} · {result.server} · NAT-правил: {result.nat_created}",
        )
        if callable(self.on_restored):
            self.on_restored(result.server, result.login)
