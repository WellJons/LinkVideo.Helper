from __future__ import annotations

"""Present RouterOS NAT ports as compact operational rows.

Per-port traffic counters are intentionally not shown in the client card. On the
RouterOS fleet they are cumulative firewall-rule counters rather than a reliable
live status, and they make the compact port rows unnecessarily crowded.
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget


_INSTALLED = False


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

        disabled = {int(value) for value in (getattr(client, "disabled_ports", []) or [])}
        conflicts = dict(getattr(client, "port_conflicts", {}) or {})
        recent = {int(value) for value in (getattr(self, "_recent_new_ports", set()) or set())}

        port_list.setSpacing(6)
        port_list.setMinimumHeight(min(320, max(160, len(client.ports) * 62 + 10)))
        port_list.setMaximumHeight(350)

        for index in range(port_list.count()):
            item = port_list.item(index)
            raw_port = item.data(Qt.ItemDataRole.UserRole)
            try:
                port = int(raw_port)
            except Exception:
                continue

            conflict_rows = list(conflicts.get(port, []) or [])

            # QListWidget paints its own item text underneath setItemWidget().
            # Keep only UserRole as the data source so the old "Порт ..." row
            # does not bleed through the custom card.
            item.setText("")
            item.setToolTip("")
            item.setStatusTip("")
            item.setWhatsThis("")

            row = QWidget()
            row.setObjectName("NatPortRow")
            # The QListWidget remains responsible for selection; labels/pills are
            # presentation only and must not swallow the click.
            row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(14, 7, 14, 7)
            layout.setSpacing(10)

            identity = QVBoxLayout()
            identity.setContentsMargins(0, 0, 0, 0)
            identity.setSpacing(2)
            number = QLabel(f"Порт {port}")
            number.setObjectName("Value")
            number.setMinimumHeight(21)
            identity.addWidget(number)

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
            detail = QLabel(" · ".join(detail_parts))
            detail.setObjectName("TinyMuted")
            detail.setMinimumHeight(17)
            detail.setWordWrap(False)
            identity.addWidget(detail)
            layout.addLayout(identity, 1)

            state = StatusPill(
                "Отключён" if port in disabled else "Включён",
                "neutral" if port in disabled else "success",
            )
            layout.addWidget(state, 0, Qt.AlignmentFlag.AlignVCenter)
            if port in recent:
                layout.addWidget(StatusPill("Новый", "success"), 0, Qt.AlignmentFlag.AlignVCenter)
            if conflict_rows:
                layout.addWidget(StatusPill("Конфликт", "danger"), 0, Qt.AlignmentFlag.AlignVCenter)

            item.setSizeHint(QSize(0, 58))
            port_list.setItemWidget(item, row)

    SearchManagePage._render_client = patched_render
    _INSTALLED = True
