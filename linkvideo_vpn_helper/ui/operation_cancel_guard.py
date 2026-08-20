from __future__ import annotations

"""Make interactive waits cancellable/non-blocking without weakening mutations.

BusyDialog is shared by many pages. Historically it swallowed Escape and used
WindowModal unconditionally, which meant an interactive search could block both
Esc and the main-window close button while a network worker was stuck.

Only a page with a live ``_cancel_event`` is treated as cancellable. Destructive
RouterOS mutations on the same page keep the original modal behaviour. Purely
informational/background waits (for example update check/download) may opt into
non-modal display with the ``lvBackgroundWait`` property.
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
    event = getattr(page, "_cancel_event", None)
    if event is None or getattr(event, "is_set", lambda: True)():
        return None
    hook = getattr(page, "cancel_current_action", None)
    return hook if callable(hook) else None


def _is_background_wait(dialog: BusyDialog) -> bool:
    try:
        return bool(dialog.property("lvBackgroundWait"))
    except RuntimeError:
        return False


def _show_centered(dialog: BusyDialog):
    # Searches and read-only background jobs may be cancelled/closed without
    # weakening destructive RouterOS operations. Mutation dialogs stay modal.
    dialog.setWindowModality(
        Qt.WindowModality.NonModal
        if _is_background_wait(dialog) or _cancel_hook(dialog)
        else Qt.WindowModality.WindowModal
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
            pass
    if _is_background_wait(dialog):
        # Hiding the indicator does not cancel the bounded background request,
        # but it must never trap the user in a modal child window.
        dialog.hide()
        return
    return _ORIGINAL_REJECT(dialog)


def install_operation_cancel_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    BusyDialog.show_centered = _show_centered
    BusyDialog.reject = _reject
    _INSTALLED = True
