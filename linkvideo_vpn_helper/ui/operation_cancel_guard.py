from __future__ import annotations

"""Make cancellable background operations actually cancellable from the UI.

BusyDialog is shared by many pages.  Historically it swallowed Escape and used
WindowModal unconditionally, which meant an interactive search could block both
Esc and the main-window close button while a network worker was stuck.

For pages that expose ``cancel_current_action()``, the busy dialog becomes
non-modal and Escape is routed to that cancellation hook.  Other long-running
mutations retain the original modal behaviour.
"""

from PySide6.QtCore import Qt

from linkvideo_vpn_helper.ui.components import BusyDialog


_INSTALLED = False
_ORIGINAL_REJECT = BusyDialog.reject
_ORIGINAL_SHOW_CENTERED = BusyDialog.show_centered


def _cancel_hook(dialog: BusyDialog):
    parent = dialog.parentWidget()
    if parent is None:
        return None
    cache = getattr(parent, "_page_cache", None)
    current_key = getattr(parent, "_current_key", "")
    page = cache.get(current_key) if isinstance(cache, dict) else None
    hook = getattr(page, "cancel_current_action", None)
    return hook if callable(hook) else None


def _show_centered(dialog: BusyDialog):
    # A cancellable operation must never disable the main window.  This also
    # guarantees that Windows' close button still works if a remote socket is
    # wedged below Python's normal timeout handling.
    dialog.setWindowModality(
        Qt.WindowModality.NonModal if _cancel_hook(dialog) else Qt.WindowModality.WindowModal
    )
    return _ORIGINAL_SHOW_CENTERED(dialog)


def _reject(dialog: BusyDialog):
    hook = _cancel_hook(dialog)
    if hook is not None:
        try:
            if hook():
                dialog.hide()
                return
        except Exception:
            # Never turn an Escape key into a UI crash.  Fall through to the
            # original behaviour if a page-specific cancellation hook fails.
            pass
    return _ORIGINAL_REJECT(dialog)


def install_operation_cancel_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    BusyDialog.show_centered = _show_centered
    BusyDialog.reject = _reject
    _INSTALLED = True
