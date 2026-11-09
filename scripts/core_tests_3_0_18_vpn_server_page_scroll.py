from __future__ import annotations

"""VPN server summary: no nested scrollbar, wheel scrolls outer page."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QScrollArea, QTableWidgetItem, QWidget,
)
from linkvideo_vpn_helper.ui.pages.vpn_servers_page import (
    ServerTableWidget, VPNServersPage,
)

app = QApplication.instance() or QApplication([])
outer = QScrollArea()
outer.resize(980, 420)
content = QWidget()
content.setMinimumSize(900, 2600)
outer.setWidget(content)
outer.setWidgetResizable(False)
outer.show()
app.processEvents()

table = ServerTableWidget(0, 8)
table.outer_scroll = outer
table.setHorizontalHeaderLabels(
    ["VPN-сервер", "Страна", "Активные", "Учётки",
     "CPU", "RAM", "Порты", "LV"]
)
table.verticalHeader().setVisible(False)
table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
table.setRowCount(12)
for i in range(12):
    table.setItem(i, 0, QTableWidgetItem(f"vpn{i+1:02d}.linkvideo.ru"))
VPNServersPage._resize_servers_table(SimpleNamespace(table=table))
table.show()
app.processEvents()
row_pixels = sum(table.rowHeight(i) for i in range(table.rowCount()))
assert table.height() >= row_pixels + table.horizontalHeader().height()
assert table.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
bar = outer.verticalScrollBar()
assert bar.maximum() > 0
assert bar.value() == 0

pos = QPointF(25.0, 25.0)
event = QWheelEvent(
    pos, QPointF(30.0, 30.0), QPoint(0, 0), QPoint(0, -120),
    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    Qt.ScrollPhase.ScrollUpdate, False
)
table.wheelEvent(event)
assert event.isAccepted()
assert bar.value() > 0
assert table.verticalScrollBar().value() == 0

table.close()
outer.close()
print("CORE TESTS 3.0.18 VPN SERVER TABLE OUTER PAGE SCROLL OK")
