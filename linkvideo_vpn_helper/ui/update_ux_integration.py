from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

from linkvideo_vpn_helper.services.silent_update_service import can_use_silent_patches
from linkvideo_vpn_helper.ui.components import BusyDialog
from linkvideo_vpn_helper.ui.dialogs import ConfirmDialog


_INSTALLED = False


class _UpdateBridge(QObject):
    progress = Signal(int, str)
    finished = Signal(str, str, str, bool)


def install_update_ux() -> None:
    """Give manual update checks the same visible lifecycle as long client jobs.

    Startup checks stay quiet. A user-initiated check owns a centered spinner from
    the first network request until a final result. Downloads expose real byte
    progress from UpdateService, including silent differential patches. The
    indicator itself is deliberately non-modal: checking/downloading may never
    trap the operator in Helper while a remote channel is slow.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.main_window import MainWindow

    original_init = MainWindow.__init__
    silent_ready = MainWindow._on_update_ready

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._lv_update_dialog = None
        self._lv_update_download_busy = False
        self._lv_update_manual_active = False
        self._lv_update_bridge = _UpdateBridge(self)
        self._lv_update_bridge.progress.connect(
            lambda value, detail: _show_busy(self, "Скачиваю обновление", detail, value)
        )
        self._lv_update_bridge.finished.connect(
            lambda kind, title, detail, startup: _finish(self, kind, title, detail, startup)
        )

    def _dialog(self):
        dialog = getattr(self, "_lv_update_dialog", None)
        if dialog is None:
            dialog = BusyDialog(self)
            # operation_cancel_guard treats read-only/background waits as
            # non-modal, so the main window can still be moved/closed while a
            # bounded update request is running.
            dialog.setProperty("lvBackgroundWait", True)
            self._lv_update_dialog = dialog
        return dialog

    def _show_busy(self, title: str, detail: str, progress: int | None = None):
        if not getattr(self, "_lv_update_manual_active", False):
            return
        dialog = _dialog(self)
        dialog.spinner.start()
        text = "" if progress is None else f"{max(0, min(100, int(progress)))}%"
        dialog.update_busy(title, detail, progress, text)
        dialog.show_centered()

    def _finish(self, kind: str, title: str, detail: str, startup: bool = False):
        if startup:
            return
        self._lv_update_manual_active = False
        dialog = getattr(self, "_lv_update_dialog", None)
        if dialog is not None:
            try:
                dialog.spinner.stop()
                dialog.hide()
            except RuntimeError:
                pass
        timeout = 6500 if kind == "error" else 4200
        self.toast.showMessage(title, detail, timeout)
        self._position_toast()

    def patched_check(self, startup: bool = False):
        if (
            getattr(self, "_update_check_busy", False)
            or getattr(self, "_lv_update_download_busy", False)
            or getattr(self, "_silent_patch_download_busy", False)
        ):
            return
        self._update_check_busy = True
        if not startup:
            self._lv_update_manual_active = True
            _show_busy(self, "Проверяю обновления", "Получаю manifest и сравниваю версии…")

        def worker():
            try:
                self.updateReady.emit(self.updater.check(), None, startup)
            except Exception as exc:
                self.updateReady.emit(None, exc, startup)

        threading.Thread(target=worker, daemon=True, name="lv-update-check").start()

    def patched_ready(self, info, error, startup: bool):
        self._update_check_busy = False
        if error:
            _finish(self, "error", "Не удалось проверить обновления", str(error), startup)
            return
        if info is None:
            _finish(self, "error", "Не удалось проверить обновления", "Сервис обновлений не вернул результат.", startup)
            return
        if not getattr(info, "has_update", False):
            _finish(
                self,
                "success",
                "Установлена актуальная версия",
                f"LinkVideo.Helper {getattr(info, 'current_version', '')}",
                startup,
            )
            return

        # The inner handler is the silent-patch integration installed just before
        # this layer. Let it own exact-version patches, but keep our visible
        # progress surface for a manual check.
        if getattr(info, "is_patch", False) and can_use_silent_patches():
            if not startup:
                self._lv_update_manual_active = True
                _show_busy(
                    self,
                    "Скачиваю патч",
                    f"Версия {info.latest_version} · проверяю целостность после загрузки…",
                    0,
                )
            return silent_ready(self, info, None, startup)

        # A full installer remains an explicit user action. Close the checking
        # overlay before showing the confirmation dialog, then reopen it for the
        # actual download so there is never an ambiguous dead period.
        if not startup:
            dialog = getattr(self, "_lv_update_dialog", None)
            if dialog is not None:
                dialog.hide()
        confirm = ConfirmDialog(
            f"Доступна версия {info.latest_version}",
            (info.notes or "Доступно обновление LinkVideo.Helper.")
            + "\n\nДля этой версии нужен полный установщик. Скачать и запустить обновление?",
            "Скачать обновление",
            False,
            self,
        )
        if not confirm.exec():
            _finish(self, "neutral", "Обновление отложено", f"Версия {info.latest_version} не скачивалась.", startup)
            return

        self._lv_update_manual_active = True
        self._lv_update_download_busy = True
        _show_busy(self, "Скачиваю обновление", f"LinkVideo.Helper {info.latest_version}", 0)

        def progress(value: int):
            bridge = getattr(self, "_lv_update_bridge", None)
            if bridge is not None:
                bridge.progress.emit(int(value), f"Загружено {int(value)}% · затем SHA-256 и версия EXE будут проверены")

        def worker():
            try:
                path = self.updater.download_setup(
                    info.setup_url,
                    progress_callback=progress,
                    expected_sha256=getattr(info, "sha256", ""),
                    expected_version=info.latest_version,
                    artifact_kind=getattr(info, "artifact_kind", "setup"),
                )
                self.updateDownloaded.emit(path, None)
            except Exception as exc:
                self.updateDownloaded.emit(None, exc)

        threading.Thread(target=worker, daemon=True, name="lv-update-download").start()

    def patched_downloaded(self, path, error):
        self._lv_update_download_busy = False
        if error:
            _finish(self, "error", "Обновление не скачано", str(error), False)
            return
        if path is None:
            _finish(self, "error", "Обновление не скачано", "Файл обновления отсутствует.", False)
            return
        _show_busy(self, "Обновление проверено", "Запускаю установщик…", 100)
        try:
            self.updater.run_setup(path)
        except Exception as exc:
            _finish(self, "error", "Не удалось запустить установщик", str(exc), False)

    # These two hooks are intentionally thread-safe: silent_update_integration
    # invokes them from its download worker, and they only emit Qt signals.
    def silent_patch_progress(self, value: int, latest_version: str, startup: bool):
        if startup:
            return
        bridge = getattr(self, "_lv_update_bridge", None)
        if bridge is not None:
            bridge.progress.emit(
                int(value),
                f"Патч {latest_version}: загружено {int(value)}% · проверяю SHA-256",
            )

    def silent_patch_finished(self, latest_version: str, error, startup: bool):
        bridge = getattr(self, "_lv_update_bridge", None)
        if bridge is None or startup:
            return
        if error:
            bridge.finished.emit("error", "Патч не подготовлен", str(error), False)
        else:
            bridge.finished.emit(
                "success",
                "Обновление готово",
                f"Патч {latest_version} скачан и проверен. Он применится автоматически после закрытия Helper.",
                False,
            )

    MainWindow.__init__ = patched_init
    MainWindow._check_updates = patched_check
    MainWindow._on_update_ready = patched_ready
    MainWindow._on_update_downloaded = patched_downloaded
    MainWindow._lv_silent_patch_progress = silent_patch_progress
    MainWindow._lv_silent_patch_finished = silent_patch_finished
    _INSTALLED = True
