from __future__ import annotations

from linkvideo_vpn_helper.services.app_logging import event


_INSTALLED = False


def install_central_retention_guard() -> None:
    """Compatibility hook while central retention remains disabled.

    LinkVideo.Cloud/PostgreSQL is the backup/archive/audit plane, but the
    RouterOS LV scripts are still explicitly managed from Helper.  Do not
    replace or block VPNAutomationService methods here: operators must be able
    to install/update, seed, start/stop and configure the existing LV scripts.

    When central retention is eventually enabled in production, that migration
    must be a separate, explicit change rather than silently removing desktop
    controls.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    event(
        "LV",
        "Управление LV доступно",
        "RouterOS LV-скрипты управляются из Helper; центральный retention LinkVideo.Cloud не включён",
    )
    _INSTALLED = True
