from __future__ import annotations

"""Final VPN Servers surface for the centralized LinkVideo.Cloud architecture.

RouterOS LV Scheduler controls are legacy. The desktop page is now an
infrastructure overview plus explicit backup actions; lifecycle ownership stays
outside this page.
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton

from linkvideo_vpn_helper.services.errors import classify_exception
from linkvideo_vpn_helper.ui.components import Card, MetricCard, StatusPill


_INSTALLED = False


def _parent_card(widget):
    current = widget
    while current is not None:
        if isinstance(current, Card):
            return current
        current = current.parentWidget()
    return None


def install_vpn_servers_centralized_ui() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.pages.vpn_servers_page import VPNServersPage

    previous_build = VPNServersPage._build

    def build(self):
        previous_build(self)

        # Local LV Scheduler management is intentionally absent from the final
        # desktop surface. Keeping disabled/blocked buttons only creates a false
        # "update required" workflow and leads operators into an intentional
        # central-retention guard error.
        for name in (
            "m_installed",
            "m_quarantine",
            "install_all_btn",
            "start_all_btn",
            "stop_all_btn",
            "policy",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.hide()

        table = getattr(self, "table", None)
        if table is not None and table.columnCount() > 7:
            table.setColumnHidden(7, True)

        # The maintenance card now contains one real action, so let the backup
        # button use the whole row instead of leaving an empty second column.
        backup = getattr(self, "backup_btn", None)
        if backup is not None:
            backup.setText("Резервная копия серверов")
            card = _parent_card(backup)
            outer = card.layout() if card is not None else None
            if outer is not None:
                for index in range(outer.count()):
                    layout = outer.itemAt(index).layout()
                    if isinstance(layout, QGridLayout) and layout.indexOf(backup) >= 0:
                        layout.removeWidget(backup)
                        layout.addWidget(backup, 0, 0, 1, 2)
                        break

        for label in self.findChildren(QLabel):
            text = str(label.text() or "")
            if "управление автономной LV-автоматикой" in text:
                label.setText("Нагрузка и состояние VPN-серверов.")

    def refresh(self, silent: bool = False):
        if self._busy:
            return
        servers = self.registry.hosts()
        if not servers:
            self.task.show()
            self.task.warning("Нет VPN-серверов", "Включите серверы в настройках.")
            return

        self._set_busy(True)
        self._refresh_silent_mode = bool(silent)
        if not silent:
            self.task.show()
            self.task.busy("Проверяю VPN-серверы", f"Серверов: {len(servers)}")

        def worker():
            rows = []
            with ThreadPoolExecutor(max_workers=min(8, len(servers)), thread_name_prefix="vpn-dashboard") as pool:
                futures = {
                    pool.submit(self.service.analyze_server_quick, host, self.credentials): host
                    for host in servers
                }
                for future in as_completed(futures):
                    host = futures[future]
                    try:
                        stat = future.result()
                        # Base renderer accepts the historical 4-tuple. The LV
                        # slot stays None and its column is hidden.
                        rows.append((host, stat, None, None))
                    except Exception as exc:
                        rows.append((host, None, None, classify_exception(exc).message))
            self.statsReady.emit(rows)

        threading.Thread(target=worker, daemon=True, name="vpn-dashboard-central").start()

    def render_server_detail(self, host: str):
        self._clear_detail()
        card = getattr(self, "detail_card", None)
        host = str(host or "").strip()
        if card is not None:
            card.setVisible(bool(host))
        if not host:
            return

        stat = self._stats_by_host.get(host)

        top = QHBoxLayout()
        name = QLabel(host)
        name.setObjectName("SectionTitle")
        top.addWidget(name)
        top.addStretch(1)
        if stat is not None:
            state, kind = self._state(int(stat.clients_online))
            top.addWidget(StatusPill(state, kind))
        self.detail_layout.addLayout(top)

        if stat is None:
            note = QLabel("Не удалось получить актуальное состояние сервера.")
            note.setObjectName("Muted")
            self.detail_layout.addWidget(note)
            return

        metrics = QHBoxLayout()
        metrics.setSpacing(8)
        values = (
            ("Активные L2TP", f"{int(stat.clients_online)} / 500"),
            ("Учётки", str(stat.clients_total)),
            ("CPU", "—" if stat.cpu_load is None else f"{stat.cpu_load}%"),
            ("RAM", "—" if stat.memory_usage_percent is None else f"{stat.memory_usage_percent}%"),
            ("NAT", str(stat.ports_total)),
        )
        for label, value in values:
            metrics.addWidget(MetricCard(label, value), 1)
        self.detail_layout.addLayout(metrics)

        actions = QHBoxLayout()
        actions.addStretch(1)
        backup = QPushButton("Резервная копия этого сервера")
        backup.clicked.connect(lambda _=False, h=host: self._backup_one(h))
        actions.addWidget(backup)
        self.detail_layout.addLayout(actions)

    VPNServersPage._build = build
    VPNServersPage.refresh = refresh
    VPNServersPage._render_server_detail = render_server_detail
    _INSTALLED = True
