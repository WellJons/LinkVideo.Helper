from __future__ import annotations

"""Small UI lifecycle and readability fixes for search/lifecycle lists."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QLabel


_INSTALLED = False


def _enhance_result_rows(page) -> None:
    results = getattr(page, "results", None)
    registry = getattr(page, "registry", None)
    if results is None or registry is None:
        return
    for row in range(results.count()):
        item = results.item(row)
        if item is None:
            continue
        client = item.data(Qt.ItemDataRole.UserRole)
        widget = results.itemWidget(item)
        if client is None or widget is None:
            continue
        try:
            country = registry.get(client.server).country
        except Exception:
            country = "—"
        meta = f"Сервер: {client.server}   ·   Страна: {country}   ·   Портов: {len(client.ports)}"
        for label in widget.findChildren(QLabel):
            text = str(label.text() or "")
            if client.server not in text and "портов:" not in text.lower():
                continue
            label.setText(meta)
            label.setStyleSheet("font-size: 12px; font-weight: 600;")
            label.setMinimumHeight(21)
            break


def _fix_inactive_row(page) -> None:
    listing = getattr(page, "list", None)
    if listing is None or listing.count() <= 0:
        return
    item = listing.item(listing.count() - 1)
    card = listing.itemWidget(item) if item is not None else None
    if item is None or card is None:
        return

    card.setMinimumHeight(100)
    hinted = card.sizeHint()
    item.setSizeHint(QSize(hinted.width(), max(106, hinted.height() + 6)))

    for label in card.findChildren(QLabel):
        text = str(label.text() or "").strip().lower()
        if text.endswith("дн.") or text == "неизвестно":
            label.setMinimumHeight(23)
            label.setContentsMargins(10, 2, 10, 2)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            break


def install_search_visual_fixes() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage
    from linkvideo_vpn_helper.ui.pages.inactive_clients_page import InactiveClientsPage

    original_on_search = SearchManagePage._on_search
    original_add_result = SearchManagePage._add_result
    original_live_refresh = getattr(SearchManagePage, "_on_live_refresh", None)
    original_add_inactive = InactiveClientsPage._add_record

    def patched_on_search(self, report):
        if getattr(report, "matches", None):
            task = getattr(self, "task", None)
            close_busy = getattr(task, "_close_busy_dialog", None)
            if callable(close_busy):
                close_busy()
        result = original_on_search(self, report)
        _enhance_result_rows(self)
        return result

    def patched_add_result(self, client):
        result = original_add_result(self, client)
        _enhance_result_rows(self)
        return result

    def patched_live_refresh(self, *args, **kwargs):
        result = original_live_refresh(self, *args, **kwargs)
        _enhance_result_rows(self)
        return result

    def patched_add_inactive(self, record):
        result = original_add_inactive(self, record)
        _fix_inactive_row(self)
        return result

    SearchManagePage._on_search = patched_on_search
    SearchManagePage._add_result = patched_add_result
    if callable(original_live_refresh):
        SearchManagePage._on_live_refresh = patched_live_refresh
    InactiveClientsPage._add_record = patched_add_inactive
    _INSTALLED = True
