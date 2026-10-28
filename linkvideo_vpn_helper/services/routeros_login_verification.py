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
    missing_user_hosts: list[str] = []
    for host in hosts:
        stage = "connect"
        try:
            # A successful RouterOS /login is only the first step. Never
            # authorize a Helper user until /user confirms that exact enabled
            # account in the full group on a trusted server.
            with client_factory(host, user, password, port=8728, timeout=timeout) as api:
                stage = "user-list"
                accounts = api.print("/user", {".proplist": "name,group,disabled"})
                if not isinstance(accounts, list):
                    raise ValueError("Unexpected RouterOS user list format")
                record = next(
                    (item for item in accounts
                     if isinstance(item, dict) and str(item.get("name", "")) == user),
                    None,
                )
                if record is None:
                    # Regional servers can have different local user lists.
                    # Continue to another trusted server, but never accept
                    # this server's /login as proof of Helper authorization.
                    missing_user_hosts.append(host)
                    continue
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
            # Log only the endpoint, stage and exception class, never the
            # raw exception: a protocol exception may contain a secret.
            from linkvideo_vpn_helper.services.app_logging import event
            event("LOGIN", "Проверка RouterOS", f"{host} · {stage} · {type(exc).__name__}")

            if _is_auth_failure(exc):
                # Do not try other servers after explicit bad credentials.
                raise LoginRejected("Неверный логин или пароль MikroTik.") from exc

            if isinstance(exc, RouterOSAPIError) and stage == "user-list":
                # A permission denial while reading /user is not a network
                # outage. Never turn it into access or silently bypass it.
                raise LoginRejected(
                    "Не удалось подтвердить пользователя через /user MikroTik. "
                    "Проверьте права учётной записи и настройки API."
                ) from exc

            if isinstance(exc, (OSError, TimeoutError, socket.timeout,
                                RouterOSAPIError, ValueError, TypeError, UnicodeError)):
                # RouterOS versions/servers may disagree about protocol or
                # legacy challenge format. Move to the next trusted host.
                # Credentials are still never considered verified here.
                unavailable.append(host)
                continue
            unavailable.append(host)

    if missing_user_hosts:
        raise LoginRejected(
            "MikroTik принял данные входа, но пользователь не найден в /user "
            "на проверенных серверах. Вход запрещён."
        )
    raise LoginUnavailable(
        "Ни один VPN-сервер не завершил проверку входа. "
        "Проверьте доступность RouterOS API (8728/8729). "
        "Техническая причина записана в журнал LinkVideo.Helper."
    )
