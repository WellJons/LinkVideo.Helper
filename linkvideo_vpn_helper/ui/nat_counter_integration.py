from __future__ import annotations

"""Present RouterOS NAT ports as compact operational rows.

The counters shown here are cumulative firewall-rule statistics from RouterOS.
They are useful evidence that a NAT rule has actually matched traffic, but they
are deliberately not described as a live connection / current port status.
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget


_INSTALLED = False


def _fmt_bytes(value: int) -> str:
    number = float(max(0, int(value or 0)))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if number < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(number)} {unit}"
            return f"{number:.1f} {unit}"
        number /= 1024.0
    return f"{int(number)} B"


def _fmt_packets(value: int) -> str:
    try:
        number = max(0, int(value or 0))
    except Exception:
        number = 0
    return f"{number:,}".replace(",", " ")


def _metric(title: str, value: str) -> QWidget:
    host = QWidget()
    host.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(1)
    caption = QLabel(title)
    caption.setObjectName("TinyMuted")
    data = QLabel(value)
    data.setObjectName("Value")
    data.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    layout.addWidget(caption, 0, Qt.AlignmentFlag.AlignRight)
    layout.addWidget(data, 0, Qt.AlignmentFlag.AlignRight)
    return host


def install_nat_counter_ui() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # Google service-account auto-discovery is installed here as well because
    # this integration is part of the normal Helper startup chain before the
    # Sheets coordinator is constructed.
    from linkvideo_vpn_helper.services.google_key_discovery_compat import install_google_key_discovery
    install_google_key_discovery()

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
        port_list.setMinimumHeight(min(330, max(170, len(client.ports) * 70 + 8)))
        port_list.setMaximumHeight(360)

        for index in range(port_list.count()):
            item = port_list.item(index)
            raw_port = item.data(Qt.ItemDataRole.UserRole)
            try:
                port = int(raw_port)
            except Exception:
                continue

            rule_bytes = int(bytes_by_port.get(port, 0) or 0)
            rule_packets = int(packets_by_port.get(port, 0) or 0)
            conflict_rows = list(conflicts.get(port, []) or [])

            row = QWidget()
            row.setObjectName("NatPortRow")
            # The QListWidget remains responsible for selection; labels/pills are
            # presentation only and must not swallow the click.
            row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(12, 7, 12, 7)
            layout.setSpacing(12)

            identity = QVBoxLayout()
            identity.setContentsMargins(0, 0, 0, 0)
            identity.setSpacing(2)
            number = QLabel(f"Порт {port}")
            number.setObjectName("Value")
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

            layout.addWidget(_metric("NAT-трафик", _fmt_bytes(rule_bytes)), 0)
            layout.addWidget(_metric("Пакеты", _fmt_packets(rule_packets)), 0)

            note = (
                f"Порт {port}. Накопительные счётчики RouterOS NAT: "
                f"{_fmt_bytes(rule_bytes)}, {_fmt_packets(rule_packets)} пакетов. "
                "Это не текущая скорость и не доказательство активного соединения прямо сейчас."
            )
            if conflict_rows:
                note += " На сервере обнаружено другое NAT-правило с тем же внешним портом."
            item.setToolTip(note)
            item.setSizeHint(QSize(0, 66))
            port_list.setItemWidget(item, row)

    SearchManagePage._render_client = patched_render
    _INSTALLED = True
