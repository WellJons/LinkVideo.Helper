from __future__ import annotations

from typing import Any


_INSTALLED = False


def install_vpn_activity_details() -> None:
    """Render useful non-secret parameters for employee RouterOS actions."""
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.pages.vpn_activity_page import VPNActivityPage

    original = VPNActivityPage._details_text

    @staticmethod
    def details_text(row: dict[str, Any]) -> str:
        details = row.get("details") or {}
        if not isinstance(details, dict):
            return original(row)
        action = str(row.get("action") or "")

        if action == "client.create":
            parts: list[str] = []
            remote = str(details.get("remote_address") or "").strip()
            ports = [str(item) for item in list(details.get("ports") or [])]
            if remote:
                parts.append(f"Remote Address {remote}")
            if ports:
                parts.append("порты " + ", ".join(ports))
            if details.get("ports_per_client") not in (None, "", 0):
                parts.append(f"портов на клиента {details.get('ports_per_client')}")
            return " · ".join(parts) or "VPN-клиент создан"

        if action == "nat.add_ports":
            return f"Добавлено портов: {details.get('count', 0)}"
        if action == "nat.remove_port":
            return f"Удалён внешний порт: {details.get('port', '—')}"
        if action == "nat.recreate":
            return f"Пересоздан внешний порт: {details.get('port', '—')}"
        if action == "client.password_change":
            return "Пароль изменён · значение пароля в историю не записывается"
        if action == "client.enabled_change":
            return "Клиент включён" if bool(details.get("enabled")) else "Клиент отключён"
        if action == "nat.enabled_change":
            state = "включён" if bool(details.get("enabled")) else "отключён"
            return f"Порт {details.get('port', '—')} {state}"
        if action == "client.disconnect":
            return "VPN-сессия отключена" if bool(details.get("session_removed")) else "Активная VPN-сессия не найдена"
        if action == "client.delete":
            return "VPN-клиент удалён с MikroTik"

        return original(row)

    VPNActivityPage._details_text = details_text
    _INSTALLED = True
