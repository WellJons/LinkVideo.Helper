from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    guard_path = "linkvideo_vpn_helper/services/central_retention_guard.py"
    sheets_path = "linkvideo_vpn_helper/ui/vpn_sheets_emergency_only.py"
    restore_path = "linkvideo_vpn_helper/ui/vpn_servers_lv_controls_restore.py"
    page_path = "linkvideo_vpn_helper/ui/pages/vpn_servers_page.py"
    policy_path = "linkvideo_vpn_helper/services/vpn_retention_policy.py"

    guard = read(guard_path)
    sheets = read(sheets_path)
    restore = read(restore_path)
    page = read(page_path)
    policy = read(policy_path)

    for path, text in (
        (guard_path, guard),
        (sheets_path, sheets),
        (restore_path, restore),
        (page_path, page),
        (policy_path, policy),
    ):
        ast.parse(text, filename=path)

    # LinkVideo.Cloud backup/archive must not silently disable the real RouterOS
    # LV controls while central retention is still disabled in production.
    for assignment in (
        "cls.install_or_update =",
        "cls.seed_lifecycle =",
        "cls.set_quarantine_enabled =",
        "cls.set_automation_enabled =",
    ):
        assert assignment not in guard
    assert "управляются из Helper" in guard

    # The final UI composition restores supported LV management after compact
    # operator polish. The obsolete centralized-only wrapper must stay gone.
    assert "install_vpn_servers_lv_controls_restore" in sheets
    assert "install_vpn_servers_centralized_ui" not in sheets
    assert 'install_all.setText("Установить / обновить LV на всех")' in restore
    assert "install_all.show()" in restore
    assert "start_all.show()" in restore
    assert "stop_all.show()" in restore

    # Per-server operational controls remain available as well.
    assert 'QPushButton("Обновить LV" if auto and auto.installed else "Установить LV")' in page
    assert 'QPushButton("Инициализировать активность")' in page
    assert 'QPushButton("Запустить LV" if auto and auto.paused else "Остановить LV")' in page
    assert 'QPushButton("Выключить карантин" if auto and auto.aging_enabled else "Включить карантин")' in page

    # Current local RouterOS automation really is 2.1.0, so an installed 2.0.0
    # must be reported as requiring an update rather than as an UI error.
    assert 'RETENTION_VERSION = "2.1.0"' in policy
    assert "public.LV_AUTOMATION_VERSION = RETENTION_VERSION" in policy

    print("LV_CONTROLS_RESTORED_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
