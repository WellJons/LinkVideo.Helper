from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from PySide6.QtCore import QDateTime, Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from linkvideo_vpn_helper.services.archive_service import ArchiveDiscovery, ArchiveService
from linkvideo_vpn_helper.services.archive_diagnosis_engine import (
    ArchiveDiagnosis, ArchiveDiagnosisEngine, DiagnosisConfidence, DiagnosisSide,
)
from linkvideo_vpn_helper.ui.components import (
    Card,
    DateTimePickerButton,
    PageHeader,
    SegmentedControl,
    StatusPill,
    TaskStatus, build_page_scaffold,
)
from linkvideo_vpn_helper.ui.dialogs import B2OLoginDialog


class ArchiveDiagnosticsPage(QWidget):
    """Диагностика архива без текстового self-parsing из 1.1.x.

    Функционально сохранены полезные проверки старого Helper:
    - основная камера и до четырёх камер того же адреса;
    - точные интервалы/разрывы;
    - reserve-transfers и переезды;
    - сравнение камер адреса;
    - автоматическая проверка массовости на текущем vcore;
    - технический отчёт по проверенным host и ошибкам.

    Все сравнения работают со структурированными данными ArchiveDiscovery.
    """

    resultReady = Signal(object, object)
    statusReady = Signal(str, str)
    authReady = Signal(object)

    MAX_CONTENT_WIDTH = 1180
    MAX_EXTRA_CAMERAS = 4
    DEFAULT_SERVER_COMPARE = 10

    def __init__(self, service: ArchiveService, settings, parent=None):
        super().__init__(parent)
        self.service = service
        self.settings = settings
        self.engine = ArchiveDiagnosisEngine()
        self.result: dict | None = None
        self._cancel_event = None
        self._extra_rows: list[tuple[QWidget, QLineEdit]] = []
        self.resultReady.connect(self._on_result)
        self.statusReady.connect(self._status)
        self.authReady.connect(self._on_auth)
        self._build()

    def _build(self):
        self.page_scroll, self.page_canvas, outer = build_page_scaffold(
            self, max_width=1360, min_width=760, margins=22, spacing=12
        )
        self.page_layout = outer


        header = Card()
        hl = QVBoxLayout(header)
        hl.setContentsMargins(16, 14, 16, 14)
        hl.setSpacing(5)
        title = QLabel("Диагностика архива")
        title.setObjectName("SectionTitle")
        hint = QLabel("Проверка архива, точных разрывов, переездов между vcore и сравнение с другими камерами.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        hl.addWidget(title)
        hl.addWidget(hint)
        outer.addWidget(header)

        form = Card(kind="hero")
        fl = QVBoxLayout(form)
        fl.setContentsMargins(16, 14, 16, 14)
        fl.setSpacing(10)

        # B2O-статус всегда расположен над периодом, чтобы сотрудник сразу
        # видел, готов ли модуль к запросам.
        auth_row = QHBoxLayout()
        self.auth_pill = StatusPill("B2O не подключён", "warning")
        self.auth_user = QLabel("")
        self.auth_user.setObjectName("Muted")
        self.auth_btn = QPushButton("Авторизация B2O")
        self.auth_btn.clicked.connect(self._auth)
        auth_row.addWidget(self.auth_pill)
        auth_row.addWidget(self.auth_user)
        auth_row.addStretch(1)
        auth_row.addWidget(self.auth_btn)
        fl.addLayout(auth_row)

        # Страна выбирает внутренний профиль B2O; числовой ID пользователю не показывается.
        # Числовые ID не редактируются вручную: Россия=241, Казахстан=1721, Беларусь=1741.
        saved_operator = str(self.settings.value("archive/operator_id", "241", str) or "241")
        if saved_operator not in {"241", "1721", "1741"}:
            saved_operator = "241"

        camera_section = QLabel("Камера")
        camera_section.setObjectName("SectionTitle")
        fl.addWidget(camera_section)

        # Страна и ID камеры намеренно расположены в отдельных строках.
        operator_row = QHBoxLayout()
        operator_row.setSpacing(10)
        operator_label = QLabel("Страна")
        operator_label.setMinimumWidth(95)
        self.operator = SegmentedControl([
            ("241", "Россия"),
            ("1721", "Казахстан"),
            ("1741", "Беларусь"),
        ], saved_operator)
        self.operator.changed.connect(self._operator_changed)
        operator_row.addWidget(operator_label)
        operator_row.addWidget(self.operator, 1)
        fl.addLayout(operator_row)

        # ID камеры расположен ниже отдельной широкой строкой.
        camera_row = QHBoxLayout()
        camera_row.setSpacing(10)
        camera_label = QLabel("ID камеры")
        camera_label.setMinimumWidth(95)
        camera_row.addWidget(camera_label)
        self.camera = QLineEdit()
        self.camera.setPlaceholderText("Например: linkvideo_100046")
        self.camera.textChanged.connect(self._sync_operator_from_camera)
        self.camera.returnPressed.connect(self._run)
        camera_row.addWidget(self.camera, 1)
        self.add_camera_btn = QPushButton("+")
        self.add_camera_btn.setProperty("role", "icon")
        self.add_camera_btn.clicked.connect(self._add_extra_camera)
        camera_row.addWidget(self.add_camera_btn)
        fl.addLayout(camera_row)

        self.extra_host = QWidget()
        self.extra_layout = QVBoxLayout(self.extra_host)
        self.extra_layout.setContentsMargins(0, 0, 0, 0)
        self.extra_layout.setSpacing(6)
        fl.addWidget(self.extra_host)

        period_section = QLabel("Период проверки")
        period_section.setObjectName("SectionTitle")
        fl.addWidget(period_section)

        self.period_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.period_row.setSpacing(10)
        self.period_row.addWidget(QLabel("Период с"))
        now_dt = QDateTime.currentDateTime()
        now_dt = now_dt.addSecs(-now_dt.time().second())
        self.start = DateTimePickerButton(now_dt.addSecs(-300))
        self.period_row.addWidget(self.start, 1)
        self.period_row.addWidget(QLabel("по"))
        self.end = DateTimePickerButton(now_dt)
        self.period_row.addWidget(self.end, 1)
        fl.addLayout(self.period_row)

        self.timezone_note = QLabel(
            "Период вводится во времени камеры. Журнал reserve-transfers работает в UTC+7; "
            "Helper автоматически переводит время камеры и UTC+7 в один epoch перед сопоставлением."
        )
        self.timezone_note.setObjectName("TinyMuted")
        self.timezone_note.setWordWrap(True)
        fl.addWidget(self.timezone_note)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.run = QPushButton("Проверить архив")
        self.run.setProperty("role", "primary")
        self.run.setMinimumHeight(44)
        self.run.clicked.connect(self._run)
        actions.addWidget(self.run)
        fl.addLayout(actions)
        self._sync_auth()
        self._operator_changed(saved_operator)
        outer.addWidget(form)

        self.task = TaskStatus()
        self.task.hide()
        outer.addWidget(self.task)

        self.report = Card()
        rl = QVBoxLayout(self.report)
        rl.setContentsMargins(16, 15, 16, 16)
        rl.setSpacing(10)
        top = QHBoxLayout()
        rt = QLabel("Результат")
        rt.setObjectName("SectionTitle")
        self.badge = StatusPill("Ожидание", "neutral")
        top.addWidget(rt)
        top.addStretch(1)
        top.addWidget(self.badge)
        rl.addLayout(top)

        # Главный вывод диагностики показывается отдельным визуальным блоком.
        # Это не инструкция, а результат анализа реальных DVR gaps / address / vcore / reserve.
        self.verdict = Card(kind="accent")
        vl = QVBoxLayout(self.verdict)
        vl.setContentsMargins(16, 14, 16, 14)
        vl.setSpacing(7)
        verdict_top = QHBoxLayout()
        self.verdict_title = QLabel("Причина ещё не определена")
        self.verdict_title.setObjectName("SectionTitle")
        self.verdict_side = StatusPill("Ожидание", "neutral")
        self.verdict_confidence = StatusPill("Уверенность: —", "neutral")
        verdict_top.addWidget(self.verdict_title, 1)
        verdict_top.addWidget(self.verdict_side)
        verdict_top.addWidget(self.verdict_confidence)
        vl.addLayout(verdict_top)
        self.verdict_summary = QLabel("")
        self.verdict_summary.setObjectName("Muted")
        self.verdict_summary.setWordWrap(True)
        vl.addWidget(self.verdict_summary)
        self.verdict_evidence_host = QWidget()
        self.verdict_evidence = QVBoxLayout(self.verdict_evidence_host)
        self.verdict_evidence.setContentsMargins(0, 2, 0, 0)
        self.verdict_evidence.setSpacing(4)
        vl.addWidget(self.verdict_evidence_host)
        self.verdict.hide()
        rl.addWidget(self.verdict)

        self.mode = SegmentedControl([
            ("summary", "Итог"),
            ("breaks", "Разрывы"),
            ("moves", "Переезды"),
            ("compare", "Сравнение"),
            ("technical", "Тех. детали"),
        ], "summary")
        self.mode.changed.connect(self._show_report)
        rl.addWidget(self.mode)

        self.report_text = QTextEdit()
        self.report_text.setReadOnly(True)
        self.report_text.setMinimumHeight(280)
        self.report_text.setMaximumHeight(520)
        self.report_text.setPlaceholderText("После проверки здесь появится отчёт и вывод о вероятной стороне проблемы.")
        rl.addWidget(self.report_text)
        outer.addWidget(self.report)
        outer.addStretch(1)

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
        if hasattr(self, "period_row"):
            self.period_row.setDirection(
                QBoxLayout.Direction.TopToBottom if self.width() < 900 else QBoxLayout.Direction.LeftToRight
            )

    def _add_extra_camera(self):
        if len(self._extra_rows) >= self.MAX_EXTRA_CAMERAS:
            return
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        label = QLabel(f"Камера этого адреса {len(self._extra_rows) + 2}")
        label.setObjectName("TinyMuted")
        edit = QLineEdit()
        prefix = self.service.b2o.operator_prefix(self._operator_id())
        edit.setPlaceholderText(f"Например: {prefix}268528")
        remove = QPushButton("×")
        remove.setProperty("role", "icon")
        remove.clicked.connect(lambda: self._remove_extra_camera(row_widget))
        row.addWidget(label)
        row.addWidget(edit, 1)
        row.addWidget(remove)
        self.extra_layout.addWidget(row_widget)
        self._extra_rows.append((row_widget, edit))
        self.add_camera_btn.setEnabled(len(self._extra_rows) < self.MAX_EXTRA_CAMERAS)

    def _remove_extra_camera(self, widget):
        remaining = []
        for row, edit in self._extra_rows:
            if row is widget:
                self.extra_layout.removeWidget(row)
                row.deleteLater()
            else:
                remaining.append((row, edit))
        self._extra_rows = remaining
        self.add_camera_btn.setEnabled(True)

    def _extra_ids(self) -> list[str]:
        result = []
        for _, edit in self._extra_rows:
            value = edit.text().strip()
            if value and value not in result:
                result.append(value)
        return result

    def cancel_current_action(self) -> bool:
        event = self._cancel_event
        if event is None or event.is_set():
            return False
        event.set()
        self._cancel_event = None
        self.run.setEnabled(True)
        self.task.show()
        self.task.warning("Диагностика остановлена", "Проверка отменена клавишей Esc.")
        return True

    def _sync_auth(self):
        token = self.service.b2o.token()
        login = self.service.b2o.login_name()
        if token:
            self.auth_pill.set_status("B2O подключён", "success")
            self.auth_user.setText(login)
            self.auth_btn.setText("Сменить вход")
        else:
            self.auth_pill.set_status("Нужен вход B2O", "warning")
            self.auth_user.setText("")
            self.auth_btn.setText("Авторизация B2O")

    def _auth(self):
        dialog = B2OLoginDialog(self.service.b2o.login_name(), self)
        if not dialog.exec():
            return
        login, password = dialog.credentials()
        self.auth_btn.setEnabled(False)
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
        self.auth_btn.setEnabled(True)
        self._sync_auth()
        if error:
            self.task.error("Авторизация не выполнена", str(error))
        else:
            self.task.done("B2O подключён", "Можно запускать диагностику.")

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
        self.camera.setPlaceholderText(f"Например: {prefix}268527")
        for _row, edit in self._extra_rows:
            edit.setPlaceholderText(f"Например: {prefix}268528")

    def _sync_operator_from_camera(self, text: str):
        raw = str(text or "").strip().lower()
        if not raw:
            return
        if raw.startswith(("linkvideo_", "linkvideokz_", "linkvideoby_")):
            detected = self.service.b2o.detect_operator_id(raw, self._operator_id())
            self.operator.setCurrent(str(detected))

    def _run(self):
        if not self.camera.text().strip():
            self.task.show()
            self.task.warning("Введите ID камеры", "Например: linkvideo_100046")
            return
        if not self.service.b2o.token():
            self._auth()
            return

        start = self._py(self.start.value())
        end = self._py(self.end.value())
        if end <= start:
            self.task.show()
            self.task.warning("Проверьте период", "Конец периода должен быть позже начала.")
            return

        main_id = self.camera.text().strip()
        operator = self.service.b2o.resolve_operator_id(main_id, self._operator_id())
        self.operator.setCurrent(str(operator))
        self.settings.setValue("archive/operator_id", str(operator))
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        address_ids = self._extra_ids()

        self.run.setEnabled(False)
        self.report_text.clear()
        self.badge.set_status("Проверка…", "neutral")
        self.task.show()
        self.task.busy("Диагностика архива", "Получаю данные основной камеры…")

        def worker():
            try:
                main = self.service.discover(
                    main_id,
                    operator,
                    start,
                    end,
                    lambda a, b: (not cancel_event.is_set() and self._cancel_event is cancel_event) and self.statusReady.emit(a, b),
                    cancel_event,
                )

                # Сравнение камер одного адреса выполняется по тому же epoch-окну,
                # что и основная камера. Это важно, если в B2O у камер разный tz.
                address_compare: list[tuple[str, ArchiveDiscovery | None, Exception | None]] = []
                if address_ids:
                    self.statusReady.emit("Сравниваю камеры адреса", f"Камер: {len(address_ids)}")
                    with ThreadPoolExecutor(max_workers=min(4, len(address_ids)), thread_name_prefix="archive-address") as pool:
                        futures = {
                            pool.submit(
                                self.service.diagnose_epoch,
                                cid,
                                operator,
                                main.requested_start,
                                main.requested_end,
                                fast=False,
                                cancel_event=cancel_event,
                            ): cid
                            for cid in address_ids
                        }
                        for future in as_completed(futures):
                            if cancel_event.is_set():
                                for item in futures:
                                    item.cancel()
                                return
                            cid = futures[future]
                            try:
                                address_compare.append((cid, future.result(), None))
                            except Exception as exc:
                                address_compare.append((cid, None, exc))

                # В старом Helper была отдельная проверка массовости по vcore.
                # Возвращаем её, но коротким DVR timeline-запросом без глубокого
                # перебора для каждой чужой камеры.
                self.statusReady.emit("Проверяю массовость", f"Ищу другие камеры на {main.camera.server}…")
                server_candidates, server_debug = self.service.b2o.cameras_on_server(
                    operator,
                    main.camera.server,
                    int(self.settings.value("archive_diag/compare_limit", self.DEFAULT_SERVER_COMPARE, int) or self.DEFAULT_SERVER_COMPARE),
                )
                main_num = str(main.camera.camera_id)
                address_nums = set()
                for raw in address_ids:
                    try:
                        number, _ = self.service.b2o.normalize_camera_id(raw)
                        address_nums.add(number)
                    except Exception:
                        pass

                filtered = []
                for cam in server_candidates:
                    cid = str(cam.get("id_camera") or "").strip()
                    if not cid or cid == main_num or cid in address_nums:
                        continue
                    filtered.append(cid)
                limit = max(1, min(15, int(self.settings.value("archive_diag/compare_limit", self.DEFAULT_SERVER_COMPARE, int) or self.DEFAULT_SERVER_COMPARE)))
                filtered = filtered[:limit]

                server_compare: list[tuple[str, ArchiveDiscovery | None, Exception | None]] = []
                if filtered:
                    self.statusReady.emit("Проверяю массовость", f"Камер на vcore: {len(filtered)}")
                    with ThreadPoolExecutor(max_workers=min(6, len(filtered)), thread_name_prefix="archive-vcore") as pool:
                        futures = {
                            pool.submit(
                                self.service.diagnose_epoch,
                                cid,
                                operator,
                                main.requested_start,
                                main.requested_end,
                                fast=True,
                                cancel_event=cancel_event,
                            ): cid
                            for cid in filtered
                        }
                        for future in as_completed(futures):
                            if cancel_event.is_set():
                                for item in futures:
                                    item.cancel()
                                return
                            cid = futures[future]
                            try:
                                server_compare.append((cid, future.result(), None))
                            except Exception as exc:
                                server_compare.append((cid, None, exc))

                if cancel_event.is_set() or self._cancel_event is not cancel_event:
                    return
                result = self._build_reports(main, address_compare, server_compare, server_debug)
                self.resultReady.emit((cancel_event, result), None)
            except Exception as exc:
                if not cancel_event.is_set() and self._cancel_event is cancel_event:
                    self.resultReady.emit((cancel_event, None), exc)

        threading.Thread(target=worker, daemon=True).start()

    def _status(self, title, detail):
        self.task.busy(title, detail)

    @staticmethod
    def _is_problem(discovery: ArchiveDiscovery) -> bool:
        return discovery.coverage_percent < 99.5 or any(g.duration >= 5 for g in discovery.gaps)

    @staticmethod
    def _gap_overlap_seconds(a: ArchiveDiscovery, b: ArchiveDiscovery) -> float:
        total = 0.0
        for ga in a.gaps:
            for gb in b.gaps:
                total += max(0.0, min(ga.end, gb.end) - max(ga.start, gb.start))
        return total

    def _build_reports(
        self,
        main: ArchiveDiscovery,
        address_compare: list[tuple[str, ArchiveDiscovery | None, Exception | None]],
        server_compare: list[tuple[str, ArchiveDiscovery | None, Exception | None]],
        server_debug: dict,
    ) -> dict:
        cov = main.coverage_percent
        reserve_start, reserve_end = self.service.reserve_log_period(main.requested_start, main.requested_end)
        client_start = self.service.format_local(main.requested_start, main.camera.timezone_offset)
        client_end = self.service.format_local(main.requested_end, main.camera.timezone_offset)

        valid_address = [(cid, d) for cid, d, err in address_compare if d is not None]
        valid_server = [(cid, d) for cid, d, err in server_compare if d is not None]
        diagnosis = self.engine.analyze(main, address_compare, server_compare)
        bad_address = [(cid, d) for cid, d in valid_address if self._is_problem(d)]
        bad_server = [(cid, d) for cid, d in valid_server if self._is_problem(d)]
        address_overlap = [
            (cid, d, self._gap_overlap_seconds(main, d))
            for cid, d in bad_address
            if self._gap_overlap_seconds(main, d) > 0.5
        ]
        server_overlap = [
            (cid, d, self._gap_overlap_seconds(main, d))
            for cid, d in bad_server
            if self._gap_overlap_seconds(main, d) > 0.5
        ]

        summary: list[str] = [
            "ИТОГ ДИАГНОСТИКИ",
            "",
            f"Камера: {main.camera.label}",
            f"Текущий server_name: {main.camera.server}",
            f"Период камеры: {client_start} — {client_end}  UTC{main.camera.timezone_offset:+d}",
            f"Запрошено: {self._dur(main.requested_duration)}",
            f"Подтверждено: {self._dur(main.covered_duration)} ({cov:.1f}%)",
            f"Пропусков: {len(main.gaps)}",
            "",
        ]

        if cov >= 99.5:
            summary.append("[OK] Архив за выбранный период найден практически полностью.")
        elif cov > 0:
            summary.append("[WARN] Архив найден частично. Точные интервалы указаны во вкладке «Разрывы».")
        else:
            summary.append("[CRITICAL] Подтверждённый DVR-архив за выбранный период не найден на проверенных серверах.")

        summary.extend(["", "Переезды / reserve-transfers:"])
        summary.append(f"Окно журнала B2O UTC+7: {reserve_start} — {reserve_end}")
        if main.reserve_events:
            for event in main.reserve_events:
                when = self.service.format_reserve_log_time(event.start) if event.start is not None else "время не определено"
                summary.append(f"- {event.server_from} → {event.server_to}; {when} UTC+7")
            if main.gaps and self._move_overlaps_any_gap(main):
                summary.append("[MOVE] Время переезда пересекается с одним из отсутствующих участков архива.")
        else:
            summary.append("- событий, пересекающих выбранный epoch-период, не найдено")

        if valid_address:
            summary.extend(["", f"Камеры этого адреса: проверено {len(valid_address)}."])
            if address_overlap:
                summary.append(f"[LOCAL] Совпадающие по времени разрывы найдены у {len(address_overlap)} камер адреса.")
            elif bad_address:
                summary.append(f"Неполный архив есть у {len(bad_address)}/{len(valid_address)} камер адреса, но точное совпадение разрывов не подтверждено.")
            else:
                summary.append("У добавленных камер этого адреса сопоставимых разрывов не найдено.")

        if valid_server:
            summary.extend(["", f"Массовость на {main.camera.server}: быстро проверено {len(valid_server)} камер."])
            if server_overlap:
                summary.append(f"[SERVER] Совпадающие по времени разрывы есть минимум у {len(server_overlap)} других камер этого vcore.")
            elif len(bad_server) >= max(3, len(valid_server) // 2):
                summary.append(f"[SERVER] Неполный архив обнаружен у {len(bad_server)}/{len(valid_server)} проверенных камер этого vcore.")
            else:
                summary.append(f"Сильная массовость не подтверждена: проблемных {len(bad_server)}/{len(valid_server)}.")
        elif server_compare:
            summary.extend(["", "Массовость по vcore проверить не удалось на достаточном числе камер."])

        summary.extend([
            "",
            "ВЕРОЯТНАЯ ПРИЧИНА",
            diagnosis.title,
            f"Сторона: {diagnosis.side_text}",
            f"Уверенность: {diagnosis.confidence_text}",
            diagnosis.summary,
        ])
        if diagnosis.evidence:
            summary.extend(["", "Ключевые факты:"])
            for ev in diagnosis.evidence:
                summary.append(f"- {ev.title}: {ev.detail}")
        if diagnosis.cautions:
            summary.extend(["", "Что ограничивает точность вывода:"])
            summary.extend(f"- {x}" for x in diagnosis.cautions)

        breaks: list[str] = ["РАЗРЫВЫ ПО ВРЕМЕНИ", ""]
        if main.gaps:
            for index, gap in enumerate(main.gaps, 1):
                a = self.service.format_local(gap.start, main.camera.timezone_offset)
                b = self.service.format_local(gap.end, main.camera.timezone_offset)
                breaks.append(f"{index}. {a} — {b}   ({self._dur(gap.duration)})")
                if index - 1 < len(diagnosis.gaps):
                    gd = diagnosis.gaps[index - 1]
                    breaks.append(f"   Причина: {gd.title} · уверенность {self._confidence_text(gd.confidence)}")
                    breaks.append(f"   {gd.summary}")
                for event in main.reserve_events:
                    if event.overlaps(gap.start, gap.end):
                        move_time = self.service.format_reserve_log_time(event.start) if event.start is not None else "не определено"
                        breaks.append(f"   ↳ переезд: {event.server_from} → {event.server_to}, {move_time} UTC+7")
                for cid, d in valid_address:
                    overlap = sum(
                        max(0.0, min(gap.end, other.end) - max(gap.start, other.start))
                        for other in d.gaps
                    )
                    if overlap > 0.5:
                        breaks.append(f"   ↳ камера {d.camera.label}: совпадает {self._dur(overlap)}")
                matches = 0
                for cid, d in valid_server:
                    overlap = sum(
                        max(0.0, min(gap.end, other.end) - max(gap.start, other.start))
                        for other in d.gaps
                    )
                    if overlap > 0.5:
                        matches += 1
                if matches:
                    breaks.append(f"   ↳ на текущем vcore похожий разрыв: ещё у {matches} камер")
        else:
            breaks.append("Подтверждённых пропусков в выбранном периоде нет.")

        breaks.extend(["", "Подтверждённые участки основной камеры:"])
        for sl in main.slices:
            a = self.service.format_local(sl.start, main.camera.timezone_offset)
            b = self.service.format_local(sl.end, main.camera.timezone_offset)
            breaks.append(f"- {a} — {b} · {sl.host} · {self._source_name(sl.source)}")

        moves: list[str] = ["ПЕРЕЕЗДЫ / RESERVE-TRANSFERS", ""]
        moves.append(f"Период камеры: {client_start} — {client_end}  UTC{main.camera.timezone_offset:+d}")
        moves.append(f"Тот же период журнала B2O: {reserve_start} — {reserve_end}  UTC+7")
        moves.append("Сопоставление выполняется по epoch, поэтому 05:03 UTC+3 и 09:03 UTC+7 считаются одним моментом.")
        moves.append("")
        if main.reserve_events:
            for index, event in enumerate(main.reserve_events, 1):
                start_move = self.service.format_reserve_log_time(event.start) if event.start is not None else "время не определено"
                end_move = self.service.format_reserve_log_time(event.end) if event.end is not None else "не завершён"
                overlap = any(event.overlaps(g.start, g.end) for g in main.gaps)
                moves.append(
                    f"{index}. {event.server_from} → {event.server_to} · {start_move} — {end_move} UTC+7"
                    + (" · ПЕРЕСЕКАЕТ РАЗРЫВ" if overlap else "")
                )
                if event.status_description:
                    moves.append(f"   {event.status_description}")
        else:
            moves.append("Событий reserve-transfer, относящихся к выбранному периоду, не найдено.")

        compare: list[str] = ["СРАВНЕНИЕ КАМЕР", ""]
        compare.append(f"Основная: {main.camera.label} — {cov:.1f}% · пропусков {len(main.gaps)} · {main.camera.server}")
        compare.extend(["", "Камеры этого же адреса:"])
        if not address_compare:
            compare.append("Дополнительные камеры не указаны. Кнопка «+» позволяет добавить ещё до четырёх камер адреса.")
        else:
            for cid, discovery, error in sorted(address_compare, key=lambda x: x[0]):
                if error or discovery is None:
                    compare.append(f"{cid}: проверить не удалось — {error}")
                    continue
                overlap = self._gap_overlap_seconds(main, discovery)
                compare.append(
                    f"{discovery.camera.label}: {discovery.coverage_percent:.1f}% · "
                    f"пропусков {len(discovery.gaps)} · совпадение разрывов {self._dur(overlap)} · {discovery.camera.server}"
                )

        compare.extend(["", f"Другие камеры текущего vcore {main.camera.server}:"])
        if not server_compare:
            compare.append("Подходящие онлайн-камеры для массового сравнения не найдены или список B2O недоступен.")
        else:
            for cid, discovery, error in sorted(server_compare, key=lambda x: x[0]):
                if error or discovery is None:
                    compare.append(f"linkvideo_{cid}: ERROR {error}")
                    continue
                overlap = self._gap_overlap_seconds(main, discovery)
                compare.append(
                    f"{discovery.camera.label}: {discovery.coverage_percent:.1f}% · "
                    f"пропусков {len(discovery.gaps)} · совпадение {self._dur(overlap)}"
                )

        compare.extend(["", "Сводка:"])
        compare.append(f"- камеры адреса с неполным архивом: {len(bad_address)}/{len(valid_address)}")
        compare.append(f"- камеры vcore с неполным архивом: {len(bad_server)}/{len(valid_server)}")
        compare.append(f"- совпадение разрывов по адресу: {len(address_overlap)}")
        compare.append(f"- совпадение разрывов по vcore: {len(server_overlap)}")

        technical: list[str] = [
            "ТЕХНИЧЕСКИЕ ДЕТАЛИ",
            "",
            f"Camera ID: {main.camera.camera_id}",
            f"Stream: {main.camera.stream_name}",
            f"Timezone камеры: UTC{main.camera.timezone_offset:+d}",
            f"Период клиента: {client_start} — {client_end}",
            f"Тот же epoch-период в reserve-transfers: {reserve_start} — {reserve_end} UTC+7",
            "Правило времени: строковые даты reserve-transfers без offset интерпретируются строго как UTC+7.",
            "",
            "Проверенные DVR host основной камеры:",
        ]
        if main.checked_hosts:
            technical.extend(f"- {x}" for x in main.checked_hosts)
        else:
            technical.append("- нет")

        technical.extend(["", "Reserve events:"])
        if main.reserve_events:
            for event in main.reserve_events:
                a = self.service.format_reserve_log_time(event.start) if event.start is not None else "—"
                b = self.service.format_reserve_log_time(event.end) if event.end is not None else "не завершён"
                technical.append(
                    f"- {event.server_from} → {event.server_to}; {a} — {b} UTC+7; "
                    f"status={event.status or '—'}; return_status={event.return_status}"
                )
        else:
            technical.append("- нет событий в выбранном окне")

        technical.extend(["", "DVR slices основной камеры:"])
        if main.slices:
            for sl in main.slices:
                technical.append(f"- {sl.host} {sl.app}/{sl.stream}: {sl.start:.0f}..{sl.end:.0f}; source={sl.source}")
        else:
            technical.append("- нет подтверждённых slices")

        if main.errors:
            technical.extend(["", "Ошибки отдельных проверок основной камеры:"])
            technical.extend(f"- {x}" for x in main.errors)

        technical.extend(["", "Автосравнение vcore:"])
        technical.append(f"- найдено B2O Search: {server_debug.get('found_total', 0)}")
        technical.append(f"- online с точным server_name: {server_debug.get('online_same_server', 0)}")
        technical.append(f"- страниц B2O: {server_debug.get('pages', 0)}")
        for error in server_debug.get("errors") or []:
            technical.append(f"- ошибка списка: {error}")
        technical.append(f"- реально проверено DVR: {len(valid_server)}/{len(server_compare)}")

        if address_compare:
            technical.extend(["", "Камеры адреса:"])
            for cid, discovery, error in address_compare:
                if discovery:
                    technical.append(
                        f"- {discovery.camera.label}: coverage={discovery.coverage_percent:.1f}; "
                        f"gaps={len(discovery.gaps)}; host={discovery.camera.server}; errors={len(discovery.errors)}"
                    )
                else:
                    technical.append(f"- {cid}: ERROR {error}")

        return {
            "main": main,
            "address_compare": address_compare,
            "server_compare": server_compare,
            "server_debug": server_debug,
            "diagnosis": diagnosis,
            "reports": {
                "summary": "\n".join(summary),
                "breaks": "\n".join(breaks),
                "moves": "\n".join(moves),
                "compare": "\n".join(compare),
                "technical": "\n".join(technical),
            },
        }

    @staticmethod
    def _move_overlaps_any_gap(discovery: ArchiveDiscovery) -> bool:
        for gap in discovery.gaps:
            for event in discovery.reserve_events:
                if event.overlaps(gap.start, gap.end):
                    return True
        return False

    def _on_result(self, payload, error):
        cancel_event, result = payload if isinstance(payload, tuple) and len(payload) == 2 else (self._cancel_event, payload)
        if cancel_event is None or cancel_event.is_set() or self._cancel_event is not cancel_event:
            return
        self._cancel_event = None
        self.run.setEnabled(True)
        if error:
            self.task.error("Диагностика не выполнена", str(error))
            return
        self.result = result
        main: ArchiveDiscovery = result["main"]
        diagnosis: ArchiveDiagnosis = result["diagnosis"]
        self._render_verdict(diagnosis)
        cov = main.coverage_percent
        kind = "success" if cov >= 99 else ("warning" if cov > 0 else "danger")
        self.badge.set_status(f"{cov:.1f}% покрытия", kind)
        self.report.show()
        self.mode.setCurrent("summary")
        self._show_report("summary")

        address_ok = sum(1 for _, d, _ in result["address_compare"] if d is not None)
        server_ok = sum(1 for _, d, _ in result["server_compare"] if d is not None)
        detail = f"Подтверждено {self._dur(main.covered_duration)} из {self._dur(main.requested_duration)}"
        if result["address_compare"]:
            detail += f" · камеры адреса {address_ok}/{len(result['address_compare'])}"
        if result["server_compare"]:
            detail += f" · vcore {server_ok}/{len(result['server_compare'])}"
        self.task.done("Диагностика завершена", detail)

    def _render_verdict(self, diagnosis: ArchiveDiagnosis):
        self.verdict.show()
        self.verdict_title.setText(diagnosis.title)
        self.verdict_summary.setText(diagnosis.summary)
        side_kind = {
            DiagnosisSide.NONE: "success",
            DiagnosisSide.CAMERA: "warning",
            DiagnosisSide.CLIENT_SITE: "warning",
            DiagnosisSide.SERVER: "danger",
            DiagnosisSide.MOVE: "info",
            DiagnosisSide.UNKNOWN: "neutral",
        }.get(diagnosis.side, "neutral")
        conf_kind = {
            DiagnosisConfidence.HIGH: "success",
            DiagnosisConfidence.MEDIUM: "warning",
            DiagnosisConfidence.LOW: "warning",
            DiagnosisConfidence.INSUFFICIENT: "neutral",
        }.get(diagnosis.confidence, "neutral")
        self.verdict_side.set_status(diagnosis.side_text, side_kind)
        self.verdict_confidence.set_status(f"Уверенность: {diagnosis.confidence_text}", conf_kind)
        while self.verdict_evidence.count():
            item = self.verdict_evidence.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for evidence in diagnosis.evidence[:6]:
            label = QLabel(f"• {evidence.title}: {evidence.detail}")
            label.setObjectName({
                "success": "SuccessText",
                "warning": "WarningText",
                "danger": "DangerText",
                "info": "InfoText",
            }.get(evidence.kind, "Muted"))
            label.setWordWrap(True)
            self.verdict_evidence.addWidget(label)
        for caution in diagnosis.cautions[:3]:
            label = QLabel(f"• Ограничение: {caution}")
            label.setObjectName("TinyMuted")
            label.setWordWrap(True)
            self.verdict_evidence.addWidget(label)

    @staticmethod
    def _confidence_text(value: DiagnosisConfidence) -> str:
        return {
            DiagnosisConfidence.HIGH: "высокая",
            DiagnosisConfidence.MEDIUM: "средняя",
            DiagnosisConfidence.LOW: "низкая",
            DiagnosisConfidence.INSUFFICIENT: "недостаточно данных",
        }.get(value, "—")

    def _show_report(self, mode: str):
        if not self.result:
            return
        self.report_text.setPlainText(self.result["reports"].get(mode, ""))

    @staticmethod
    def _source_name(value: str) -> str:
        return {
            "main": "основной сервер",
            "reserve": "reserve / переезд",
            "camera": "B2O кандидат",
            "history": "история камеры",
            "deep": "найден прошлый vcore",
            "compare": "сравнение",
        }.get(str(value or ""), str(value or ""))

    @staticmethod
    def _dur(seconds: float) -> str:
        total = max(0, int(round(seconds or 0)))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h} ч {m:02d} мин {s:02d} сек"
        if m:
            return f"{m} мин {s:02d} сек"
        return f"{s} сек"
