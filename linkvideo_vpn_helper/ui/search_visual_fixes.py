from __future__ import annotations

"""Small UI lifecycle fixes for search results.

Two rendering layers are intentionally kept separate in 3.0.8: TaskStatus owns
its floating BusyDialog, while inline port traffic replaces QListWidget rows
with custom widgets. This module closes the search BusyDialog on a successful
result and prevents the hidden base item text from being painted underneath the
custom port row.
"""

from PySide6.QtCore import Qt


_INSTALLED = False


def install_search_visual_fixes() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage
    import linkvideo_vpn_helper.ui.port_traffic_inline as port_traffic_inline

    original_on_search = SearchManagePage._on_search
    original_decorate = port_traffic_inline._decorate_port_rows

    def patched_on_search(self, report):
        # A successful search used to call TaskStatus.hide(). TaskStatus itself
        # is already hidden while busy, so that did not close the separate
        # BusyDialog and left "Ищу порт" floating over the finished card.
        if getattr(report, "matches", None):
            task = getattr(self, "task", None)
            close_busy = getattr(task, "_close_busy_dialog", None)
            if callable(close_busy):
                close_busy()
        return original_on_search(self, report)

    def patched_decorate(page):
        original_decorate(page)
        port_list = getattr(page, "port_list", None)
        if port_list is None:
            return
        for index in range(port_list.count()):
            item = port_list.item(index)
            if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
                continue
            # setItemWidget() does not reliably suppress QListWidgetItem's own
            # display text on Windows styles. The custom row already contains
            # the port label, so keeping both produces the doubled text seen in
            # the client card.
            item.setText("")

    SearchManagePage._on_search = patched_on_search
    port_traffic_inline._decorate_port_rows = patched_decorate
    _INSTALLED = True
