from __future__ import annotations

"""Present RouterOS NAT ports as compact operational rows.

The RouterOS ``bytes``/``packets`` values are cumulative counters for the NAT
rule. They are useful diagnostics, so keep them visible, but place them on a
second row instead of squeezing every value into one horizontal line.
"""

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget


_INSTALLED = False


def _fmt_bytes(value: int) -> str:
    number = float(max(0, int(value or 0)))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if number < 1024.0 or unit == units[-1]:
            return f"{int(number)} {unit}" if unit == "B" else f"{number:.1f} {unit}"
        number /= 1024.0
    return f"{int(number)} B"


def _fmt_packets(value: int) -> str:
    try:
        number = max(0, int(value or 0))
    except Exception:
        number = 0
    return f"{number:,}".replace(",", " ")


def _counter(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("TinyMuted")
    label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    label.setMinimumWidth(0)
    return label


def install_nat_counter_ui() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # Google service-account auto-discovery is installed here as well because
    # this integration is part of the normal Helper startup chain before the
    # Sheets coordinator is constructed.
    from linkvideo_vpn_helper.services.google_key_discovery_compat import install_google_key_discovery
    install_google_key_discovery()
    from linkvideo_vpn_helper.ui.vpn_sheets_key_ui_compat import install_vpn_sheets_key_ui
    install_vpn_sheets_key_ui()

    from linkvideo_vpn_helper.ui.components import StatusPill
    from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage

    original_render = SearchManagePage._render_client

    def patched_render(self):
        original_render(self)
        client = getattr(self, "current", None)
        port_list = getattr(self, "port_list", None)
        if client is None or port_list is None or not client.ports:
            return

        bytes_by_port = dict(getattr(client, "port_nat_bytes", {}) or {})
        packets_by_port = dict(getattr(client, "port_nat_packets", {}) or {})
        disabled = {int(value) for value in (getattr(client, "disabled_ports", []) or [])}
        conflicts = dict(getattr(client, "port_conflicts", {}) or {})
        recent = {int(value) for value in (getattr(self, "_recent_new_ports", set()) or set())}

        port_list.setSpacing(6)
        report = getattr(self, "_camera_match_result", None)
        match_key = getattr(self, "_camera_match_key", None)
        current_key = (
            client.server, client.login, tuple(sorted(client.ports))
        )
        # Do not show stale IDs after changing client, port list or B2O login.
        if not match_key or match_key[:3] != current_key:
            report = None
        confirmed_by_port = {
            int(link.port): link for link in (report.links if report is not None else ())
        }
        grouped_by_port = {
            int(port): tuple(links) for port, links in (
                getattr(report, "grouped", ()) if report is not None else ())
        }
        client_key = (client.server, client.login)
        if getattr(self, "_nvr_expanded_client_key", None) != client_key:
            self._nvr_expanded_client_key = client_key
            self._nvr_expanded_ports = set()
        # The parent SearchClientInfoScroll already handles all vertical
        # scrolling. No second scrollbar should appear inside the port card.
        port_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        port_list.setMinimumHeight(118)

        for index in range(port_list.count()):
            item = port_list.item(index)
            raw_port = item.data(Qt.ItemDataRole.UserRole)
            try:
                port = int(raw_port)
            except Exception:
                continue

            conflict_rows = list(conflicts.get(port, []) or [])
            rule_bytes = int(bytes_by_port.get(port, 0) or 0)
            rule_packets = int(packets_by_port.get(port, 0) or 0)

            # QListWidget paints its own item text underneath setItemWidget().
            item.setText("")
            item.setToolTip("")
            item.setStatusTip("")
            item.setWhatsThis("")

            row = QWidget()
            row.setObjectName("NatPortRow")
            # Buttons inside the port row must be clickable. The row itself
            # ignores non-button mouse clicks so the list still handles selection.
            outer = QVBoxLayout(row)
            outer.setContentsMargins(14, 7, 14, 7)
            outer.setSpacing(3)

            top = QHBoxLayout()
            top.setContentsMargins(0, 0, 0, 0)
            top.setSpacing(8)
            number = QLabel(f"Порт {port}")
            number.setObjectName("Value")
            number.setMinimumHeight(22)
            top.addWidget(number)
            top.addStretch(1)
            top.addWidget(StatusPill(
                "Отключён" if port in disabled else "Включён",
                "neutral" if port in disabled else "success",
            ))
            if port in recent:
                top.addWidget(StatusPill("Новый", "success"))
            if conflict_rows:
                top.addWidget(StatusPill("Конфликт", "danger"))
            outer.addLayout(top)

            # The regular page writes the ID to QListWidgetItem.text(), but
            # this integration replaces that text with a custom QWidget.
            # Draw the verified ID *inside the visible port card* instead.
            camera_link = confirmed_by_port.get(port) if not conflict_rows else None
            if camera_link is not None:
                camera_id = QLabel("ID камеры B2O: " + str(camera_link.camera_id))
                camera_id.setObjectName("B2OPortCameraId")
                camera_id.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse)
                camera_id.setToolTip(
                    "Камера B2O: " + str(camera_link.label) +
                    " · подтверждено по логину аккаунта и RTSP-порту")
                camera_id.setMinimumHeight(22)
                camera_id.setStyleSheet("font-size: 14px; font-weight: 700;")
                camera_row = QHBoxLayout()
                camera_row.setContentsMargins(0, 0, 0, 0)
                camera_row.setSpacing(4)
                camera_row.addWidget(camera_id, 0)

                # Native QPushButton styles have a 40px min-height, so they
                # clip the glyph and look like a giant orange smudge. Draw a
                # 15px two-sheet copy pictogram into a transparent tool button.
                picture = QPixmap(16, 16)
                picture.fill(Qt.GlobalColor.transparent)
                painter = QPainter(picture)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                tone = camera_id.palette().color(camera_id.foregroundRole())
                painter.setPen(QPen(QColor(tone), 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(QRectF(5, 4, 9, 10), 1.4, 1.4)
                painter.drawLine(3, 11, 3, 3)
                painter.drawLine(3, 3, 11, 3)
                painter.end()
                copy_id = QToolButton(row)
                copy_id.setObjectName("B2OCopyCameraId")
                copy_id.setIcon(QIcon(picture))
                copy_id.setIconSize(QSize(16, 16))
                copy_id.setFixedSize(20, 20)
                copy_id.setStyleSheet(
                    "QToolButton#B2OCopyCameraId {"
                    "background: transparent; border: 0; padding: 0; margin: 0;"
                    "}"
                )
                copy_id.setCursor(Qt.CursorShape.PointingHandCursor)
                copy_id.setToolTip("Скопировать ID камеры " + str(camera_link.camera_id))
                copy_id.setAccessibleName("Скопировать ID камеры B2O")

                def copy_camera_id(value, button):
                    QGuiApplication.clipboard().setText(value)
                    button.setToolTip("ID камеры скопирован: " + value)

                copy_id.clicked.connect(
                    lambda _checked=False, value=str(camera_link.camera_id), button=copy_id:
                    copy_camera_id(value, button))
                camera_row.addWidget(copy_id, 0, Qt.AlignmentFlag.AlignVCenter)
                camera_row.addStretch(1)
                outer.addLayout(camera_row)

            camera_group = grouped_by_port.get(port) if not conflict_rows else None
            group_extra = 0
            if camera_group:
                # Several channels of one NVR share an RTSP endpoint. They
                # are verified by the SAME server + external port and shown
                # only on explicit operator request, never mistaken for NAT
                # collisions on this router.
                toggle = QToolButton(row)
                toggle.setObjectName("B2ONvrExpand")
                toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
                toggle.setAutoRaise(True)
                toggle.setCursor(Qt.CursorShape.PointingHandCursor)
                toggle.setStyleSheet(
                    "QToolButton#B2ONvrExpand {"
                    "background: transparent; border: 0; padding: 4px 0;"
                    "text-align: left; font-weight: 650;"
                    "}"
                )
                details = QWidget(row)
                details.setObjectName("B2ONvrCameraList")
                details_layout = QVBoxLayout(details)
                details_layout.setContentsMargins(10, 2, 0, 3)
                details_layout.setSpacing(5)

                camera_heights: list[int] = []
                for link in camera_group:
                    # A camera name and its ID are two separate lines. The
                    # previous one-line label clipped IDs in narrow cards.
                    entry = QWidget(details)
                    entry.setObjectName("B2ONvrCameraEntry")
                    entry_layout = QVBoxLayout(entry)
                    entry_layout.setContentsMargins(0, 3, 0, 4)
                    entry_layout.setSpacing(2)

                    name = QLabel(str(link.label))
                    name.setObjectName("B2ONvrCameraName")
                    name.setTextFormat(Qt.TextFormat.PlainText)
                    name.setWordWrap(True)
                    name.setMinimumWidth(0)
                    name.setTextInteractionFlags(
                        Qt.TextInteractionFlag.TextSelectableByMouse)
                    name.setToolTip(str(link.label))
                    entry_layout.addWidget(name)

                    id_row = QHBoxLayout()
                    id_row.setContentsMargins(0, 0, 0, 0)
                    id_row.setSpacing(5)
                    camera_id = QLabel(f"ID камеры B2O: {link.camera_id}")
                    camera_id.setObjectName("B2ONvrCameraId")
                    camera_id.setTextFormat(Qt.TextFormat.PlainText)
                    camera_id.setStyleSheet("font-weight: 700;")
                    camera_id.setTextInteractionFlags(
                        Qt.TextInteractionFlag.TextSelectableByMouse)
                    camera_id.setMinimumWidth(0)
                    camera_id.setToolTip(
                        f"Подтверждено: {client.server}:{port} · ID {link.camera_id}")
                    id_row.addWidget(camera_id, 0)
                    copy = QToolButton(entry)
                    copy.setObjectName("B2OCopyNvrCameraId")
                    copy.setText("⧉")
                    copy.setFixedSize(22, 22)
                    copy.setCursor(Qt.CursorShape.PointingHandCursor)
                    copy.setStyleSheet(
                        "QToolButton#B2OCopyNvrCameraId {"
                        "background: transparent; border: none; "
                        "font-size: 16px; padding: 0px; }"
                    )
                    copy.setToolTip("Скопировать ID камеры " + str(link.camera_id))
                    copy.clicked.connect(
                        lambda _checked=False, value=str(link.camera_id):
                        QGuiApplication.clipboard().setText(value))
                    id_row.addWidget(copy, 0)
                    id_row.addStretch(1)
                    entry_layout.addLayout(id_row)
                    details_layout.addWidget(entry)

                    # Calculate height using the REAL available port-card width.
                    # Wrapping may require 2+ lines for customer camera names.
                    text_width = max(145, port_list.viewport().width() - 100)
                    metrics = QFontMetrics(name.font())
                    name_height = metrics.boundingRect(
                        QRect(0, 0, text_width, 3000),
                        int(Qt.TextFlag.TextWordWrap), name.text()
                    ).height()
                    camera_heights.append(max(18, name_height) + 34)

                outer.addWidget(toggle)
                outer.addWidget(details)
                expanded = port in self._nvr_expanded_ports
                details.setVisible(expanded)
                group_detail_height = (sum(camera_heights) + 5 * max(0, len(camera_heights) - 1) + 8)
                group_extra = 30 + (group_detail_height if expanded else 0)

                def toggle_nvr(_checked=False, *,
                               target=port, panel=details, button=toggle,
                               list_item=item, list_widget=port_list,
                               total=len(camera_group), content_height=group_detail_height):
                    expanded_now = panel.isHidden()
                    panel.setVisible(expanded_now)
                    if expanded_now:
                        self._nvr_expanded_ports.add(target)
                    else:
                        self._nvr_expanded_ports.discard(target)
                    button.setText(
                        ("▾ Свернуть список камер" if expanded_now
                         else f"▸ Раскрыть список найденных камер ({total})"))
                    list_item.setSizeHint(QSize(
                        0, 76 + 30 + (content_height if expanded_now else 0)))
                    list_widget.fit_to_contents()
                toggle.clicked.connect(toggle_nvr)
                toggle.setText(
                    "▾ Свернуть список камер" if expanded else
                    f"▸ Раскрыть список найденных камер ({len(camera_group)})")

            detail_parts = ["TCP · dst-nat"]
            if conflict_rows:
                owners: list[str] = []
                for conflict in conflict_rows:
                    try:
                        owner = str(conflict.owner_text() or "").strip()
                    except Exception:
                        owner = ""
                    if owner and owner not in owners:
                        owners.append(owner)
                if owners:
                    detail_parts.append("также: " + ", ".join(owners[:2]))

            bottom = QHBoxLayout()
            bottom.setContentsMargins(0, 0, 0, 0)
            bottom.setSpacing(12)
            detail = QLabel(" · ".join(detail_parts))
            detail.setObjectName("TinyMuted")
            detail.setMinimumWidth(0)
            bottom.addWidget(detail, 1)
            bottom.addWidget(_counter(f"NAT-трафик: {_fmt_bytes(rule_bytes)}"), 0)
            bottom.addWidget(_counter(f"Пакеты: {_fmt_packets(rule_packets)}"), 0)
            outer.addLayout(bottom)

            item.setSizeHint(QSize(
                0, (90 if camera_link is not None else 76) + group_extra))
            port_list.setItemWidget(item, row)

        # Grow to the complete height of all rows; the parent left-panel
        # QScrollArea remains the ONLY scrollbar even for many ports.
        port_list.fit_to_contents()

    SearchManagePage._render_client = patched_render
    _INSTALLED = True
