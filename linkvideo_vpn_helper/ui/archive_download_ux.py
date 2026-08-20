from __future__ import annotations

"""Archive download UX fixes for long-running real-world downloads.

Archive discovery may keep its short floating wait, but the actual download can
run for many minutes and must stay visible inside the archive page instead of
covering the application with BusyDialog.  Also make the selected-folder button
unambiguous and always report/open the actual output file location.
"""

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from linkvideo_vpn_helper.services.archive_service import ArchiveDownloadResult


_INSTALLED = False


def install_archive_download_ux() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.pages.archive_download_page import ArchiveDownloadPage

    original_build = ArchiveDownloadPage._build
    original_download = ArchiveDownloadPage._download
    original_update = ArchiveDownloadPage._on_download_update
    original_cancel = ArchiveDownloadPage.cancel_current_action

    def patched_build(self):
        original_build(self)
        # "Папка" looked like an "open folder" action even though it invokes
        # QFileDialog.getExistingDirectory, which intentionally hides files.
        self.btn_folder.setText("Изменить папку")

        original_task_busy = self.task.busy

        def task_busy(title: str, detail: str = "", progress=None, progress_text: str = ""):
            if getattr(self, "_lv_archive_download_active", False):
                return self.task.busy_inline(title, detail, progress, progress_text)
            return original_task_busy(title, detail, progress, progress_text)

        # This is an instance-only routing decision: other TaskStatus users keep
        # the normal floating busy dialog.
        self.task.busy = task_busy

    def patched_download(self):
        self._lv_archive_download_active = True
        try:
            return original_download(self)
        except Exception:
            self._lv_archive_download_active = False
            raise

    def _valid_output(result) -> bool:
        if not isinstance(result, ArchiveDownloadResult):
            return False
        try:
            path = Path(result.output)
            return path.is_file() and path.stat().st_size > 0
        except Exception:
            return False

    def patched_update(self, payload):
        kind = payload.get("type") if isinstance(payload, dict) else ""
        if kind == "done":
            result = payload.get("result")
            if not _valid_output(result):
                # Never claim success merely because FFmpeg returned a result
                # object. The final MP4 must physically exist and be non-empty.
                replacement = {
                    "type": "error",
                    "error": RuntimeError(
                        "FFmpeg завершил обработку, но итоговый MP4 не найден в выбранной папке."
                    ),
                    "cancel_event": payload.get("cancel_event"),
                }
                self._lv_archive_download_active = False
                return original_update(self, replacement)

            outcome = original_update(self, payload)
            self._lv_archive_download_active = False
            output = Path(result.output).resolve()
            self._last_output = output
            # Keep the save field synchronized with the directory that actually
            # contains the verified file.
            self.folder.setText(str(output.parent))
            self.settings.setValue("archive/folder_v2", str(output.parent))
            current_detail = str(self.task.detail.text() or "").strip()
            path_line = f"Путь: {output}"
            if path_line not in current_detail:
                self.task.detail.setText((current_detail + "\n\n" + path_line).strip())
            self.summary_text.setText(f"Архив сохранён: {output}")
            return outcome

        if kind == "error":
            self._lv_archive_download_active = False
        return original_update(self, payload)

    def patched_cancel(self) -> bool:
        result = original_cancel(self)
        if result:
            self._lv_archive_download_active = False
        return result

    def patched_open_folder(self):
        output = getattr(self, "_last_output", None)
        if output is not None:
            try:
                output = Path(output)
                if output.exists():
                    target = output.parent
                    target.mkdir(parents=True, exist_ok=True)
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
                    return
            except Exception:
                pass
        folder = Path(self.folder.text().strip() or (Path.home() / "Videos" / "LinkVideo_Archive"))
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    ArchiveDownloadPage._build = patched_build
    ArchiveDownloadPage._download = patched_download
    ArchiveDownloadPage._on_download_update = patched_update
    ArchiveDownloadPage.cancel_current_action = patched_cancel
    ArchiveDownloadPage._open_folder = patched_open_folder
    _INSTALLED = True
