from __future__ import annotations

from linkvideo_vpn_helper.services.app_logging import event


_INSTALLED = False


def install_central_retention_guard() -> None:
    """Prevent desktop Helper from owning RouterOS retention automation.

    The central VPNSync DeadlineScheduler is the only supported owner of
    30/90/365 lifecycle decisions. Existing RouterOS LV scripts are migrated
    separately by the server rollout tool; this guard prevents employees from
    reinstalling/re-enabling them from an older UI path.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.services import vpn_automation_service as public

    cls = public.VPNAutomationService

    def managed_centrally(*_args, **_kwargs):
        raise RuntimeError(
            "Автоматика 30/90/365 теперь управляется центральным LinkVideo.VPNSync. "
            "Изменять LV Scheduler на MikroTik из Helper больше не требуется."
        )

    cls.install_or_update = managed_centrally
    cls.seed_lifecycle = managed_centrally
    cls.set_quarantine_enabled = managed_centrally
    cls.set_automation_enabled = managed_centrally

    event(
        "LV",
        "Desktop retention отключён",
        "30/90/365 управляется центральным VPNSync; RouterOS LV Scheduler из Helper заблокирован",
    )
    _INSTALLED = True
