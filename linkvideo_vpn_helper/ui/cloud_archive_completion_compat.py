from __future__ import annotations

"""Finish deleted-archive UI from the real VPNSync response signal."""


_INSTALLED = False


def install_cloud_archive_completion_compat() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage

    original = SearchManagePage._on_deleted_search

    def on_deleted_search(self, query: str, hits, error):
        current_query = self.query.text().strip()
        wanted = str(query or "").strip()
        original(self, query, hits, error)

        # A late response for an older query must not touch current UI state.
        if self._mode != "login" or wanted != current_query:
            return

        # The signal above is the completion event. Never leave the operator UI
        # saying that the archive is still being checked after this point.
        self._deleted_lookup_pending = False
        if error is not None:
            if self.results.count():
                self.task.hide()
                self.open_hint.setText("Клик по записи — открыть карточку")
            else:
                self.open_hint.setText("Архив удалённых недоступен")
            return

        if self.results.count():
            self.open_hint.setText("Клик по записи — открыть карточку")
        else:
            self.open_hint.setText("Совпадений нет")

    SearchManagePage._on_deleted_search = on_deleted_search
    SearchManagePage._lv_archive_response_completion = True
    _INSTALLED = True
