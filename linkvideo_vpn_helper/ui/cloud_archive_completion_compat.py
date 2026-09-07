from __future__ import annotations

"""Finish deleted-archive UI from real responses and keep live search primary."""


_INSTALLED = False


def install_cloud_archive_completion_compat() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage

    # The PostgreSQL archive is a recovery fallback, not part of normal active
    # client lookup. Do not contact it at all when live MikroTik search found a
    # client. Only a zero-match login search falls through to the archive.
    def on_search(self, report):
        self._cancel_event = None
        self.btn_search.setEnabled(True)
        failed_servers = {str(item.server).lower() for item in report.errors}
        successful = max(0, int(report.checked) - len(failed_servers))
        self._active_match_count = len(report.matches)
        self._search_had_errors = bool(report.errors)

        for client in report.matches:
            self._add_result(client)

        archive_lookup = False
        if (
            not report.matches
            and self._mode == "login"
            and self._deleted_lookup_query == self.query.text().strip()
        ):
            bridge = getattr(self, "_cloud_archive_bridge", None)
            if bridge is not None:
                archive_lookup = True
                self._deleted_lookup_pending = True
                bridge.search_deleted(self._deleted_lookup_query)

        if report.matches:
            # searchReady is the real completion event for the active MikroTik
            # search. TaskStatus.hide also closes the floating BusyDialog.
            self.task.hide()
            self.search_note.clear()
        elif report.errors:
            self.task.warning(
                "Клиент не найден на всех доступных серверах",
                f"Успешно проверено {successful}/{report.total}. {len(report.errors)} сервер(ов) проверить не удалось.",
            )
            self.search_note.setText(
                "Не удалось проверить:\n"
                + "\n".join(f"• {item.server}: {item.error.message}" for item in report.errors[:10])
            )
        elif archive_lookup:
            self.task.busy(
                "Проверяю удалённые учётки",
                "Активного клиента нет · выполняю реальный запрос к архиву PostgreSQL…",
            )
            self.search_note.clear()
        else:
            self.task.done("Клиент не найден", f"Проверено серверов: {successful}/{report.total}")
            self.search_note.clear()

        if self.results.count():
            self.results.setCurrentRow(-1)
            self.open_hint.setText("Клик по записи — открыть карточку")
        elif archive_lookup:
            self.open_hint.setText("Проверяю архив удалённых…")
        else:
            self.open_hint.setText("Совпадений нет")

    original_deleted = SearchManagePage._on_deleted_search

    def on_deleted_search(self, query: str, hits, error):
        current_query = self.query.text().strip()
        wanted = str(query or "").strip()
        original_deleted(self, query, hits, error)

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

    SearchManagePage._on_search = on_search
    SearchManagePage._on_deleted_search = on_deleted_search
    SearchManagePage._lv_archive_response_completion = True
    _INSTALLED = True
