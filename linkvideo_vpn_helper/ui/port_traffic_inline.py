from __future__ import annotations

"""Inline live traffic for NAT port rows.

This extension keeps the existing 42 px port row height. The port identity stays
on the left and a compact live status occupies the otherwise unused space on the
right. Conntrack is polled only while one client card is visible; multi-VPN
search remains lightweight.
"""

import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from linkvideo_vpn_helper.services.port_traffic_service import PortTrafficService


_INSTALLED = False


class _TrafficBridge(QObject):
    ready = Signal(object)


def _polish(label: QLabel, object_name: str) -> None:
    if label.objectName() == object_name:
        return
    label.setObjectName(object_name)
    label.style().unpolish(label)
    label.style().polish(label)


def _fmt_bps(value: float | int) -> str:
    bps = max(0.0, float(value or 0))
    if bps >= 1_000_000_000:
        return f"{bps / 1_000_000_000:.2f} Гбит/с"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.2f} Мбит/с"
    if bps >= 1_000:
        return f"{bps / 1_000:.0f} Кбит/с"
    return f"{bps:.0f} бит/с"


def _client_key(page) -> tuple[str, str]:
    current = getattr(page, "current", None)
    if current is None:
        return "", ""
    return str(current.server), str(current.login)


def _reset_for_client(page) -> None:
    page._port_traffic_generation = int(getattr(page, "_port_traffic_generation", 0)) + 1
    page._port_traffic_busy = False
    page._port_traffic_previous = {}
    page._port_traffic_previous_key = _client_key(page)


def _decorate_port_rows(page) -> None:
    current = getattr(page, "current", None)
    port_list = getattr(page, "port_list", None)
    if current is None or port_list is None:
        return

    old_key = getattr(page, "_port_traffic_previous_key", ("", ""))
    if old_key != _client_key(page):
        _reset_for_client(page)

    page._port_traffic_labels = {}
    conflicts = current.port_conflicts or {}
    recent = set(int(x) for x in getattr(page, "_recent_new_ports", set()) or set())

    for index in range(port_list.count()):
        item = port_list.item(index)
        value = item.data(Qt.ItemDataRole.UserRole)
        if value is None:
            continue
        try:
            port = int(value)
        except Exception:
            continue

        row = QWidget(port_list)
        row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(8)

        has_conflict = bool(conflicts.get(port))
        if has_conflict:
            left_text = f"⚠ Порт {port}  ·  конфликт"
            left_object = "WarningText"
        elif port in recent:
            left_text = f"Порт {port}  ·  новый"
            left_object = "Value"
        else:
            left_text = f"Порт {port}"
            left_object = "Value"

        left = QLabel(left_text)
        left.setObjectName(left_object)
        left.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        traffic = QLabel("… проверка")
        traffic.setObjectName("TinyMuted")
        traffic.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        traffic.setMinimumWidth(210)
        traffic.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout.addWidget(left)
        layout.addStretch(1)
        layout.addWidget(traffic)
        port_list.setItemWidget(item, row)
        page._port_traffic_labels[port] = traffic

        if has_conflict:
            owners = []
            for conflict in conflicts.get(port, []):
                try:
                    owner = conflict.owner_text()
                except Exception:
                    owner = "другое NAT-правило"
                if owner not in owners:
                    owners.append(owner)
            item.setToolTip(
                "Конфликт NAT: этот внешний TCP-порт встречается в другой учётке на том же сервере"
                + (("\nТакже: " + ", ".join(owners)) if owners else "")
            )

    _show_waiting_state(page)


def _show_waiting_state(page) -> None:
    current = getattr(page, "current", None)
    labels = getattr(page, "_port_traffic_labels", {})
    if current is None:
        return
    disabled = {int(x) for x in current.disabled_ports or []}
    for port, label in labels.items():
        if port in disabled:
            label.setText("○ отключён")
            label.setToolTip("NAT-правило этого порта отключено")
            _polish(label, "Muted")
        elif not current.is_online:
            label.setText("○ VPN не в сети")
            label.setToolTip("PPP/L2TP-сессия клиента сейчас не активна")
            _polish(label, "Muted")
        else:
            label.setText("… проверка")
            label.setToolTip("Проверяю connection tracking RouterOS")
            _polish(label, "TinyMuted")


def _request_port_traffic(page, force: bool = False) -> None:
    current = getattr(page, "current", None)
    if current is None or not getattr(current, "ports", None):
        return
    panel = getattr(page, "detail_panel", None)
    if panel is not None and not panel.isVisible() and not force:
        return

    if not current.is_online:
        _show_waiting_state(page)
        return
    if getattr(page, "_port_traffic_busy", False):
        return

    server, login = _client_key(page)
    remote = str(current.remote_address or "")
    ports = [int(p) for p in current.ports]
    generation = int(getattr(page, "_port_traffic_generation", 0)) + 1
    page._port_traffic_generation = generation
    page._port_traffic_busy = True

    def worker() -> None:
        payload = {
            "generation": generation,
            "server": server,
            "login": login,
            "sampled_at": time.monotonic(),
            "samples": None,
            "error": None,
        }
        try:
            payload["samples"] = page._port_traffic_service.sample_client(
                server,
                page.credentials,
                login,
                remote,
                ports,
            )
        except Exception as exc:
            payload["error"] = str(exc) or exc.__class__.__name__
        try:
            page._port_traffic_bridge.ready.emit(payload)
        except RuntimeError:
            pass

    threading.Thread(
        target=worker,
        daemon=True,
        name=f"lv-port-traffic:{server}:{login}",
    ).start()


def _apply_samples(page, payload: dict) -> None:
    if not isinstance(payload, dict):
        return
    current = getattr(page, "current", None)
    if current is None:
        return
    if (payload.get("server"), payload.get("login")) != _client_key(page):
        return
    if int(payload.get("generation", -1)) != int(getattr(page, "_port_traffic_generation", -2)):
        return

    page._port_traffic_busy = False
    labels = getattr(page, "_port_traffic_labels", {})
    disabled = {int(x) for x in current.disabled_ports or []}
    now = float(payload.get("sampled_at") or time.monotonic())
    error = payload.get("error")

    if error:
        for port, label in labels.items():
            if port in disabled:
                continue
            label.setText("! нет данных")
            label.setToolTip(str(error))
            _polish(label, "WarningText")
        return

    samples = payload.get("samples") or {}
    previous = getattr(page, "_port_traffic_previous", {})
    next_previous = {}

    for port, label in labels.items():
        if port in disabled:
            label.setText("○ отключён")
            label.setToolTip("NAT-правило этого порта отключено")
            _polish(label, "Muted")
            continue

        sample = samples.get(port)
        if sample is None:
            label.setText("○ нет данных")
            label.setToolTip("RouterOS не вернул состояние порта")
            _polish(label, "Muted")
            continue

        if sample.connections <= 0:
            label.setText("○ нет соединения")
            internal = f" → {sample.internal_port}" if sample.internal_port else ""
            label.setToolTip(f"TCP dst-nat {port}{internal}: активных connection tracking записей нет")
            _polish(label, "Muted")
            next_previous[port] = (sample.total_bytes, now)
            continue

        rate = float(sample.total_rate_bps)
        old = previous.get(port)
        if old:
            old_bytes, old_time = old
            dt = max(0.20, now - float(old_time))
            # Use byte delta as a compatibility fallback and as a sanity check
            # when a RouterOS build exposes rate fields but keeps them at zero.
            if sample.total_bytes >= int(old_bytes):
                delta_rate = (sample.total_bytes - int(old_bytes)) * 8.0 / dt
                if rate <= 0 and delta_rate > 0:
                    rate = delta_rate
        next_previous[port] = (sample.total_bytes, now)

        internal = f" → {sample.internal_port}" if sample.internal_port else ""
        details = [
            f"TCP dst-nat {port}{internal}",
            f"Соединений: {sample.connections}",
            f"С ответом: {sample.seen_reply}",
            f"К клиенту: {_fmt_bps(sample.orig_rate_bps)}",
            f"От клиента: {_fmt_bps(sample.repl_rate_bps)}",
        ]

        if rate > 0:
            label.setText(f"● {_fmt_bps(rate)}")
            label.setToolTip("\n".join(details))
            _polish(label, "SuccessText")
        elif old is None and not sample.rate_supported:
            label.setText(f"● {sample.connections} соед. · замер…")
            label.setToolTip("\n".join(details) + "\nТекущая скорость будет рассчитана по изменению byte counters на следующем опросе.")
            _polish(label, "TinyMuted")
        else:
            label.setText("● соединение · без трафика")
            label.setToolTip("\n".join(details))
            _polish(label, "Muted")

    page._port_traffic_previous = next_previous


def install_inline_port_traffic() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage

    original_init = SearchManagePage.__init__
    original_render = SearchManagePage._render_client
    original_close = SearchManagePage._close_client_view

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._port_traffic_service = PortTrafficService()
        self._port_traffic_bridge = _TrafficBridge(self)
        self._port_traffic_bridge.ready.connect(lambda payload: _apply_samples(self, payload))
        self._port_traffic_timer = QTimer(self)
        self._port_traffic_timer.setInterval(2200)
        self._port_traffic_timer.timeout.connect(lambda: _request_port_traffic(self))
        self._port_traffic_busy = False
        self._port_traffic_generation = 0
        self._port_traffic_previous = {}
        self._port_traffic_previous_key = ("", "")
        self._port_traffic_labels = {}

    def patched_render(self):
        original_render(self)
        _decorate_port_rows(self)
        if getattr(self, "current", None) is not None:
            if not self._port_traffic_timer.isActive():
                self._port_traffic_timer.start()
            QTimer.singleShot(50, lambda: _request_port_traffic(self, force=True))

    def patched_close(self, checked=False, immediate: bool = False):
        timer = getattr(self, "_port_traffic_timer", None)
        if timer is not None:
            timer.stop()
        self._port_traffic_generation = int(getattr(self, "_port_traffic_generation", 0)) + 1
        self._port_traffic_busy = False
        self._port_traffic_previous = {}
        self._port_traffic_labels = {}
        return original_close(self, checked, immediate)

    SearchManagePage.__init__ = patched_init
    SearchManagePage._render_client = patched_render
    SearchManagePage._close_client_view = patched_close
    _INSTALLED = True
