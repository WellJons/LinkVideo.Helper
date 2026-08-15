from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from linkvideo_vpn_helper.services.errors import classify_exception
from linkvideo_vpn_helper.services.server_registry import ServerRegistry
from linkvideo_vpn_helper.services.vpn_service import InactiveClientRecord, SessionCredentials, VPNService
from linkvideo_vpn_helper.ui.components import Card, EmptyState, SegmentedControl, StatusPill, TaskStatus, build_page_scaffold
from linkvideo_vpn_helper.ui.dialogs import ConfirmDialog


class InactiveClientsPage(QWidget):
    scanReady = Signal(object, object)
    actionReady = Signal(object, object, str)
    progressReady = Signal(int, int, str)
    batchProgressReady = Signal(int, int, str)

    INACTIVE_DAYS = 30

    SORT_OLD = "old"
    SORT_NEW = "new"
    SORT_SERVER = "server"

    def __init__(
        self,
        service: VPNService,
        credentials: SessionCredentials,
        registry: ServerRegistry,
        parent=None,
    ):
        super().__init__(parent)
        self.service = service
        self.credentials = credentials
        self.registry = registry
        self._cancel_event: threading.Event | None = None
        self.current: InactiveClientRecord | None = None
        self._records: list[InactiveClientRecord] = []
        # Эти ключи — настоящий множественный выбор для массовых действий.
        # Обычный клик выбирает одну запись, Ctrl+ЛКМ добавляет/убирает запись из выбора.
        self._selected_keys: set[tuple[str, str]] = set()
        self._sort_mode = self.SORT_OLD
        self._busy_kind = ""
        self.scanReady.connect(self._on_scan)
        self.actionReady.connect(self._on_action)
        self.progressReady.connect(self._on_progress)
        self.batchProgressReady.connect(self._on_batch_progress)
        self._build()

    @staticmethod
    def _key(record: InactiveClientRecord) -> tuple[str, str]:
        return record.server, record.login

    @classmethod
    def sort_records(cls, records: list[InactiveClientRecord], mode: str) -> list[InactiveClientRecord]:
        return VPNService.sort_inactive_records(records, mode)

    def _build(self):
        self.scroll, self.canvas, root = build_page_scaffold(
            self, max_width=1400, min_width=760, margins=22, spacing=12
        )
        self.page_layout = root


        # Шапка страницы — одна спокойная рабочая карточка вместо нескольких полос.
        header = Card()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 16, 18, 16)
        hl.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(4)
        title = QLabel("VPN-клиенты")
        title.setObjectName("SectionTitle")
        hint = QLabel(
            "Состояния VPN-учёток: активные, спящие 30+ дней, автоматический карантин 90+, "
            "кандидаты в архив 365+, ручные отключения и записи с неизвестной последней активностью."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        self.scan_summary = QLabel("Проверка ещё не запускалась")
        self.scan_summary.setObjectName("TinyMuted")
        left.addWidget(title)
        left.addWidget(hint)
        left.addWidget(self.scan_summary)
        self.refresh_btn = QPushButton("Проверить серверы")
        self.refresh_btn.setProperty("role", "primary")
        self.refresh_btn.clicked.connect(self._scan)
        hl.addLayout(left, 1)
        hl.addWidget(self.refresh_btn, 0, Qt.AlignmentFlag.AlignTop)
        root.addWidget(header)

        # TaskStatus показывается только во время работы или при проблеме.
        self.task = TaskStatus()
        self.task.hide()
        root.addWidget(self.task)

        toolbar = Card(subtle=True)
        toolbar.setObjectName("ArchiveClientsToolbar")
        tl = QVBoxLayout(toolbar)
        tl.setContentsMargins(14, 12, 14, 12)
        tl.setSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(10)
        sort_label = QLabel("Сортировка")
        sort_label.setObjectName("Muted")
        self.sort_control = SegmentedControl([
            (self.SORT_OLD, "Старые"),
            (self.SORT_NEW, "Новые"),
            (self.SORT_SERVER, "По серверу"),
        ], self.SORT_OLD)
        self.sort_control.setMinimumWidth(330)
        self.sort_control.changed.connect(self._set_sort)
        self.selection_pill = StatusPill("Выбрано: 0", "neutral")
        self.select_all_btn = QPushButton("Выбрать все")
        self.select_all_btn.clicked.connect(self._select_all)
        self.clear_selection_btn = QPushButton("Очистить выбор")
        self.clear_selection_btn.clicked.connect(self._clear_selected)
        row.addWidget(sort_label)
        row.addWidget(self.sort_control, 1)
        row.addWidget(self.selection_pill)
        row.addWidget(self.select_all_btn)
        row.addWidget(self.clear_selection_btn)
        tl.addLayout(row)
        sort_hint = QLabel("Старые — давно не подключались · Новые — потеряли связь недавно · Без достоверной даты — всегда в конце.")
        sort_hint.setObjectName("TinyMuted")
        sort_hint.setWordWrap(True)
        tl.addWidget(sort_hint)

        self.batch_row = QWidget()
        br = QHBoxLayout(self.batch_row)
        br.setContentsMargins(0, 0, 0, 0)
        br.setSpacing(8)
        self.batch_text = QLabel("Ctrl + клик — выбрать несколько записей")
        self.batch_text.setObjectName("Muted")
        br.addWidget(self.batch_text, 1)
        self.batch_enable = QPushButton("Включить")
        self.batch_enable.clicked.connect(lambda: self._batch_toggle(True))
        self.batch_disable = QPushButton("Отключить")
        self.batch_disable.clicked.connect(lambda: self._batch_toggle(False))
        self.batch_delete = QPushButton("Удалить")
        self.batch_delete.setProperty("role", "danger")
        self.batch_delete.clicked.connect(self._batch_delete)
        br.addWidget(self.batch_enable)
        br.addWidget(self.batch_disable)
        br.addWidget(self.batch_delete)
        tl.addWidget(self.batch_row)
        root.addWidget(toolbar)

        self.body = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.body.setSpacing(12)

        left_card = Card()
        ll = QVBoxLayout(left_card)
        ll.setContentsMargins(16, 15, 16, 16)
        ll.setSpacing(9)
        lt = QHBoxLayout()
        label_box = QVBoxLayout()
        label_box.setSpacing(2)
        label = QLabel("Учётные записи")
        label.setObjectName("SectionTitle")
        select_hint = QLabel("ЛКМ — открыть · Ctrl + ЛКМ — множественный выбор")
        select_hint.setObjectName("TinyMuted")
        label_box.addWidget(label)
        label_box.addWidget(select_hint)
        self.count_pill = StatusPill("0", "neutral")
        lt.addLayout(label_box)
        lt.addStretch(1)
        lt.addWidget(self.count_pill, 0, Qt.AlignmentFlag.AlignTop)
        ll.addLayout(lt)

        self.list = QListWidget()
        self.list.setObjectName("ArchiveClientsList")
        self.list.setSpacing(8)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setMinimumHeight(420)
        self.list.setMaximumHeight(680)
        ll.addWidget(self.list)

        right_card = Card()
        rl = QVBoxLayout(right_card)
        rl.setContentsMargins(16, 15, 16, 16)
        rl.setSpacing(10)
        self.detail_host = QWidget()
        self.detail = QVBoxLayout(self.detail_host)
        self.detail.setContentsMargins(0, 0, 0, 0)
        self.detail.setSpacing(10)
        self.detail.addWidget(EmptyState("Учётка не выбрана", "Выберите запись в списке слева.", "⌛"), 1)
        rl.addWidget(self.detail_host)
        rl.addStretch(1)

        self.body.addWidget(left_card, 6)
        self.body.addWidget(right_card, 4)
        root.addLayout(self.body)
        root.addStretch(1)
        self._sync_batch_controls()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "body"):
            return
        compact = self.width() < 1120
        self.body.setDirection(QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight)
        self.list.setMaximumHeight(460 if compact else 680)

    def refresh_servers(self):
        pass

    def cancel_current_action(self) -> bool:
        event = self._cancel_event
        if event is None or event.is_set():
            return False
        event.set()
        self._cancel_event = None
        self._set_busy(False)
        self.task.show()
        self.task.warning("Операция остановлена", "Действие отменено клавишей Esc.")
        return True

    def _set_busy(self, busy: bool):
        self.refresh_btn.setEnabled(not busy)
        self.sort_control.setEnabled(not busy)
        self.select_all_btn.setEnabled(not busy)
        self.clear_selection_btn.setEnabled(not busy and bool(self._selected_keys))
        self.batch_enable.setEnabled(not busy and bool(self._selected_keys))
        self.batch_disable.setEnabled(not busy and bool(self._selected_keys))
        self.batch_delete.setEnabled(not busy and bool(self._selected_keys))

    def _scan(self):
        servers = self.registry.hosts()
        if not servers:
            self.task.show()
            self.task.warning("Нет активных VPN-серверов", "Включите серверы в настройках.")
            return
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self._busy_kind = "scan"
        self._set_busy(True)
        self.task.show()
        self.task.busy("Проверяю VPN-серверы", f"Проверено 0 из {len(servers)}", 0)
        cutoff = datetime.now() - timedelta(days=self.INACTIVE_DAYS)

        def worker():
            records: list[InactiveClientRecord] = []
            errors = []
            workers = min(8, max(1, len(servers)))
            pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="inactive-vpn")
            futures = {
                pool.submit(self.service.list_lifecycle_clients, server, self.credentials, self.INACTIVE_DAYS, True): server
                for server in servers
            }
            checked = 0
            try:
                for future in as_completed(futures):
                    if cancel_event.is_set():
                        for item in futures:
                            item.cancel()
                        pool.shutdown(wait=False, cancel_futures=True)
                        return
                    server = futures[future]
                    checked += 1
                    try:
                        records.extend(future.result())
                    except Exception as exc:
                        errors.append((server, classify_exception(exc)))
                    self.progressReady.emit(checked, len(servers), server)
            finally:
                if not cancel_event.is_set():
                    pool.shutdown(wait=True)
            if not cancel_event.is_set() and self._cancel_event is cancel_event:
                self.scanReady.emit(records, errors)

        threading.Thread(target=worker, daemon=True).start()

    def _on_progress(self, checked: int, total: int, server: str):
        if self._cancel_event is None or self._busy_kind != "scan":
            return
        pct = int(checked * 100 / total) if total else 0
        self.task.busy("Проверяю VPN-серверы", f"Проверено {checked} из {total} · {server}", pct)

    def _on_batch_progress(self, done: int, total: int, label: str):
        if self._cancel_event is None or self._busy_kind != "batch":
            return
        pct = int(done * 100 / total) if total else 0
        self.task.busy("Выполняю действие", f"{done} из {total} · {label}", pct)

    def _on_scan(self, records, errors):
        self._cancel_event = None
        self._busy_kind = ""
        self._records = list(records or [])
        self._selected_keys.clear()
        self.current = None
        self._render_records()
        self.count_pill.set_status(str(len(self._records)), "warning" if self._records else "neutral")
        if errors:
            self.task.show()
            self.task.warning(
                "Проверка завершена не полностью",
                f"Найдено учёток: {len(self._records)}. Не удалось проверить серверов: {len(errors)}.",
            )
            self.scan_summary.setText(f"Найдено: {len(self._records)} · серверов с ошибкой: {len(errors)}")
        else:
            self.task.hide()
            self.scan_summary.setText(f"Найдено учётных записей: {len(self._records)}")
        self._set_busy(False)
        if self.list.count():
            self._activate_item(self.list.item(0), ctrl=False)

    def _set_sort(self, mode: str):
        self._sort_mode = mode
        self._render_records(keep_current=True)

    def _render_records(self, keep_current: bool = False):
        current_key = self._key(self.current) if keep_current and self.current else None
        self.list.clear()
        self.current = None
        for record in self.sort_records(self._records, self._sort_mode):
            self._add_record(record)
        self._sync_batch_controls()
        if current_key:
            for row in range(self.list.count()):
                item = self.list.item(row)
                record = item.data(Qt.ItemDataRole.UserRole)
                if record and self._key(record) == current_key:
                    self.list.setCurrentRow(row)
                    self.current = record
                    self._sync_card_states()
                    self._render_detail()
                    break
        elif not self.list.count():
            self._render_empty()
        else:
            self._sync_card_states()

    def _add_record(self, record: InactiveClientRecord):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, record)

        card = Card(subtle=True)
        card.setObjectName("ArchiveClientCard")
        card.setProperty("focused", "false")
        card.setProperty("bulk", "true" if self._key(record) in self._selected_keys else "false")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 11, 14, 11)
        lay.setSpacing(7)

        top = QHBoxLayout()
        top.setSpacing(8)
        login = QLabel(record.login)
        login.setObjectName("Value")
        login.setStyleSheet("font-weight: 800;")
        state_labels = {
            "A": ("Активна", "success"),
            "S": ("Спящая", "warning"),
            "Q": ("Карантин", "warning"),
            "R": ("Кандидат в архив", "danger"),
            "M": ("Отключена вручную", "neutral"),
            "U": ("Активность неизвестна", "neutral"),
        }
        state_text, state_kind = state_labels.get(record.lifecycle_state, (record.lifecycle_state or "—", "neutral"))
        if record.is_online:
            state_text, state_kind = "В сети", "success"
        state = StatusPill(state_text, state_kind)
        top.addWidget(login)
        top.addStretch(1)
        top.addWidget(state)
        lay.addLayout(top)

        country = self.registry.get(record.server).country
        server = QLabel(f"{record.server}    {country}")
        server.setObjectName("TinyMuted")
        lay.addWidget(server)

        age_days = max(0, (datetime.now() - record.last_logged_out_dt).days) if record.last_logged_out_dt else None
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        last = QLabel(f"Последняя активность: {self._fmt(record.last_logged_out_dt)}")
        last.setObjectName("Muted")
        age = StatusPill(f"{age_days} дн." if age_days is not None else "неизвестно", "warning" if age_days is not None else "neutral")
        bottom.addWidget(last, 1)
        bottom.addWidget(age)
        lay.addLayout(bottom)

        # Все дочерние подписи пропускают мышь в карточку — Ctrl+ЛКМ работает по всей площади.
        for widget in (login, state, server, last, age):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        def click(event, target=item):
            if event.button() != Qt.MouseButton.LeftButton:
                return
            ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self._activate_item(target, ctrl=ctrl)

        card.mousePressEvent = click
        item.setSizeHint(card.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, card)

    def _activate_item(self, item: QListWidgetItem, ctrl: bool):
        if item is None:
            return
        record = item.data(Qt.ItemDataRole.UserRole)
        if not record:
            return
        key = self._key(record)
        if ctrl:
            if key in self._selected_keys:
                self._selected_keys.discard(key)
            else:
                self._selected_keys.add(key)
        else:
            self._selected_keys = {key}
        self.list.setCurrentItem(item)
        self.current = record
        self._sync_card_states()
        self._sync_batch_controls()
        self._render_detail()

    def _sync_card_states(self):
        current_key = self._key(self.current) if self.current else None
        for row in range(self.list.count()):
            item = self.list.item(row)
            record = item.data(Qt.ItemDataRole.UserRole)
            card = self.list.itemWidget(item)
            if not record or not card:
                continue
            key = self._key(record)
            card.setProperty("focused", "true" if key == current_key else "false")
            card.setProperty("bulk", "true" if key in self._selected_keys else "false")
            card.style().unpolish(card)
            card.style().polish(card)

    def _select_all(self):
        self._selected_keys = {self._key(record) for record in self._records}
        self._sync_card_states()
        self._sync_batch_controls()

    def _clear_selected(self):
        self._selected_keys.clear()
        self._sync_card_states()
        self._sync_batch_controls()

    def _sync_batch_controls(self):
        count = len(self._selected_keys)
        self.selection_pill.set_status(f"Выбрано: {count}", "warning" if count else "neutral")
        self.batch_text.setText(
            f"Выбрано записей: {count} · Ctrl + клик — изменить выбор"
            if count
            else "Ctrl + клик — выбрать несколько записей"
        )
        busy = self._cancel_event is not None
        for btn in (self.batch_enable, self.batch_disable, self.batch_delete):
            btn.setEnabled(bool(count) and not busy)
        self.clear_selection_btn.setEnabled(bool(count) and not busy)

    def _clear_detail(self):
        while self.detail.count():
            item = self.detail.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _render_empty(self):
        self._clear_detail()
        self.detail.addWidget(EmptyState("Учётка не выбрана", "Выберите запись в списке слева.", "⌛"), 1)

    @staticmethod
    def _info_row(label_text: str, value_text: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        label = QLabel(label_text)
        label.setObjectName("Muted")
        value = QLabel(value_text)
        value.setObjectName("Value")
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(value, 1)
        return row

    def _render_detail(self):
        record = self.current
        if not record:
            self._render_empty()
            return
        self._clear_detail()

        title_row = QHBoxLayout()
        title = QLabel(record.login)
        title.setObjectName("SectionTitle")
        detail_labels = {"A":"Активная", "S":"Спящая", "Q":"Карантин", "R":"Кандидат в архив", "M":"Отключена вручную", "U":"Активность неизвестна"}
        current_state = StatusPill(detail_labels.get(record.lifecycle_state, record.lifecycle_state or "—"), "success" if record.lifecycle_state == "A" else ("warning" if record.lifecycle_state in {"S", "Q"} else "neutral"))
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(current_state)
        self.detail.addLayout(title_row)

        info = Card(subtle=True)
        il = QVBoxLayout(info)
        il.setContentsMargins(14, 13, 14, 13)
        il.setSpacing(10)
        country = self.registry.get(record.server).country
        age_days = max(0, (datetime.now() - record.last_logged_out_dt).days) if record.last_logged_out_dt else None
        il.addWidget(self._info_row("VPN-сервер", record.server))
        il.addWidget(self._info_row("Страна", country))
        il.addWidget(self._info_row("Состояние", detail_labels.get(record.lifecycle_state, record.lifecycle_state or "—")))
        il.addWidget(self._info_row("Источник", "LV-автоматика" if record.lifecycle_source == "lv" else "RouterOS"))
        il.addWidget(self._info_row("Последняя активность", self._fmt(record.last_logged_out_dt)))
        il.addWidget(self._info_row("Без подключения", f"{age_days} дней" if age_days is not None else "неизвестно"))
        self.detail.addWidget(info)

        actions = Card()
        al = QVBoxLayout(actions)
        al.setContentsMargins(14, 13, 14, 13)
        al.setSpacing(9)
        at = QLabel("Действия")
        at.setObjectName("SectionTitle")
        al.addWidget(at)
        toggle = QPushButton("Отключить учётку" if record.is_enabled else "Включить учётку")
        toggle.clicked.connect(self._toggle)
        delete = QPushButton("Удалить клиента")
        delete.setProperty("role", "danger")
        delete.clicked.connect(self._delete)
        al.addWidget(toggle)
        al.addWidget(delete)
        self.detail.addWidget(actions)
        self.detail.addStretch(1)

    def _toggle(self):
        record = self.current
        if not record:
            return
        target = not record.is_enabled
        title = "Включить учётку" if target else "Отключить учётку"
        dialog = ConfirmDialog(title, f"{title} {record.login} на {record.server}?", title, False, self)
        if not dialog.exec():
            return
        self._run_action("toggle", lambda: self.service.set_secret_enabled(record.server, self.credentials, record.login, target))

    def _delete(self):
        record = self.current
        if not record:
            return
        dialog = ConfirmDialog(
            "Удалить клиента",
            f"Будут удалены PPP Secret, профиль и NAT-правила {record.login}. Действие нельзя отменить.",
            "Удалить клиента",
            True,
            self,
        )
        if not dialog.exec():
            return
        self._run_action("delete", lambda: self.service.delete_client(record.server, self.credentials, record.login))

    def _selected_records(self) -> list[InactiveClientRecord]:
        wanted = set(self._selected_keys)
        return [record for record in self._records if self._key(record) in wanted]

    def _batch_toggle(self, enabled: bool):
        records = self._selected_records()
        if not records:
            return
        verb = "Включить" if enabled else "Отключить"
        dialog = ConfirmDialog(
            f"{verb} выбранные учётки",
            f"{verb} {len(records)} выбранных учётных записей?",
            verb,
            False,
            self,
        )
        if not dialog.exec():
            return
        self._run_batch("enable" if enabled else "disable", records, enabled)

    def _batch_delete(self):
        records = self._selected_records()
        if not records:
            return
        dialog = ConfirmDialog(
            "Удалить выбранные учётки",
            f"Будут полностью удалены {len(records)} клиентов: PPP Secret, профиль и NAT-правила. Действие нельзя отменить.",
            "Удалить выбранные",
            True,
            self,
        )
        if not dialog.exec():
            return
        self._run_batch("delete_many", records, None)

    def _run_batch(self, name: str, records: list[InactiveClientRecord], enabled: bool | None):
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self._busy_kind = "batch"
        self._set_busy(True)
        self.task.show()
        self.task.busy("Выполняю действие", f"0 из {len(records)}", 0)

        def one(record: InactiveClientRecord):
            if cancel_event.is_set():
                return record, "cancelled"
            if name == "delete_many":
                self.service.delete_client(record.server, self.credentials, record.login)
            else:
                self.service.set_secret_enabled(record.server, self.credentials, record.login, bool(enabled))
            return record, None

        def worker():
            successes: list[InactiveClientRecord] = []
            failures: list[tuple[InactiveClientRecord, object]] = []
            total = len(records)
            workers = min(4, max(1, total))
            pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="inactive-batch")
            futures = {pool.submit(one, record): record for record in records}
            done = 0
            try:
                for future in as_completed(futures):
                    record = futures[future]
                    if cancel_event.is_set():
                        for item in futures:
                            item.cancel()
                        pool.shutdown(wait=False, cancel_futures=True)
                        return
                    done += 1
                    try:
                        _record, failure = future.result()
                        if failure:
                            failures.append((record, failure))
                        else:
                            successes.append(record)
                    except Exception as exc:
                        failures.append((record, classify_exception(exc)))
                    self.batchProgressReady.emit(done, total, record.login)
            finally:
                if not cancel_event.is_set():
                    pool.shutdown(wait=True)
            if not cancel_event.is_set() and self._cancel_event is cancel_event:
                self.actionReady.emit(
                    {"successes": successes, "failures": failures, "enabled": enabled},
                    None,
                    name,
                )

        threading.Thread(target=worker, daemon=True).start()

    def _run_action(self, name: str, fn):
        record = self.current
        if not record:
            return
        self._set_busy(True)
        self.task.show()
        self.task.busy("Выполняю действие", f"{record.login} · {record.server}")

        def worker():
            try:
                self.actionReady.emit(fn(), None, name)
            except Exception as exc:
                self.actionReady.emit(None, classify_exception(exc), name)

        threading.Thread(target=worker, daemon=True).start()

    def _on_action(self, result, error, name: str):
        if name in ("delete_many", "enable", "disable"):
            self._cancel_event = None
            self._busy_kind = ""
            self._set_busy(False)
            if error:
                self.task.error("Операция не выполнена", getattr(error, "message", None) or str(error))
                return
            payload = result or {}
            successes: list[InactiveClientRecord] = list(payload.get("successes") or [])
            failures = list(payload.get("failures") or [])
            success_keys = {self._key(x) for x in successes}
            if name == "delete_many":
                self._records = [x for x in self._records if self._key(x) not in success_keys]
                self._selected_keys.difference_update(success_keys)
                if self.current and self._key(self.current) in success_keys:
                    self.current = None
            else:
                target_enabled = bool(payload.get("enabled"))
                for record in self._records:
                    if self._key(record) in success_keys:
                        record.is_enabled = target_enabled
                        record.lifecycle_state = "A" if target_enabled else "M"
            self._render_records(keep_current=True)
            self.count_pill.set_status(str(len(self._records)), "warning" if self._records else "neutral")
            if failures:
                self.task.warning(
                    "Операция выполнена частично",
                    f"Успешно: {len(successes)}. С ошибкой: {len(failures)}. Неуспешные записи оставлены в списке.",
                )
            else:
                action = "Удалено" if name == "delete_many" else ("Включено" if payload.get("enabled") else "Отключено")
                self.task.done("Готово", f"{action} учётных записей: {len(successes)}")
            return

        self._set_busy(False)
        if error:
            self.task.error("Операция не выполнена", getattr(error, "message", None) or str(error))
            return
        record = self.current
        if not record:
            return
        if name == "delete":
            key = self._key(record)
            self._records = [x for x in self._records if self._key(x) != key]
            self._selected_keys.discard(key)
            self._remove_current_item()
            self.task.done("Клиент удалён", f"{record.login} удалён с {record.server}")
        elif name == "toggle":
            record.is_enabled = not record.is_enabled
            record.lifecycle_state = "A" if record.is_enabled else "M"
            self._render_records(keep_current=True)
            self.task.done("Состояние изменено", "Учётная запись обновлена.")

    def _remove_current_item(self):
        current_key = self._key(self.current) if self.current else None
        sorted_records = self.sort_records(self._records, self._sort_mode)
        self._render_records()
        self.count_pill.set_status(str(len(self._records)), "warning" if self._records else "neutral")
        if sorted_records:
            self._activate_item(self.list.item(0), ctrl=False)
        else:
            self.current = None
            self._render_empty()
        if current_key:
            self._selected_keys.discard(current_key)
        self._sync_batch_controls()

    @staticmethod
    def _fmt(value: datetime | None) -> str:
        return value.strftime("%d/%m/%y %H:%M:%S") if value else "—"
