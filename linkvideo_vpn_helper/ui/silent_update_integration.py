from __future__ import annotations

import threading

from linkvideo_vpn_helper.services.silent_update_service import (
    can_use_silent_patches,
    has_staged_patch,
    stage_patch,
    trigger_staged_patch,
)

_INSTALLED = False


def install_silent_patch_updates() -> None:
    """Make exact-version patches background-only while keeping full Setup visible.

    Startup checks remain automatic. If the public manifest offers a patch for
    the exact installed version and the privileged updater task is present, the
    patch is downloaded and staged without dialogs. It is applied when Helper is
    closed. Full installers keep the existing confirmation flow.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.main_window import MainWindow

    original_ready = MainWindow._on_update_ready
    original_close = MainWindow.closeEvent

    def patched_ready(self, info, error, startup: bool):
        if (
            error is None
            and info is not None
            and getattr(info, "has_update", False)
            and getattr(info, "is_patch", False)
            and can_use_silent_patches()
        ):
            if getattr(self, "_silent_patch_download_busy", False):
                return
            self._update_check_busy = False
            self._silent_patch_download_busy = True
            latest = str(getattr(info, "latest_version", ""))

            def progress(value: int) -> None:
                hook = getattr(self, "_lv_silent_patch_progress", None)
                if callable(hook):
                    hook(int(value), latest, startup)

            def worker() -> None:
                worker_error = None
                try:
                    path = self.updater.download_setup(
                        info.setup_url,
                        progress_callback=progress,
                        expected_sha256=getattr(info, "sha256", ""),
                        expected_version=latest,
                        artifact_kind="patch",
                    )
                    stage_patch(
                        path,
                        to_version=latest,
                        sha256=getattr(info, "sha256", ""),
                    )
                    self._silent_patch_staged = True
                    self._silent_patch_error = ""
                except Exception as exc:
                    # Never manipulate Qt widgets from the worker thread. The UX
                    # hook below emits a Qt signal and the next startup check may
                    # retry if staging failed.
                    worker_error = exc
                    self._silent_patch_error = str(exc)
                finally:
                    self._silent_patch_download_busy = False
                    hook = getattr(self, "_lv_silent_patch_finished", None)
                    if callable(hook):
                        hook(latest, worker_error, startup)

            threading.Thread(
                target=worker,
                daemon=True,
                name=f"lv-silent-patch:{latest}",
            ).start()
            return

        return original_ready(self, info, error, startup)

    def patched_close(self, event):
        # Trigger only after a complete patch has been staged. The task runs as
        # SYSTEM and waits briefly before touching Program Files, so the normal
        # Qt shutdown can finish first. If task start fails, pending files stay
        # in ProgramData and the next update check can retry safely.
        if has_staged_patch():
            trigger_staged_patch()
        return original_close(self, event)

    MainWindow._on_update_ready = patched_ready
    MainWindow.closeEvent = patched_close

    # Install these wrappers after the silent-patch handler so the manual update
    # UX can sit outside it and expose real patch download progress. The process
    # guard is global and also protects FFmpeg/PowerShell subprocesses.
    from linkvideo_vpn_helper.ui.update_ux_integration import install_update_ux
    from linkvideo_vpn_helper.ui.background_ux_integration import install_background_ux

    install_update_ux()
    install_background_ux()
    _INSTALLED = True
