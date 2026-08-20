from __future__ import annotations

"""Role policy for the shared AdminChats RouterOS account.

The support team signs in with the literal ``AdminChats`` login. Personal
RouterOS logins used by system administrators are treated as privileged. The
policy is deliberately enforced at the service boundary as well as in the UI so
hidden buttons are not the only protection around destructive mutations.
"""

from functools import wraps


EMPLOYEE_LOGIN = "adminchats"
SYSTEM_ADMIN_ONLY = "Действие доступно только системному администратору."
_INSTALLED = False


def _username(value) -> str:
    if hasattr(value, "username"):
        value = getattr(value, "username", "")
    return str(value or "").strip()


def is_employee_username(username: str) -> bool:
    return _username(username).casefold() == EMPLOYEE_LOGIN


def is_employee_credentials(credentials) -> bool:
    return is_employee_username(_username(credentials))


def require_system_admin(credentials, action: str = "Это действие") -> None:
    if is_employee_credentials(credentials):
        raise PermissionError(f"{action} доступно только системному администратору.")


def install_service_access_policy() -> None:
    """Wrap dangerous VPNService mutations with an AdminChats guard."""
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.services.vpn_service import VPNService

    restricted = {
        "remove_port": "Удаление NAT-порта",
        "set_password": "Смена пароля VPN-учётки",
        "set_secret_enabled": "Включение или отключение VPN-учётки",
        "set_port_enabled": "Включение или отключение NAT-порта",
        "recreate_port": "Пересоздание NAT-порта",
        "delete_client": "Удаление VPN-клиента",
    }

    for method_name, action in restricted.items():
        original = getattr(VPNService, method_name, None)
        if not callable(original) or getattr(original, "_lv_system_admin_only", False):
            continue

        def make_wrapper(fn, label):
            @wraps(fn)
            def guarded(self, server, creds, *args, **kwargs):
                require_system_admin(creds, label)
                return fn(self, server, creds, *args, **kwargs)

            guarded._lv_system_admin_only = True
            guarded._lv_original = fn
            return guarded

        setattr(VPNService, method_name, make_wrapper(original, action))

    _INSTALLED = True
