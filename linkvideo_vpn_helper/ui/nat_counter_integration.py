from __future__ import annotations

"""Display cumulative RouterOS NAT rule counters without claiming live status."""

from PySide6.QtCore import Qt


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


def install_nat_counter_ui() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage

    original_render = SearchManagePage._render_client

    def patched_render(self):
        original_render(self)
        client = getattr(self, "current", None)
        port_list = getattr(self, "port_list", None)
        if client is None or port_list is None:
            return

        bytes_by_port = dict(getattr(client, "port_nat_bytes", {}) or {})
        packets_by_port = dict(getattr(client, "port_nat_packets", {}) or {})
        if not client.ports:
            return

        for index in range(port_list.count()):
            item = port_list.item(index)
            raw_port = item.data(Qt.ItemDataRole.UserRole)
            try:
                port = int(raw_port)
            except Exception:
                continue
            rule_bytes = int(bytes_by_port.get(port, 0) or 0)
            rule_packets = int(packets_by_port.get(port, 0) or 0)
            suffix = f"NAT: {_fmt_bytes(rule_bytes)} · {_fmt_packets(rule_packets)} пак."
            current = str(item.text() or "").strip()
            if "NAT:" not in current:
                item.setText(f"{current}    ·    {suffix}")
            old_tip = str(item.toolTip() or "").strip()
            note = (
                "Накопительные счётчики RouterOS для NAT-правила: сколько байт и пакетов "
                "правило сопоставило с момента создания/сброса счётчика. Это не текущая скорость и не признак активного соединения."
            )
            item.setToolTip((old_tip + "\n" + note).strip())
            item.setSizeHint(item.sizeHint().expandedTo(item.sizeHint()))

    SearchManagePage._render_client = patched_render
    _INSTALLED = True
