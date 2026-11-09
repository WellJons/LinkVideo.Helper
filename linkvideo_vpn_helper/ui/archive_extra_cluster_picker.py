from __future__ import annotations

"""Optional B2O archive operator selection, shared by download and diagnosis.

Only explicit settings toggles expose these choices. Identical operator IDs
share the same B2O data; display labels can differ without inventing credentials.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget


class ArchiveExtraClusterPicker(QWidget):
    changed = Signal(object)

    def __init__(self, b2o, parent=None):
        super().__init__(parent)
        self.b2o = b2o
        self._loading = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        title = QLabel("Доп. кластер")
        title.setObjectName("TinyMuted")
        title.setMinimumWidth(110)
        layout.addWidget(title)
        self.combo = QComboBox()
        self.combo.setObjectName("ArchiveExtraClusterSelector")
        self.combo.setMinimumHeight(39)
        self.combo.setToolTip(
            "Выберите включённый дополнительный оператор B2O. "
            "Здравсервис и Goodline используют одинаковый ID 1442.")
        layout.addWidget(self.combo, 1)
        self.combo.currentIndexChanged.connect(self._changed)
        self.refresh()

    def refresh(self) -> None:
        current_key = self.combo.currentData()
        self._loading = True
        try:
            self.combo.clear()
            self.combo.addItem("Основные кластеры (Россия / Казахстан / Беларусь)", None)
            for key, title, operator_id in self.b2o.active_extra_clusters():
                self.combo.addItem(f"{title} · оператор {operator_id}", key)
            index = self.combo.findData(current_key)
            self.combo.setCurrentIndex(max(0, index))
            self.setVisible(self.combo.count() > 1)
        finally:
            self._loading = False
        self.changed.emit(self.selected_operator())

    def selected_operator(self) -> int | None:
        key = self.combo.currentData()
        for entry_key, _title, operator_id in self.b2o.active_extra_clusters():
            if key == entry_key:
                return int(operator_id)
        return None

    def clear_selection(self):
        self.combo.setCurrentIndex(0)

    def _changed(self, *_):
        if not self._loading:
            self.changed.emit(self.selected_operator())
