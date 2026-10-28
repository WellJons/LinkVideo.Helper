from __future__ import annotations

"""Verify a desktop login against a live RouterOS /user record.

A populated login/password field is never evidence of authentication. The
RouterOS API must accept the credentials AND /user must return the same,
enabled account in the full group. The result is deliberately not cached:
saved credentials are revalidated on every new Helper process.
"""

from dataclasses import dataclass
import socket

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIClient, RouterOSAPIError


class LoginRejected(Exception):
    """Credentials, identity, enabled status or group were not accepted."""


class LoginUnavailable(Exception):
    """No trusted RouterOS endpoint could verify this login."""


@dataclass(frozen=True, slots=True)
class VerifiedRouterOSUser:
    username: str
    group: str
    server: str


def _is_enabled(value: object) -> bool:
    return str(value or "").strip().lower() not in {"true", "yes", "1", "on"}


def _is_auth_failure(exc: BaseException) -> bool:
    text = str(exc).casefold()
    return isinstance(exc, RouterOSAPIError) and any(
        marker in text for marker in (
            "invalid user", "invalid password", "invalid user name or password",
            "not logged in", "login failed", "cannot log in", "authentication failed",
            "неверный логин", "неверный пароль",
        )
    )


def verify_routeros_user(
    username: str,
    password: str,
    servers: list[str] | tuple[str, ...],
    *,
    timeout: float = 4.5,
    client_factory=RouterOSAPIClient,
) -> VerifiedRouterOSUser:
    user = str(username or "").strip()
    if not user or not password:
        raise LoginRejected("Введите логин и пароль MikroTik.")

    hosts = list(dict.fromkeys(str(host or "").strip() for host in servers if str(host or "").strip()))
    if not hosts:
        raise LoginUnavailable("Не настроены VPN-серверы для проверки входа.")

    unavailable: list[str] = []
    for host in hosts:
        try:
            # Connect first authenticates through /login; no API access is granted
            # by this Python application until both steps below succeed.
            with client_factory(host, user, password, port=8728, timeout=timeout) as api:
                accounts = api.print("/user", {".proplist": "name,group,disabled"})
                record = next(
                    (item for item in accounts if str(item.get("name", "")) == user),
                    None,
                )
                if record is None:
                    raise LoginRejected(
                        "RouterOS принял соединение, но пользователь не найден в /user. Вход запрещён."
                    )
                if not _is_enabled(record.get("disabled", "no")):
                    raise LoginRejected("Учётная запись MikroTik отключена. Вход запрещён.")
                group = str(record.get("group", "")).strip()
                if group.casefold() != "full":
                    raise LoginRejected(
                        "Недостаточно прав RouterOS: для входа в Helper требуется группа full."
                    )
                return VerifiedRouterOSUser(user, group, host)
        except LoginRejected:
            raise
        except Exception as exc:
            # An explicit bad-password response from a reachable authentication
            # endpoint must not be bypassed by trying a different router.
            if _is_auth_failure(exc):
                raise LoginRejected("Неверный логин или пароль MikroTik.") from exc

            # /user permission failures and API traps are not network outages.
            # Do not fall through to another server with potentially different
            # group or account state.
            if isinstance(exc, RouterOSAPIError):
                raise LoginRejected(
                    "Не удалось подтвердить пользователя через /user MikroTik. "
                    "Проверьте права учётной записи и настройки API."
                ) from exc

            if isinstance(exc, (OSError, TimeoutError, socket.timeout)):
                unavailable.append(host)
                continue
            raise LoginUnavailable(
                "Не удалось безопасно завершить проверку входа в MikroTik."
            ) from exc

    raise LoginUnavailable(
        "Не удалось связаться ни с одним VPN-сервером для проверки входа. "
        "Проверьте сеть и повторите попытку."
    )
