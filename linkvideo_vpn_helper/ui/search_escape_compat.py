from __future__ import annotations

"""Escape returns an opened client card to the search results."""


_INSTALLED = False


def install_search_escape_compat() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage

    original_cancel = SearchManagePage.cancel_current_action

    def cancel_current_action(self) -> bool:
        # A running search/action keeps priority: Esc cancels it first.
        if original_cancel(self):
            return True
        if getattr(self, "_action_busy", False):
            return False
        if getattr(self, "current", None) is None:
            return False

        timer = getattr(self, "_live_timer", None)
        if timer is not None:
            timer.stop()
        try:
            self._close_client_view()
        except Exception:
            return False

        self.current = None
        self._selected_port = None
        try:
            self.results.setCurrentRow(-1)
            self._highlight_result(None)
            self.open_hint.setText("Клик по записи — открыть карточку")
            self.query.setFocus()
        except Exception:
            pass
        return True

    SearchManagePage.cancel_current_action = cancel_current_action
    _INSTALLED = True
