from __future__ import annotations

"""Clarify that VPNSync API authentication is separate from Ubuntu SSH."""

from linkvideo_vpn_helper.services.cloud_vpnsync import CloudConnectionError, CloudVPNSyncClient


_INSTALLED = False


def install_cloud_auth_message_compat() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_login = CloudVPNSyncClient.login

    def login(self):
        try:
            return original_login(self)
        except CloudConnectionError as exc:
            text = str(exc)
            if "Неверный логин или пароль облачного сервера" in text:
                raise CloudConnectionError(
                    "Сервер VPNSync доступен, но вход не выполнен. Используется отдельная учётная запись VPNSync; "
                    "логин Ubuntu/SSH (Termius) автоматически сюда не переносится."
                ) from exc
            raise

    CloudVPNSyncClient.login = login
    _INSTALLED = True
