from __future__ import annotations

"""Runtime log viewer embedded in Settings."""

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from linkvideo_vpn_helper.services.app_logging import (
    clear_logs,
    event,
    install_operation_logging,
    log_dir,
    read_recent,
)


_PATCHED = False


def install_runtime_log_ui() -> None:
    global _PATCHED
    install_operation_logging()
    if _PATCHED:
        return

    from linkvideo_vpn_helper.ui.components import Card
    from linkvideo_vpn_helper.ui.pages.settings_page import SettingsPage

    original_build = SettingsPage._build

    def patched_build(self):
        original_build(self)

        card = Card(subtle=True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        top = QHBoxLayout()
        labels = QVBoxLayout()
        labels.setSpacing(3)
        title = QLabel("Журнал работы")
        title.setObjectName("SectionTitle")
        hint = QLabel(
            "Последние действия и ошибки Helper. Пароли, Google private key и токены в журнал не записываются."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        labels.addWidget(title)
        labels.addWidget(hint)
        top.addLayout(labels, 1)

        refresh = QPushButton("Обновить")
        copy = QPushButton("Скопировать")
        folder = QPushButton("Открыть папку")
        clear = QPushButton("Очистить")
        clear.setProperty("role", "ghost")
        top.addWidget(refresh)
        top.addWidget(copy)
        top.addWidget(folder)
        top.addWidget(clear)
        layout.addLayout(top)

        viewer = QPlainTextEdit()
        viewer.setReadOnly(True)
        viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        viewer.setMinimumHeight(220)
        viewer.setMaximumHeight(340)
        viewer.document().setMaximumBlockCount(1200)
        self.runtime_log_viewer = viewer
        layout.addWidget(viewer)

        path_label = QLabel(str(log_dir()))
        path_label.setObjectName("TinyMuted")
        path_label.setWordWrap(True)
        layout.addWidget(path_label)

        def reload_logs():
            text = read_recent(1000)
            viewer.setPlainText(text)
            cursor = viewer.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            viewer.setTextCursor(cursor)

        def copy_logs():
            QApplication.clipboard().setText(viewer.toPlainText())
            event("LOG", "Журнал скопирован в буфер обмена")

        def open_logs():
            folder_path = log_dir()
            folder_path.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder_path)))
            event("LOG", "Открыта папка журнала", folder_path)

        def clear_view():
            clear_logs()
            reload_logs()

        refresh.clicked.connect(reload_logs)
        copy.clicked.connect(copy_logs)
        folder.clicked.connect(open_logs)
        clear.clicked.connect(clear_view)
        reload_logs()

        # SettingsPage finishes with TaskStatus + stretch. Insert the log card
        # immediately before TaskStatus so it remains visible above the spacer.
        task = getattr(self, "task", None)
        index = self.page_layout.indexOf(task) if task is not None else -1
        if index >= 0:
            self.page_layout.insertWidget(index, card)
        else:
            self.page_layout.addWidget(card)
        self.runtime_log_card = card

    SettingsPage._build = patched_build
    _PATCHED = True
