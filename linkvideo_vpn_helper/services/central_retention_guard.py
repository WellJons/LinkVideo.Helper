from __future__ import annotations

from linkvideo_vpn_helper.services.app_logging import event


_INSTALLED = False


def install_central_retention_guard() -> None:
    """Prevent desktop Helper from owning RouterOS retention automation.

    Lifecycle ownership has moved away from per-router LV Scheduler tasks and
    into the centralized LinkVideo.Cloud service. This guard keeps legacy calls
    fail-closed even if an old UI path or compatibility hook still invokes them.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.services import vpn_automation_service as public

    cls = public.VPNAutomationService

    def managed_centrally(*_args, **_kwargs):
        raise RuntimeError(
            "Локальная LV-автоматика на MikroTik больше не управляется из Helper. "
            "Жизненный цикл VPN-клиентов перенесён в LinkVideo.Cloud; локальные LV-команды заблокированы."
        )

    cls.install_or_update = managed_centrally
    cls.seed_lifecycle = managed_centrally
    cls.set_quarantine_enabled = managed_centrally
    cls.set_automation_enabled = managed_centrally

    event(
        "LV",
        "Desktop retention отключён",
        "Управление жизненным циклом перенесено в LinkVideo.Cloud; RouterOS LV Scheduler из Helper заблокирован",
    )
    _INSTALLED = True
