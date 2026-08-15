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

            def worker() -> None:
                try:
                    path = self.updater.download_setup(
                        info.setup_url,
                        expected_sha256=getattr(info, "sha256", ""),
                        expected_version=info.latest_version,
                        artifact_kind="patch",
                    )
                    stage_patch(
                        path,
                        to_version=info.latest_version,
                        sha256=getattr(info, "sha256", ""),
                    )
                    self._silent_patch_staged = True
                except Exception as exc:
                    # A background patch must never manipulate Qt widgets from
                    # its worker thread. Keep the error for diagnostics and let
                    # the next normal update check retry.
                    self._silent_patch_error = str(exc)
                finally:
                    self._silent_patch_download_busy = False

            threading.Thread(
                target=worker,
                daemon=True,
                name=f"lv-silent-patch:{getattr(info, 'latest_version', '')}",
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
    _INSTALLED = True
