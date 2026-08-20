from __future__ import annotations

"""Present RouterOS NAT ports as compact operational rows.

The RouterOS ``bytes``/``packets`` values are cumulative counters for the NAT
rule. They are useful diagnostics, so keep them visible, but place them on a
second row instead of squeezing every value into one horizontal line.
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget


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
        port_list.setMinimumHeight(min(380, max(185, len(client.ports) * 80 + 10)))
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
            row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
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

            item.setSizeHint(QSize(0, 76))
            port_list.setItemWidget(item, row)

    SearchManagePage._render_client = patched_render
    _INSTALLED = True
