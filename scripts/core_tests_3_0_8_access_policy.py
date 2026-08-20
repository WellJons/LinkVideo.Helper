from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from linkvideo_vpn_helper.services.access_policy import (
    is_employee_credentials,
    is_employee_username,
    install_service_access_policy,
)
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials, VPNService

assert is_employee_username("AdminChats")
assert is_employee_username("adminchats")
assert is_employee_username("  ADMINCHATS  ")
assert not is_employee_username("evstegneev.na")
assert is_employee_credentials(SessionCredentials("AdminChats", "x"))
assert not is_employee_credentials(SessionCredentials("ivanov", "x"))

install_service_access_policy()
service = VPNService()
employee = SessionCredentials("AdminChats", "x")

restricted_calls = [
    ("set_password", ("vpn01.linkvideo.ru", employee, "890000001", "newpass")),
    ("set_secret_enabled", ("vpn01.linkvideo.ru", employee, "890000001", False)),
    ("set_port_enabled", ("vpn01.linkvideo.ru", employee, "890000001", 11136, False)),
    ("remove_port", ("vpn01.linkvideo.ru", employee, "890000001", 11136)),
    ("recreate_port", ("vpn01.linkvideo.ru", employee, "890000001", 11136)),
    ("delete_client", ("vpn01.linkvideo.ru", employee, "890000001")),
]
for method_name, args in restricted_calls:
    method = getattr(service, method_name)
    assert getattr(getattr(VPNService, method_name), "_lv_system_admin_only", False), method_name
    try:
        method(*args)
    except PermissionError as exc:
        assert "системному администратору" in str(exc).lower(), (method_name, exc)
    else:
        raise AssertionError(f"AdminChats unexpectedly allowed: {method_name}")

assert not getattr(VPNService.add_ports, "_lv_system_admin_only", False)
assert not getattr(VPNService.create_client, "_lv_system_admin_only", False)
assert not getattr(VPNService.disconnect_client_session, "_lv_system_admin_only", False)

ui = (ROOT / "linkvideo_vpn_helper/ui/access_policy_integration.py").read_text(encoding="utf-8")
assert '_RESTRICTED_SECTIONS = {"inactive", "vpn_servers"}' in ui
assert '"Переподключить VPN"' in ui
assert '"Сменить пароль"' in ui
assert '"Вкл./выкл. учётку"' in ui
assert '"Удалить клиента"' in ui
assert '"Только системный администратор"' in ui
assert 'guard_ui("_password_inline"' in ui
assert 'guard_ui("_toggle_selected_port"' in ui
assert 'guard_ui("_remove_selected_port"' in ui
assert 'guard_ui("_toggle_account"' in ui
assert 'guard_ui("_delete"' in ui

startup = (ROOT / "linkvideo_vpn_helper/ui/search_visual_fixes.py").read_text(encoding="utf-8")
assert "install_access_policy()" in startup

print("CORE TESTS 3.0.8 ADMINCHATS ACCESS POLICY OK")
