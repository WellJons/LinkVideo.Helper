from __future__ import annotations

"""Operator camera card. Every network operation runs outside the Qt thread."""

import threading
from dataclasses import replace

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.b2o_camera_preview import fetch_camera_preview
from linkvideo_vpn_helper.services.camera_diagnostics import (
    CameraLookupError, inspect_camera,
)
from linkvideo_vpn_helper.services.camera_live_probe import probe_live_camera, _cached_ffmpeg
from linkvideo_vpn_helper.services.b2o_rtsp_probe import fetch_camera_stream
from linkvideo_vpn_helper.ui.components import Card, StatusPill


_FFMPEG_SETUP_LOCK = threading.Lock()


class CameraDetailDialog(QWidget):
    """Embedded camera details in the search workspace (legacy public name)."""
    backRequested = Signal()
    detailsReady = Signal(object, str)
    previewReady = Signal(object, str)
    telemetryReady = Signal(object)
    telemetryProgress = Signal(int, str)

    def __init__(self, choice, settings, parent=None, *, inspector=inspect_camera,
                 preview_loader=fetch_camera_preview, live_probe=probe_live_camera):
        super().__init__(parent)
        self.choice = choice
        self.settings = settings
        self.inspector = inspector
        self.preview_loader = preview_loader
        self.live_probe = live_probe
        self._generation = 0
        self._preview_bytes = b""
        self._last_report = None
        self._last_telemetry = None
        self.setObjectName("CameraInformationPage")
        self.setWindowTitle("Информация по камере · LinkVideo.Helper")
        self.setMinimumSize(0, 0)
        self._build()
        self.detailsReady.connect(self._on_details)
        self.previewReady.connect(self._on_preview)
        self.telemetryReady.connect(self._on_telemetry)
        self.telemetryProgress.connect(self._on_progress)
        QTimer.singleShot(0, self.refresh)

    def showEvent(self, event):
        super().showEvent(event)
        # The clicked search-row button must not cause Qt to open the nested
        # scroll view halfway down the camera card.
        QTimer.singleShot(
            0, lambda: self.detail_scroll.verticalScrollBar().setValue(0)
        )

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 0)
        outer.setSpacing(12)
        header_bar = Card(subtle=True)
        header = QHBoxLayout(header_bar)
        header.setContentsMargins(14, 12, 14, 12)
        header.setSpacing(12)
        self.back_button = QPushButton("← К результатам")
        self.back_button.setObjectName("CameraBackToResults")
        self.back_button.setProperty("role", "soft")
        self.back_button.clicked.connect(self.backRequested.emit)
        header.addWidget(self.back_button)
        name_column = QVBoxLayout()
        self.heading = QLabel(self.choice.label)
        self.heading.setObjectName("PageTitle")
        self.heading.setWordWrap(True)
        name_column.addWidget(self.heading)
        extra = self.choice.country
        if self.choice.customer_login:
            extra += " · " + self.choice.customer_login
        self.subtitle = QLabel(extra)
        self.subtitle.setObjectName("Muted")
        name_column.addWidget(self.subtitle)
        header.addLayout(name_column, 1)
        self.status = StatusPill("Загрузка", "neutral")
        header.addWidget(self.status)
        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.setProperty("role", "soft")
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn)
        outer.addWidget(header_bar)

        scroll = QScrollArea(self)
        self.detail_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        block = QVBoxLayout(inner)
        block.setContentsMargins(1, 2, 1, 8)
        block.setSpacing(12)

        split = QHBoxLayout()
        split.setSpacing(12)
        image_card = Card(kind="hero")
        image_layout = QVBoxLayout(image_card)
        image_layout.setContentsMargins(15, 15, 15, 15)
        image_title = QLabel("Превью камеры")
        image_title.setObjectName("CardTitle")
        image_layout.addWidget(image_title)
        self.image = QLabel("Загружаю превью…")
        self.image.setObjectName("CameraCardPreview")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumHeight(225)
        self.image.setMinimumWidth(310)
        self.image.setWordWrap(True)
        self.image.setStyleSheet(
            "QLabel#CameraCardPreview {background: palette(alternate-base); "
            "border: 1px solid palette(mid); border-radius: 10px; padding: 10px;}"
        )
        image_layout.addWidget(self.image, 1)
        caption = QLabel("Последний кадр из B2O · не прямой видеопоток")
        caption.setObjectName("TinyMuted")
        caption.setWordWrap(True)
        image_layout.addWidget(caption)
        split.addWidget(image_card, 5)

        metrics = Card(kind="hero")
        metrics_layout = QVBoxLayout(metrics)
        metrics_layout.setContentsMargins(14, 14, 14, 14)
        metrics_layout.setSpacing(10)
        top = QLabel("Диагностика потока")
        top.setObjectName("CardTitle")
        metrics_layout.addWidget(top)
        metric_grid = QGridLayout()
        metric_grid.setSpacing(8)
        self.rate = self._metric(metric_grid, "Битрейт", "Измеряю…")
        self.fps = self._metric(metric_grid, "FPS", "Проверяю…")
        self.resolution = self._metric(metric_grid, "Разрешение", "Проверяю…")
        self.video_codec = self._metric(metric_grid, "Видеокодек", "Проверяю…")
        self.audio_codec = self._metric(metric_grid, "Аудиокодек", "Проверяю…")
        self.connection = self._metric(metric_grid, "RTSP", "Проверяю…")
        metrics_layout.addLayout(metric_grid)
        self.last = self._metric(metric_grid, "Последние данные видео", "Проверяю…")
        metrics_layout.addStretch(1)
        split.addWidget(metrics, 4)
        block.addLayout(split)

        details = Card()
        detail_layout = QVBoxLayout(details)
        title = QLabel("Данные камеры")
        title.setObjectName("CardTitle")
        detail_layout.addWidget(title)
        self.info = QLabel("Получаю данные из админки…")
        self.info.setObjectName("Muted")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(self.info)
        self.facts = QGridLayout()
        self.facts.setHorizontalSpacing(18)
        self.facts.setVerticalSpacing(10)
        detail_layout.addLayout(self.facts)
        block.addWidget(details)

        diagnostic = Card(subtle=True)
        d = QVBoxLayout(diagnostic)
        title = QLabel("Диагностика")
        title.setObjectName("CardTitle")
        d.addWidget(title)
        self.diagnostic_text = QLabel(
            "Проверяю доступность и наличие свежего видео в фоновом режиме…"
        )
        self.diagnostic_text.setObjectName("Muted")
        self.diagnostic_text.setWordWrap(True)
        self.diagnostic_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        d.addWidget(self.diagnostic_text)
        block.addWidget(diagnostic)
        block.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        note = QLabel("Данные собираются в фоне · RTSP и архив проверяются отдельно")
        note.setObjectName("TinyMuted")
        outer.addWidget(note)

    @staticmethod
    def _metric(layout, title, value):
        tile = Card(subtle=True)
        frame = QVBoxLayout(tile)
        frame.setContentsMargins(10, 8, 10, 8)
        frame.setSpacing(4)
        caption = QLabel(title)
        caption.setObjectName("TinyMuted")
        frame.addWidget(caption)
        field = QLabel(value)
        field.setObjectName("Value")
        field.setWordWrap(True)
        frame.addWidget(field)
        index = layout.count()
        layout.addWidget(tile, index // 2, index % 2)
        return field

    def _set_facts(self, rows):
        while self.facts.count():
            item = self.facts.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        entries = [(label, value) for label, value in rows if value]
        for index, (label, value) in enumerate(entries):
            tile = QWidget(self)
            box = QVBoxLayout(tile)
            box.setContentsMargins(0, 2, 0, 2)
            box.setSpacing(3)
            label_widget = QLabel(label)
            label_widget.setObjectName("TinyMuted")
            box.addWidget(label_widget)
            value_widget = QLabel(str(value))
            value_widget.setObjectName("Value")
            value_widget.setWordWrap(True)
            value_widget.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            box.addWidget(value_widget)
            self.facts.addWidget(tile, index // 3, index % 3)

    def refresh(self):
        if not self.refresh_btn.isEnabled():
            return
        self._generation += 1
        generation = self._generation
        self.refresh_btn.setEnabled(False)
        self.status.set_status("Обновление", "warning")
        # Preserve the last successful reading during refresh. Otherwise an
        # intermittent API/RTSP failure makes the whole card appear empty.
        if self._last_report is None:
            self.info.setText("Получаю данные камеры…")
            self.image.setText("Загружаю превью…")
            self.image.setPixmap(QPixmap())
            self.rate.setText("Измеряю…")
            self.last.setText("Проверяю…")
            self.connection.setText("Проверяю…")
            self.resolution.setText("Проверяю…")
            self.fps.setText("Проверяю…")
            self.video_codec.setText("Проверяю…")
            self.audio_codec.setText("Проверяю…")
            self.diagnostic_text.setText("Запросы выполняются в фоне…")

        def worker():
            try:
                report = self.inspector(
                    B2OService(self.settings), self.choice.camera_id,
                    self.choice.operator_id, with_network=False,
                    with_preview=False,
                )
                self.detailsReady.emit((generation, report), "")
            except CameraLookupError as exc:
                self.detailsReady.emit((generation, None), str(exc))
            except Exception:
                self.detailsReady.emit(
                    (generation, None),
                    "Не удалось получить карточку камеры. Проверьте права и ID.",
                )
        threading.Thread(target=worker, name="camera-card-metadata",
                         daemon=True).start()

    def _on_details(self, packet, error):
        generation, report = packet
        if generation != self._generation:
            return
        if error or report is None:
            if self._last_report is not None:
                self.status.set_status("Обновление не удалось", "warning")
                self.diagnostic_text.setText(
                    "Повторный запрос админки не удался. Показаны данные "
                    "предыдущей успешной проверки; они могут быть устаревшими."
                )
            else:
                self.status.set_status("Ошибка", "danger")
                self.info.setText(error)
                self.info.show()
                self.image.setText("Превью недоступно")
                self.rate.setText("Нет данных")
                self.last.setText("Нет данных")
                self.connection.setText("Нет данных")
                self.diagnostic_text.setText("Диагностика невозможна без карточки камеры.")
            self.refresh_btn.setEnabled(True)
            return
        self._last_report = report
        if str(report.status or "").casefold() in ("error", "offline", "failed"):
            self.status.set_status("B2O: " + report.status, "warning")
        else:
            self.status.set_status("Карточка загружена", "success")
        title = str(self.choice.label or "").strip()
        self.heading.setText(
            report.name or (title if title and not title.lower().startswith(
                ("linkvideo_", "linkvideoby_", "linkvideokz_")
            ) else report.camera_id)
        )
        lines = [
            ("ID", report.camera_id),
            ("Страна", report.operator),
            ("Логин клиента", self.choice.customer_login),
            ("Название", report.name),
            ("Статус B2O", report.status),
            ("Серийный номер", report.serial),
            ("Модель", report.model),
            ("Сервер", report.server),
            ("IP камеры", report.ip),
            ("RTSP порт", str(report.rtsp_port) if report.rtsp_port else ""),
            ("Разрешение B2O", str(report.metadata.get("nimble_main_resolution") or "")),
            ("Видеокодек B2O", str(report.metadata.get("nimble_main_video_codec") or "")),
            ("Аудиокодек B2O", str(report.metadata.get("nimble_main_audio_codec") or "")),
            ("Битрейт B2O (справочно)", (
                f'{float(report.metadata["nimble_main_bandwidth"]) / 1_000_000:.2f} Мбит/с'
                if str(report.metadata.get("nimble_main_bandwidth") or "").isdigit()
                else ""
            )),
            ("Глубина архива", report.archive_depth),
            ("Тариф", self.choice.tariff),
        ]
        self.info.setText("\n".join(
            f"{label}: {value}" for label, value in lines if value
        ))
        self.info.hide()
        self._set_facts(lines)
        self.refresh_btn.setEnabled(True)

        def preview_worker():
            try:
                b2o = B2OService(self.settings)
                number, _ = b2o.normalize_camera_id(
                    report.camera_id, self.choice.operator_id
                )
                blob = self.preview_loader(report.operator, number, b2o.token())
                self.previewReady.emit((generation, blob), "")
            except Exception:
                self.previewReady.emit(
                    (generation, None), "Не удалось получить последнее изображение."
                )

        def telemetry_worker():
            try:
                b2o = B2OService(self.settings)
                stream = fetch_camera_stream(
                    b2o, report.camera_id, self.choice.operator_id
                )
                sample = report
                if stream is not None:
                    sample = replace(
                        report, rtsp_url=stream.url,
                        rtsp_host=stream.host, rtsp_port=stream.port,
                        port_source="camera-streams-urls",
                    )
                    # The installer does not bundle FFmpeg. Reuse the same
                    # verified LocalAppData download as Archive, only when a
                    # real authorized RTSP endpoint exists. Keep Qt responsive
                    # and serialize first-use setup across multiple cameras.
                    if not _cached_ffmpeg():
                        self.telemetryProgress.emit(
                            generation, "Подготавливаю FFmpeg для RTSP-замера…"
                        )
                        with _FFMPEG_SETUP_LOCK:
                            if not _cached_ffmpeg():
                                from linkvideo_vpn_helper.services.archive_service_core import ArchiveService
                                try:
                                    ArchiveService(self.settings).ensure_ffmpeg(
                                        progress=lambda stage, detail:
                                            self.telemetryProgress.emit(
                                                generation,
                                                f"{stage}: {detail}"[:180],
                                            )
                                    )
                                except Exception:
                                    self.telemetryProgress.emit(
                                        generation, "FFmpeg недоступен — проверю доступный архив."
                                    )
                data = self.live_probe(sample)
                self.telemetryReady.emit((generation, data))
            except Exception:
                self.telemetryReady.emit((generation, None))

        threading.Thread(target=preview_worker, name="camera-card-preview",
                         daemon=True).start()
        threading.Thread(target=telemetry_worker, name="camera-card-telemetry",
                         daemon=True).start()

    def _on_preview(self, packet, error):
        generation, blob = packet
        if generation != self._generation:
            return
        if not blob:
            self.image.setText(error or "Последнее изображение недоступно")
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(blob):
            self.image.setText("Сервер не вернул изображение.")
            return
        self._preview_bytes = blob
        bounds = self.image.size()
        self.image.setPixmap(
            pixmap.scaled(
                max(250, bounds.width() - 14),
                max(190, bounds.height() - 14),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _on_progress(self, generation, message):
        if generation == self._generation:
            self.diagnostic_text.setText(message)

    def _on_telemetry(self, packet):
        generation, data = packet
        if generation != self._generation:
            return
        if data is None:
            if self._last_telemetry is not None:
                self.diagnostic_text.setText(
                    "Повторная сетевая проверка не удалась. Показан прежний "
                    "замер, он не отражает текущее состояние видеопотока."
                )
            else:
                self.rate.setText("Не измерен")
                self.last.setText("Нет подтверждённой даты")
                self.connection.setText("Недоступно")
                self.resolution.setText("Нет данных")
                self.fps.setText("Не измерен")
                self.video_codec.setText("Нет данных")
                self.audio_codec.setText("Нет данных")
                self.diagnostic_text.setText("Не удалось выполнить сетевую диагностику.")
            return
        self._last_telemetry = data
        self.resolution.setText(data.resolution or "Не определено")
        self.fps.setText(data.fps or "Не измерен")
        self.video_codec.setText(data.video_codec or "Не определён")
        self.audio_codec.setText(data.audio_codec or "Не определён")
        self.rate.setText(data.bitrate_mbps)
        self.last.setText(data.last_stream)
        self.connection.setText(
            "RTSP-поток получен"
            if data.source == "RTSP"
            else ("RTSP порт доступен" if "TCP:" in data.connectivity and
                  "— открыт" in data.connectivity
                  else ("Медиасервер: HTTPS доступен"
                        if "TCP:443 — открыт" in data.connectivity
                        else "Нет подтверждения RTSP-потока"))
        )
        self.diagnostic_text.setText(data.connectivity + "\n\n" + data.detail)
