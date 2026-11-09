from __future__ import annotations

"""Offline regression for the single scrollbar and honest DVR gap verdicts."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QListWidgetItem, QScrollArea, QVBoxLayout, QWidget

from linkvideo_vpn_helper.ui.pages.search_manage_page import ClientPortsList
from linkvideo_vpn_helper.services.archive_diagnosis_engine import (
    ArchiveDiagnosisEngine, DiagnosisSide,
)
from linkvideo_vpn_helper.services.archive_service_core import (
    ArchiveCamera, ArchiveDiscovery, ArchiveGap, ArchiveSlice,
)

app = QApplication.instance() or QApplication([])

outer = QScrollArea()
outer.setWidgetResizable(True)
outer.resize(480, 430)
host = QWidget()
layout = QVBoxLayout(host)
port_list = ClientPortsList()
port_list.outer_scroll = outer
for i in range(30):
    item = QListWidgetItem(f"VPN Port {10000+i}")
    item.setSizeHint(QSize(0, 78))
    port_list.addItem(item)
port_list.fit_to_contents()
layout.addWidget(port_list)
layout.addStretch()
outer.setWidget(host)
outer.show()
app.processEvents()
assert port_list.count() == 30
assert port_list.height() >= 30 * 78
assert port_list.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
bar = outer.verticalScrollBar()
assert bar.maximum() > 0

wheel = QWheelEvent(
    QPointF(12, 12), QPointF(12, 12), QPoint(0, 0), QPoint(0, -120),
    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    Qt.ScrollPhase.ScrollUpdate, False,
)
port_list.wheelEvent(wheel)
assert wheel.isAccepted()
assert 0 < bar.value() < bar.maximum(), (bar.value(), bar.maximum())

# The production QApplication installs a global nested-wheel filter, which
# previously swallowed events at the now-hidden inner scrollbar.
from linkvideo_vpn_helper.ui.nested_scroll_guard import install_nested_scroll_guard
install_nested_scroll_guard()
bar.setValue(0)
another = QWheelEvent(
    QPointF(12, 12), QPointF(12, 12), QPoint(0, 0), QPoint(0, -120),
    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    Qt.ScrollPhase.ScrollUpdate, False,
)
QApplication.sendEvent(port_list.viewport(), another)
assert bar.value() > 0, "Global wheel filter must forward to outer left pane"

# Expanding a DVR adds content to the SAME outer scroll area.
port_list.item(0).setSizeHint(QSize(0, 390))
port_list.fit_to_contents()
app.processEvents()
assert port_list.height() >= 29*78+390
assert bar.maximum() > 100
port_list.close()
outer.close()

engine = ArchiveDiagnosisEngine()
camera = ArchiveCamera("123", "linkvideo_123", "vcore.example", "live/123", "", 7)
short_gap = ArchiveDiscovery(
    camera, 0.0, 100000.0,
    [ArchiveSlice("vcore.example", "live", "123", 0.0, 99990.0, "")],
    [ArchiveGap(99990.0, 100000.0)], ["vcore.example"], [],
)
assert short_gap.coverage_percent > 99.5
diagnosis = engine.analyze(short_gap, [], [])
assert diagnosis.side is not DiagnosisSide.NONE
assert len(diagnosis.gaps) == 1, "A real 10-second gap must remain in the diagnosis"
assert diagnosis.confidence_text == "Недостаточно данных"

healthy = ArchiveDiscovery(
    camera, 0.0, 100000.0,
    [ArchiveSlice("vcore.example", "live", "123", 0.0, 100000.0, "")],
    [], ["vcore.example"], [],
)
assert engine.analyze(healthy, [], []).side is DiagnosisSide.NONE

source = __import__("pathlib").Path(__file__).resolve().parents[1].joinpath(
    "linkvideo_vpn_helper/ui/pages/archive_diagnostics_page.py"
).read_text(encoding="utf-8")
assert "if cov >= 99.5 and not main.gaps:" in source
assert "except Exception as exc:" in source
assert "Не удалось получить сравнение vcore" in source

print("CORE TESTS 3.0.20 SINGLE PORT SCROLL AND DVR GAP ACCURACY OK")
