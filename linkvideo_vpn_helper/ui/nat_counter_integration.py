from __future__ import annotations

"""Present RouterOS NAT ports as compact operational rows.

The RouterOS ``bytes``/``packets`` values are cumulative counters for the NAT
rule. They are useful diagnostics, so keep them visible, but place them on a
second row instead of squeezing every value into one horizontal line.
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


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
        port_list.setMinimumHeight(185)
        port_list.setMaximumHeight(410)

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
                camera_id.setMinimumHeight(26)
                camera_id.setStyleSheet("font-size: 14px; font-weight: 700;")
                camera_row = QHBoxLayout()
                camera_row.setContentsMargins(0, 0, 0, 0)
                camera_row.setSpacing(8)
                camera_row.addWidget(camera_id, 0)
                copy_id = QPushButton("⧉")
                copy_id.setObjectName("B2OCopyCameraId")
                copy_id.setProperty("role", "soft")
                copy_id.setFixedSize(28, 26)
                copy_id.setToolTip("Скопировать ID камеры " + str(camera_link.camera_id))
                copy_id.setAccessibleName("Скопировать ID камеры B2O")
                def copy_camera_id(value, button):
                    QGuiApplication.clipboard().setText(value)
                    button.setText("✓")
                    button.setToolTip("ID камеры скопирован")
                copy_id.clicked.connect(
                    lambda _checked=False, value=str(camera_link.camera_id), button=copy_id:
                    copy_camera_id(value, button))
                camera_row.addWidget(copy_id)
                camera_row.addStretch(1)
                outer.addLayout(camera_row)

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

            item.setSizeHint(QSize(0, 99 if camera_link is not None else 76))
            port_list.setItemWidget(item, row)

        # _render_client() initially sized the plain-text list too short.
        # After custom rows are attached, reserve space for the visible ID
        # and the traffic line; keep scrolling for larger port inventories.
        required = sum(
            port_list.item(i).sizeHint().height() + port_list.spacing()
            for i in range(port_list.count())
        ) + 14
        port_list.setFixedHeight(min(410, max(185, required)))

    SearchManagePage._render_client = patched_render
    _INSTALLED = True
