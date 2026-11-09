from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit, QPushButton

from linkvideo_vpn_helper.services.archive_service import ArchiveService
from linkvideo_vpn_helper.services.archive_download_methods import install_archive_download_methods
from linkvideo_vpn_helper.ui.components_compat import install_components_compat


def main() -> None:
    app = QApplication.instance() or QApplication([])
    install_components_compat()
    install_archive_download_methods()

    from linkvideo_vpn_helper.ui.pages.archive_download_page import ArchiveDownloadPage

    with tempfile.TemporaryDirectory(prefix="lvh_archive_ui_") as td:
        ini = Path(td) / "settings.ini"
        settings = QSettings(str(ini), QSettings.Format.IniFormat)
        service = ArchiveService(settings)
        page = ArchiveDownloadPage(service, settings)
        combo = getattr(page, "archive_method_combo", None)
        assert combo is not None, "archive method selector was not created"
        assert combo.count() == 3
        assert [combo.itemText(i) for i in range(combo.count())] == [
            "1. FFmpeg",
            "2. Curl",
            "3. Без звука",
        ]
        assert [combo.itemData(i) for i in range(combo.count())] == [
            "ffmpeg",
            "curl",
            "ffmpeg_no_audio",
        ]
        assert combo.currentData() == "ffmpeg"
        assert page.archive_command_button.objectName() == "ArchiveManualFFmpegButton"

        modal_errors = []
        def apply_manual():
            dialog = QApplication.activeModalWidget()
            try:
                assert isinstance(dialog, QDialog)
                editor = dialog.findChild(QPlainTextEdit, "ArchiveManualFFmpegEditor")
                assert editor is not None
                assert "{input}" in editor.toPlainText()
                assert "{output}" in editor.toPlainText()
                editor.setPlainText(
                    'ffmpeg -y -nostdin -i "{input}" -c copy "{output}"')
                save = next(button for button in dialog.findChildren(QPushButton)
                            if button.text() == "Сохранить")
                save.click()
            except Exception as exc:
                modal_errors.append(exc)
                if dialog is not None:
                    dialog.reject()  # Prevent a stuck modal on CI.

        QTimer.singleShot(0, apply_manual)
        page.archive_command_button.click()
        assert not modal_errors, modal_errors
        assert page._lv_archive_manual_command
        assert "изменена" in page.archive_command_button.text()
        assert page.service._lv_archive_manual_command == page._lv_archive_manual_command

        combo.setCurrentIndex(1)
        app.processEvents()
        assert settings.value("archive/download_method", "", str) == "curl"
        assert not page._lv_archive_manual_command, "choosing standard method resets manual mode"
        page.deleteLater()
        app.processEvents()

    print("CORE TESTS 3.0.11 ARCHIVE UI SMOKE OK")


if __name__ == "__main__":
    main()
