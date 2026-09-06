from __future__ import annotations

from PySide6.QtCore import Qt

from linkvideo_vpn_helper.services.app_logging import event


_RUNTIME_INSTALLED = False
_UI_INSTALLED = False


def install_sheets_emergency_runtime() -> None:
    """Turn Google Sheets into an explicit disaster-recovery path only.

    VPNSync/PostgreSQL is authoritative. No periodic timer, startup sync or
    post-mutation automatic Google write is allowed in normal operation.
    Manual export APIs remain available as the last-resort reserve.
    """
    global _RUNTIME_INSTALLED
    if _RUNTIME_INSTALLED:
        return

    from linkvideo_vpn_helper.ui import vpn_sheets_sync_integration as integration

    cls = integration.VPNSyncCoordinator
    original_init = cls.__init__

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        periodic = getattr(self, "_periodic", None)
        if periodic is not None:
            periodic.stop()
        event("SHEETS", "Google Sheets в аварийном режиме", "автоматическая синхронизация отключена")

    def no_periodic(self):
        # QTimer.singleShot created by the legacy coordinator calls this patched
        # method and therefore cannot start a background fleet reconciliation.
        return None

    def no_mutation_sync(self, server: str, reason: str, login: str):
        # Normal mutations are already captured centrally by VPNSync listener +
        # PostgreSQL history. Sheets is intentionally allowed to become stale
        # until an operator explicitly requests an emergency backup.
        event(
            "SHEETS",
            "Резерв Google Sheets не обновлялся автоматически",
            f"{server} · {reason}" + (f" · {login}" if login else ""),
        )
        return None

    cls.__init__ = init
    cls._periodic_sync = no_periodic
    cls._queue_mutation = no_mutation_sync
    _RUNTIME_INSTALLED = True


def install_sheets_emergency_ui() -> None:
    """Correct the legacy VPN Servers card after its Sheets patch is attached."""
    global _UI_INSTALLED
    if _UI_INSTALLED:
        return

    from linkvideo_vpn_helper.ui import vpn_sheets_sync_integration as integration
    from linkvideo_vpn_helper.ui.pages.vpn_servers_page import VPNServersPage

    original_build = VPNServersPage._build

    def build(self):
        original_build(self)
        coordinator = getattr(integration, "_COORDINATOR", None)
        status = getattr(self, "sheets_sync_status", None)
        button = getattr(self, "sheets_sync_btn", None)
        restore_button = getattr(self, "sheets_restore_btn", None)

        # Client recovery is intentionally available only from the normal
        # client-search card, where the operator first sees whether the account
        # exists on live MikroTik. Google Sheets remains export-only emergency
        # reserve in this infrastructure page.
        if restore_button is not None:
            restore_button.hide()
            restore_button.setEnabled(False)

        if status is not None:
            if coordinator is not None and coordinator.is_configured():
                status.setText(
                    "Google Sheets · аварийный резерв · автоматическая синхронизация отключена · "
                    "основная база PostgreSQL на облачном сервере"
                )
            else:
                status.setText("Google Sheets · аварийный резерв не настроен")
        if button is not None:
            button.setText("Резервная копия в Google Sheets")
            button.setToolTip("Ручной аварийный экспорт текущего состояния RouterOS в Google Sheets")

        if coordinator is not None:
            def started(total: int):
                if status is not None:
                    status.setText(f"Ручной аварийный экспорт в Google Sheets · серверов {total}")
                if button is not None:
                    button.setText(f"Экспорт 0/{total}…")

            def progress(done: int, total: int, host: str, detail: str):
                if button is not None:
                    button.setText(f"Экспорт {done}/{total}…")
                if status is not None:
                    status.setText(f"Ручной резерв · {host} · {detail}")

            def finished(ok: int, failed: int):
                if button is not None:
                    button.setText("Резервная копия в Google Sheets")
                if status is not None:
                    if failed:
                        status.setText(f"Аварийный резерв обновлён частично · успешно {ok} · ошибок {failed}")
                    else:
                        status.setText(
                            f"Аварийный резерв обновлён вручную · серверов {ok} · "
                            "автосинхронизация выключена"
                        )

            coordinator.syncStarted.connect(started)
            coordinator.syncProgress.connect(progress)
            coordinator.syncFinished.connect(finished)

    VPNServersPage._build = build
    _UI_INSTALLED = True
