from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDateTime, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QBoxLayout, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.archive_service import (
    ArchiveDiscovery, ArchiveDownloadResult, ArchiveService,
)
from linkvideo_vpn_helper.ui.components import (
    Card, DateTimePickerButton, MetricCard, PageHeader, SegmentedControl, StatusPill, TaskStatus, button_feedback, build_page_scaffold,
)
from linkvideo_vpn_helper.ui.dialogs import B2OLoginDialog


class ArchiveDownloadPage(QWidget):
    discoveryReady = Signal(object, object)
    authReady = Signal(object)
    downloadUpdate = Signal(object)

    def __init__(self, service: ArchiveService, settings, parent=None):
        super().__init__(parent)
        self.service = service
        self.settings = settings
        self.discovery: ArchiveDiscovery | None = None
        self._last_output: Path | None = None
        self._retry_action = None
        self._cancel_event = None
        self.discoveryReady.connect(self._on_discovery)
        self.authReady.connect(self._on_auth)
        self.downloadUpdate.connect(self._on_download_update)
        self._build()

    def _build(self):
        self.page_scroll, self.page_canvas, outer = build_page_scaffold(
            self, max_width=1360, min_width=760, margins=22, spacing=12
        )
        self.page_layout = outer


        header = Card()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 12, 18, 12)
        title = QLabel("Скачивание архива")
        title.setObjectName("SectionTitle")
        hl.addWidget(title)
        hl.addStretch(1)
        outer.addWidget(header)

        self.work = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.work.setSpacing(12)

        params_card = Card(kind="hero")
        params = QVBoxLayout(params_card)
        params.setContentsMargins(18, 16, 18, 16)
        params.setSpacing(12)
        period_title = QLabel("Период")
        period_title.setObjectName("SectionTitle")
        params.addWidget(period_title)

        self.period_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.period_row.setSpacing(8)
        self.period_row.addWidget(QLabel("с"))
        now_dt = QDateTime.currentDateTime()
        now_dt = now_dt.addSecs(-now_dt.time().second())
        self.start = DateTimePickerButton(now_dt.addSecs(-300))
        self.period_row.addWidget(self.start, 1)
        self.period_row.addWidget(QLabel("по"))
        self.end = DateTimePickerButton(now_dt)
        self.period_row.addWidget(self.end, 1)
        params.addLayout(self.period_row)

        params_title = QLabel("Параметры")
        params_title.setObjectName("SectionTitle")
        params.addWidget(params_title)
        camera_row = QHBoxLayout()
        camera_row.setSpacing(10)
        camera_label = QLabel("ID камеры")
        camera_label.setMinimumWidth(110)
        self.camera = QLineEdit()
        self.camera.setPlaceholderText("Например: 207728 или linkvideo_207728")
        self.camera.textChanged.connect(self._sync_operator_from_camera)
        self.camera.returnPressed.connect(self._find)
        camera_row.addWidget(camera_label)
        camera_row.addWidget(self.camera, 1)
        params.addLayout(camera_row)

        self.auth_row_widget = QWidget()
        auth = QHBoxLayout(self.auth_row_widget)
        auth.setContentsMargins(0, 0, 0, 0)
        self.auth_pill = StatusPill("B2O не подключён", "warning")
        self.auth_user = QLabel("")
        self.auth_user.setObjectName("Muted")
        self.btn_auth = QPushButton("Войти в B2O")
        self.btn_auth.clicked.connect(self._authorize)
        auth.addWidget(self.auth_pill)
        auth.addWidget(self.auth_user)
        auth.addStretch(1)
        auth.addWidget(self.btn_auth)
        params.addWidget(self.auth_row_widget)
        # Авторизация B2O должна быть видна до выбора периода. Перемещаем
        # строку в самый верх карточки параметров, над «Период».
        params.removeWidget(self.auth_row_widget)
        params.insertWidget(0, self.auth_row_widget)
        self._sync_auth()

        operator_row = QHBoxLayout()
        operator_row.setSpacing(10)
        operator_label = QLabel("Страна")
        operator_label.setMinimumWidth(110)
        saved_operator = str(self.settings.value("archive/operator_id", "241", str) or "241")
        if saved_operator not in {"241", "1721", "1741"}:
            saved_operator = "241"
        self.operator = SegmentedControl([
            ("241", "Россия"),
            ("1721", "Казахстан"),
            ("1741", "Беларусь"),
        ], saved_operator)
        self.operator.changed.connect(self._operator_changed)
        operator_row.addWidget(operator_label)
        operator_row.addWidget(self.operator, 1)
        params.insertLayout(1, operator_row)
        self._operator_changed(saved_operator)

        self.btn_find = QPushButton("Найти архив")
        self.btn_find.setProperty("role", "primary")
        self.btn_find.setMinimumHeight(44)
        self.btn_find.clicked.connect(self._find)
        params.addWidget(self.btn_find)

        save_card = Card()
        save = QVBoxLayout(save_card)
        save.setContentsMargins(18, 16, 18, 16)
        save.setSpacing(10)
        save_title = QLabel("Сохранение")
        save_title.setObjectName("SectionTitle")
        save.addWidget(save_title)

        default = str(Path.home() / "Videos" / "LinkVideo_Archive")
        self.folder = QLineEdit(str(self.settings.value("archive/folder_v2", default, str) or default))
        self.folder.setReadOnly(True)
        self.folder.setMinimumHeight(42)
        save.addWidget(self.folder)

        save_hint = QLabel("Имя MP4 формируется автоматически по камере и выбранному периоду.")
        save_hint.setObjectName("TinyMuted")
        save_hint.setWordWrap(True)
        save.addWidget(save_hint)

        save_buttons = QHBoxLayout()
        save_buttons.setSpacing(8)
        self.btn_folder = QPushButton("Папка")
        self.btn_folder.clicked.connect(self._choose_folder)
        self.btn_copy_path = QPushButton("Скопировать путь")
        self.btn_copy_path.clicked.connect(self._copy_path)
        save_buttons.addWidget(self.btn_folder, 1)
        save_buttons.addWidget(self.btn_copy_path, 1)
        save.addLayout(save_buttons)

        self.work.addWidget(params_card, 3)
        self.work.addWidget(save_card, 2)
        outer.addLayout(self.work)

        self.task = TaskStatus()
        self.task.hide()
        self.task.retryRequested.connect(self._retry)
        outer.addWidget(self.task)

        # Нижний блок занимает оставшееся место так же, как лог скачивания в 1.1.2,
        # но показывает только понятные этапы и реальный прогресс.
        self.summary = Card()
        sl = QVBoxLayout(self.summary)
        sl.setContentsMargins(18, 16, 18, 16)
        sl.setSpacing(10)
        top = QHBoxLayout()
        st = QLabel("Скачивание")
        st.setObjectName("SectionTitle")
        self.coverage = StatusPill("Ожидание", "neutral")
        top.addWidget(st)
        top.addStretch(1)
        top.addWidget(self.coverage)
        sl.addLayout(top)

        self.metrics_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.metrics_row.setSpacing(8)
        self.m_requested = MetricCard("Запрошено", "—")
        self.m_found = MetricCard("Найдено", "—")
        self.m_servers = MetricCard("Источники", "—")
        self.m_parts = MetricCard("Участки", "—")
        for m in (self.m_requested, self.m_found, self.m_servers, self.m_parts):
            self.metrics_row.addWidget(m, 1)
        sl.addLayout(self.metrics_row)

        self.summary_text = QLabel("Введите ID камеры и период, затем нажмите «Найти архив».")
        self.summary_text.setObjectName("Muted")
        self.summary_text.setWordWrap(True)
        sl.addWidget(self.summary_text)

        self.gaps_card = Card(kind="danger")
        gl = QVBoxLayout(self.gaps_card)
        gl.setContentsMargins(14, 12, 14, 12)
        gl.setSpacing(5)
        gap_title = QLabel("В исходной записи есть пропуски")
        gap_title.setObjectName("WarningText")
        self.gaps_text = QLabel("")
        self.gaps_text.setObjectName("Muted")
        self.gaps_text.setWordWrap(True)
        gl.addWidget(gap_title)
        gl.addWidget(self.gaps_text)
        self.gaps_card.hide()
        sl.addWidget(self.gaps_card)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.btn_download = QPushButton("Скачать архив")
        self.btn_download.setProperty("role", "primary")
        self.btn_download.setEnabled(False)
        self.btn_download.clicked.connect(self._download)
        self.btn_open = QPushButton("Открыть папку")
        self.btn_open.clicked.connect(self._open_folder)
        bottom.addWidget(self.btn_download)
        bottom.addWidget(self.btn_open)
        bottom.addStretch(1)
        sl.addLayout(bottom)
        outer.addWidget(self.summary)
        outer.addStretch(1)
        self.discovery = None

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "operator"):
            saved = str(self.settings.value("archive/operator_id", "241", str) or "241")
            if saved not in {"241", "1721", "1741"}:
                saved = "241"
            self.operator.setCurrent(saved)
            if hasattr(self, "camera"):
                self._sync_operator_from_camera(self.camera.text())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "work"):
            return
        compact = self.width() < 1050
        very_compact = self.width() < 900
        self.work.setDirection(QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight)
        self.period_row.setDirection(QBoxLayout.Direction.TopToBottom if very_compact else QBoxLayout.Direction.LeftToRight)
        self.metrics_row.setDirection(QBoxLayout.Direction.TopToBottom if very_compact else QBoxLayout.Direction.LeftToRight)

    @staticmethod
    def _field_title(title: str, hint: str) -> QWidget:
        widget = QWidget()
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        t = QLabel(title)
        t.setObjectName("CardTitle")
        h = QLabel(hint)
        h.setObjectName("TinyMuted")
        h.setWordWrap(True)
        lay.addWidget(t)
        lay.addWidget(h)
        return widget

    def cancel_current_action(self) -> bool:
        event = self._cancel_event
        if event is None or event.is_set():
            return False
        event.set()
        self._cancel_event = None
        self.btn_find.setEnabled(True)
        self.btn_download.setEnabled(bool(self.discovery and self.discovery.has_downloadable_archive))
        self.task.show()
        self.task.warning("Операция остановлена", "Поиск или скачивание архива отменено клавишей Esc.")
        return True

    def _sync_auth(self):
        token = self.service.b2o.token()
        login = self.service.b2o.login_name()
        if token:
            self.auth_pill.set_status("B2O подключён", "success")
            self.auth_user.setText(login)
            self.btn_auth.setText("Сменить вход")
        else:
            self.auth_pill.set_status("Нужен вход B2O", "warning")
            self.auth_user.setText("")
            self.btn_auth.setText("Войти в B2O")

    def _authorize(self):
        dialog = B2OLoginDialog(self.service.b2o.login_name(), self)
        if not dialog.exec():
            return
        login, password = dialog.credentials()
        self.btn_auth.setEnabled(False)
        self.task.show()
        self.task.busy("Авторизация B2O", "Проверяю учётные данные…")

        def worker():
            try:
                self.service.b2o.login(login, password)
                self.authReady.emit(None)
            except Exception as exc:
                self.authReady.emit(exc)

        threading.Thread(target=worker, daemon=True).start()

    def _on_auth(self, error):
        self.btn_auth.setEnabled(True)
        self._sync_auth()
        if error:
            self.task.error("Авторизация не выполнена", self._friendly_error(error))
        else:
            self.task.done("B2O подключён", "Можно искать архив по ID камеры.")

    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Папка для архива", self.folder.text().strip() or str(Path.home()))
        if path:
            self.folder.setText(path)
            self.settings.setValue("archive/folder_v2", path)

    @staticmethod
    def _py(qdt):
        d = qdt.date()
        t = qdt.time()
        return datetime(d.year(), d.month(), d.day(), t.hour(), t.minute(), t.second())

    def _operator_id(self) -> int:
        try:
            return int(self.operator.current())
        except Exception:
            return 241

    def _operator_changed(self, value: str):
        operator_id = self.service.b2o.valid_operator_id(value)
        self.settings.setValue("archive/operator_id", str(operator_id))
        prefix = self.service.b2o.operator_prefix(operator_id)
        self.camera.setPlaceholderText(f"Например: 268527 или {prefix}268527")

    def _sync_operator_from_camera(self, text: str):
        raw = str(text or "").strip().lower()
        if not raw:
            return
        detected = self.service.b2o.detect_operator_id(raw, self._operator_id())
        # Числовой ID сам по себе не меняет выбор оператора. Префикс камеры — меняет.
        if raw.startswith(("linkvideo_", "linkvideokz_", "linkvideoby_")):
            self.operator.setCurrent(str(detected))

    def _find(self):
        camera_id = self.camera.text().strip()
        if not camera_id:
            self.task.show()
            prefix = self.service.b2o.operator_prefix(self._operator_id())
            self.task.warning("Введите ID камеры", f"Например: 268527 или {prefix}268527")
            return
        if not self.service.b2o.token():
            self._authorize()
            return
        operator = self.service.b2o.resolve_operator_id(camera_id, self._operator_id())
        self.operator.setCurrent(str(operator))
        self.settings.setValue("archive/operator_id", str(operator))
        start = self._py(self.start.value())
        end = self._py(self.end.value())
        if end <= start:
            self.task.show()
            self.task.warning("Проверьте период", "Конец периода должен быть позже начала.")
            return

        self._retry_action = self._find
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self.btn_find.setEnabled(False)
        self.btn_download.setEnabled(False)
        self.discovery = None
        self.coverage.set_status("Поиск…", "neutral")
        self.summary_text.setText("Определяю архив по плейлисту выбранного периода…")
        self.task.show()
        self.task.busy("Поиск архива", "Получаю данные камеры из B2O…")

        def progress(title, detail):
            if not cancel_event.is_set() and self._cancel_event is cancel_event:
                self.downloadUpdate.emit({"type": "discover_stage", "title": title, "detail": detail})

        def worker():
            try:
                result = self.service.discover(camera_id, operator, start, end, progress, cancel_event)
                if not cancel_event.is_set() and self._cancel_event is cancel_event:
                    self.discoveryReady.emit((cancel_event, result), None)
            except Exception as exc:
                if not cancel_event.is_set() and self._cancel_event is cancel_event:
                    self.discoveryReady.emit((cancel_event, None), exc)

        threading.Thread(target=worker, daemon=True).start()

    def _on_discovery(self, payload, error):
        cancel_event, discovery = payload if isinstance(payload, tuple) and len(payload) == 2 else (self._cancel_event, payload)
        if cancel_event is None or cancel_event.is_set() or self._cancel_event is not cancel_event:
            return
        self._cancel_event = None
        self.btn_find.setEnabled(True)
        if error:
            self.task.error("Архив найти не удалось", self._friendly_error(error), retry=True)
            return
        self.discovery = discovery
        cov = discovery.coverage_percent
        kind = "success" if cov >= 99 else ("warning" if cov > 0 else "danger")
        self.coverage.set_status(f"{cov:.1f}% покрытия", kind)
        self.m_requested.setValue(self._dur(discovery.requested_duration))
        self.m_found.setValue(self._dur(discovery.covered_duration))
        hosts = list(dict.fromkeys(x.host for x in discovery.slices))
        checked_hosts = list(dict.fromkeys(discovery.checked_hosts))
        self.m_servers.setValue(str(len(checked_hosts)))
        self.m_parts.setValue(str(len(discovery.slices) if discovery.slices else discovery.hls_fallback_segments))

        if discovery.hls_fallback_url and discovery.hls_fallback_hosts:
            actual = ", ".join(discovery.hls_fallback_hosts[:4])
            self.summary_text.setText(
                "Сервер архива определён по плейлисту плеера за выбранный период: " + actual
            )
        elif discovery.slices:
            self.summary_text.setText("Архив подтверждён на: " + ", ".join(hosts))
        elif discovery.hls_fallback_url:
            self.summary_text.setText(
                f"Архив найден через плейлист плеера на {discovery.hls_fallback_host}."
            )
        else:
            detail = f"Проверено источников: {len(checked_hosts)}. Плейлист и резервные маршруты не подтвердили запись за выбранный период."
            try:
                depth = int(discovery.camera.raw.get("archive_depth") or 0)
            except Exception:
                depth = 0
            if depth > 0 and (time.time() - discovery.requested_end) > depth * 86400:
                detail += f" Выбранный период старше заявленной глубины архива камеры ({depth} дн.), поэтому запись могла уже быть удалена."
            self.summary_text.setText(detail)

        # IMPORTANT: _gaps() naturally returns the whole requested period when
        # zero DVR slices were found. That must NOT be presented as "partial
        # archive". Partial means that at least some media was actually found.
        if discovery.slices and discovery.gaps:
            lines = [
                f"{self.service.format_local(g.start, discovery.camera.timezone_offset)} — "
                f"{self.service.format_local(g.end, discovery.camera.timezone_offset)}   ·   {self._dur(g.duration)}"
                for g in discovery.gaps
            ]
            self.gaps_text.setText("\n".join(lines) + "\n\nHelper скачает все найденные участки и не отменит загрузку из-за этих пропусков.")
            self.gaps_card.show()
            self.task.warning(
                "Архив найден частично",
                f"Доступно {self._dur(discovery.covered_duration)} из {self._dur(discovery.requested_duration)}. Можно скачивать.",
            )
        elif discovery.hls_fallback_url:
            self.gaps_card.hide()
            if discovery.coverage_percent >= 99:
                self.task.done("Архив найден", f"Плейлист плеера подтвердил {self._dur(discovery.covered_duration)} записи.")
            else:
                self.task.warning(
                    "Архив найден по плейлисту",
                    f"Плейлист содержит около {self._dur(discovery.covered_duration)} из {self._dur(discovery.requested_duration)}. Найденную запись можно скачать.",
                )
        elif discovery.slices:
            self.gaps_card.hide()
            self.task.done("Архив найден полностью", f"Доступно {self._dur(discovery.covered_duration)} записи.")
        else:
            self.gaps_card.hide()
            extra = ""
            if discovery.errors:
                extra = " Последняя проверка: " + str(discovery.errors[-1])[:260]
            self.task.error("Архив не найден", "За выбранный период медиасегменты не найдены." + extra)

        self.btn_download.setEnabled(discovery.has_downloadable_archive)
        self.summary.show()

    def _download(self):
        d = self.discovery
        if not d or not d.has_downloadable_archive:
            return
        folder = Path(self.folder.text().strip() or (Path.home() / "Videos" / "LinkVideo_Archive"))
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.task.error("Не удалось открыть папку", str(exc))
            return
        self.settings.setValue("archive/folder_v2", str(folder))
        local_start = self.service.format_local(d.requested_start, d.camera.timezone_offset).replace(".", "-").replace(":", "-").replace(" ", "_")
        local_end = self.service.format_local(d.requested_end, d.camera.timezone_offset).split(" ")[-1].replace(":", "-")
        output = folder / f"{d.camera.label}_{local_start}_{local_end}.mp4"
        # Never silently overwrite an archive downloaded earlier for the same period.
        if output.exists():
            stem = output.stem
            suffix_index = 2
            while True:
                candidate = output.with_name(f"{stem}_{suffix_index}.mp4")
                if not candidate.exists():
                    output = candidate
                    break
                suffix_index += 1
        self._last_output = output
        self._retry_action = self._download
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self.btn_download.setEnabled(False)
        self.btn_find.setEnabled(False)
        self.task.show()
        self.task.busy("Подготовка скачивания", "Проверяю компонент FFmpeg…", 0)

        def worker():
            try:
                result = self.service.download(
                    d, output,
                    lambda payload: (not cancel_event.is_set() and self._cancel_event is cancel_event) and self.downloadUpdate.emit(payload),
                    cancel_event,
                )
                if not cancel_event.is_set() and self._cancel_event is cancel_event:
                    self.downloadUpdate.emit({"type": "done", "result": result, "cancel_event": cancel_event})
            except Exception as exc:
                if not cancel_event.is_set() and self._cancel_event is cancel_event:
                    self.downloadUpdate.emit({"type": "error", "error": exc, "cancel_event": cancel_event})

        threading.Thread(target=worker, daemon=True).start()

    def _on_download_update(self, payload):
        if not isinstance(payload, dict):
            return
        kind = payload.get("type")
        if kind == "discover_stage":
            self.task.busy(str(payload.get("title") or "Поиск архива"), str(payload.get("detail") or ""))
            return
        if kind == "stage":
            current = self.task.progress.value() if self.task.progress.isVisible() else 0
            self.task.busy(str(payload.get("title") or "Скачивание"), str(payload.get("detail") or ""), current)
            return
        if kind == "progress":
            value = int(payload.get("value") or 0)
            done = float(payload.get("done") or 0)
            total = float(payload.get("total") or 0)
            self.task.busy("Скачивание архива", "Сохраняю доступную запись в MP4…", value, f"{self._dur(done)} из {self._dur(total)}")
            return
        if kind == "done":
            self._cancel_event = None
            self.btn_find.setEnabled(True)
            self.btn_download.setEnabled(True)
            result = payload.get("result")
            if not isinstance(result, ArchiveDownloadResult):
                return
            self._last_output = result.output
            source_gap = bool(self.discovery and ((self.discovery.slices and self.discovery.gaps) or (self.discovery.hls_fallback_url and self.discovery.coverage_percent < 99)))
            if result.partial:
                failed_lines = []
                for sl in result.failed_slices:
                    failed_lines.append(
                        f"{self.service.format_local(sl.start, self.discovery.camera.timezone_offset)} — "
                        f"{self.service.format_local(sl.end, self.discovery.camera.timezone_offset)} · {sl.host}"
                    )
                reasons = "\n".join(f"• {x}" for x in result.errors[:4])
                detail = (
                    f"Файл сохранён: {result.output.name}. Не удалось скачать {len(result.failed_slices)} подтверждённых участков:\n"
                    + "\n".join(failed_lines)
                )
                if reasons:
                    detail += "\n\nПричина:\n" + reasons
                self.task.warning("Скачивание завершено частично", detail)
            elif source_gap:
                self.task.warning(
                    "Скачивание завершено",
                    f"Файл сохранён. Получено {self._dur(result.downloaded_duration)}; в исходном архиве были указанные выше пропуски.",
                )
            else:
                self.task.done("Скачивание завершено", f"Файл сохранён: {result.output.name}")
            return
        if kind == "error":
            self._cancel_event = None
            self.btn_find.setEnabled(True)
            self.btn_download.setEnabled(bool(self.discovery and self.discovery.has_downloadable_archive))
            self.task.error("Скачать архив не удалось", self._friendly_error(payload.get("error")), retry=True)

    def _retry(self):
        if callable(self._retry_action):
            self._retry_action()

    def _open_folder(self):
        folder = Path(self.folder.text().strip() or (Path.home() / "Videos" / "LinkVideo_Archive"))
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _copy_path(self):
        QGuiApplication.clipboard().setText(str(self._last_output) if self._last_output else self.folder.text().strip())
        button_feedback(self.btn_copy_path, "✓ Скопировано")

    @staticmethod
    def _friendly_error(error) -> str:
        text = str(error or "").strip()
        low = text.lower()
        if "401" in low or "403" in low or "авториза" in low:
            return "Авторизация B2O истекла или была отклонена. Выполните вход заново."
        if "timed out" in low or "timeout" in low:
            return "Один из серверов не ответил вовремя. Повторите попытку."
        if "no space" in low or "disk full" in low or "недостаточно места" in low:
            return "На диске недостаточно свободного места для сохранения архива."
        if "ffmpeg" in low:
            return "FFmpeg не смог получить или сохранить запись. " + text
        return text or "Неизвестная ошибка"

    @staticmethod
    def _dur(seconds) -> str:
        sec = max(0, int(round(float(seconds or 0))))
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        parts = []
        if h:
            parts.append(f"{h} ч")
        if m:
            parts.append(f"{m} мин")
        if s or not parts:
            parts.append(f"{s} сек")
        return " ".join(parts)
