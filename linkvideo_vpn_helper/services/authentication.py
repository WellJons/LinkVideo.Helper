from __future__ import annotations

"""Fail-closed RouterOS login verification before exposing the Helper UI."""

from concurrent.futures import ThreadPoolExecutor, as_completed

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIClient, RouterOSAPIError
from linkvideo_vpn_helper.services.errors import ErrorKind, classify_exception


class AuthenticationFailure(Exception):
    """User-facing reason that credentials could not be verified."""


def _verify_on_server(host: str, username: str, password: str, port: int, timeout: float) -> None:
    # RouterOS /login itself validates the account against /user and checks its
    # password/disabled state. Never accept a username based on a local list.
    with RouterOSAPIClient(host, username, password, port=port, timeout=timeout) as api:
        # Prove the session can run an authenticated read command. In particular,
        # a successful TCP connection is NOT proof of successful login.
        if not api.print("/system/identity", {".proplist": "name"}):
            raise RouterOSAPIError("RouterOS не подтвердил активную API-сессию")

        # Also check the RouterOS user record when this account is permitted to
        # query /user. Some limited support accounts cannot inspect /user; their
        # authenticated API session remains the authority in that case.
        try:
            users = api.print("/user", {"?name": username, ".proplist": "name,disabled"})
        except RouterOSAPIError as exc:
            text = str(exc).casefold()
            if not any(part in text for part in ("not enough permissions", "permission denied", "access denied", "not permitted", "insufficient permissions")):
                raise
            return
        for user in users:
            if str(user.get("name", "")).casefold() == username.casefold():
                if str(user.get("disabled", "no")).casefold() in ("yes", "true", "1"):
                    raise RouterOSAPIError("Учётная запись MikroTik отключена")
                return
        # When /user is hidden by a restricted policy it may return an empty
        # list. /login and /system/identity already proved the active session.
        if users:
            raise RouterOSAPIError("Авторизованная учётная запись отсутствует в /user")


def verify_credentials(username: str, password: str, hosts: list[str], *, port: int = 8728, timeout: float = 4.5) -> str:
    """Return a verified host or deny login; never bypass on network failures.

    Accounts may exist on only one of the configured VPN routers. Probe enabled
    servers concurrently rather than rejecting a real user after one failure.
    """
    username = str(username or "").strip()
    if not username or not password:
        raise AuthenticationFailure("Введите логин и пароль MikroTik.")
    servers = list(dict.fromkeys(str(host).strip() for host in hosts if str(host).strip()))
    if not servers:
        raise AuthenticationFailure("Нет VPN-серверов для проверки учётной записи.")

    denied = False
    network_failure = False
    other_failure = False
    executor = ThreadPoolExecutor(max_workers=min(4, len(servers)), thread_name_prefix="lv-auth")
    pending = [executor.submit(_verify_on_server, host, username, password, port, timeout) for host in servers]
    try:
        for future in as_completed(pending):
            try:
                future.result()
            except Exception as exc:
                kind = classify_exception(exc).kind
                if kind == ErrorKind.AUTH or isinstance(exc, RouterOSAPIError) and any(
                    part in str(exc).casefold() for part in ("отключена", "отсутствует в /user")
                ):
                    denied = True
                elif kind in (ErrorKind.TIMEOUT, ErrorKind.DNS, ErrorKind.REFUSED, ErrorKind.NETWORK):
                    network_failure = True
                else:
                    other_failure = True
            else:
                return servers[pending.index(future)]
    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    if denied:
        raise AuthenticationFailure("Логин или пароль не подтверждены VPN-серверами. Проверьте данные MikroTik.")
    if network_failure and not other_failure:
        raise AuthenticationFailure("Не удалось подключиться к VPN-серверам для проверки входа. Проверьте сеть и повторите попытку.")
    raise AuthenticationFailure("Не удалось подтвердить учётную запись через RouterOS API. Попробуйте позже или обратитесь к администратору.")
