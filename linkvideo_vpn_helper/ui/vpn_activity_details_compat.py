from __future__ import annotations

from typing import Any


_INSTALLED = False


def install_vpn_activity_details() -> None:
    """Render useful non-secret parameters for employee and automatic VPN actions."""
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.services.cloud_vpnsync import CloudVPNSyncClient
    from linkvideo_vpn_helper.ui.pages import vpn_activity_page as page_module

    # The cloud login can be a shared service account. Direct Helper actions put
    # the actual desktop employee in non-secret audit details; present that name
    # in the Employee column while keeping the transport account in PostgreSQL.
    original_activity = CloudVPNSyncClient.activity

    def activity(self, *args, **kwargs):
        rows = list(original_activity(self, *args, **kwargs) or [])
        for row in rows:
            if not isinstance(row, dict) or str(row.get("source") or "") != "desktop":
                continue
            details = row.get("details") or {}
            if not isinstance(details, dict):
                continue
            employee = str(details.get("employee") or "").strip()
            if employee:
                row["actor"] = employee
        return rows

    CloudVPNSyncClient.activity = activity

    VPNActivityPage = page_module.VPNActivityPage
    page_module._SOURCE_LABELS.update({
        "archive": "Восстановление",
        "retention": "Автоматика",
    })
    page_module._ACTION_LABELS.update({
        "archive.restore.preflight": "Проверка перед восстановлением",
        "archive.restore.server_preflight": "Проверка сервера перед восстановлением",
        "archive.restore": "Восстановление VPN-клиента",
        "retention.sleep": "Переход в спящий статус",
        "retention.quarantine": "Карантин VPN-клиента",
        "retention.delete": "Автоматическое удаление VPN-клиента",
        "retention.cancelled_active": "Автоматика отменена: клиент активен",
        "retention.error": "Ошибка автоматики VPN",
    })

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

        if action.startswith("retention."):
            if action == "retention.cancelled_active":
                active = details.get("active_sessions")
                return f"Найдена активная VPN-сессия: {active}" if active is not None else "Клиент снова активен"
            if action == "retention.sleep":
                return f"Следующее действие: {details.get('next_action_at', '—')}"
            error_text = str(details.get("error") or "").strip()
            if error_text:
                return error_text

        return original(row)

    VPNActivityPage._details_text = details_text
    _INSTALLED = True
