from __future__ import annotations

"""Two-stage Ula Oracle login using an explicitly authorized bootstrap connection.

Do not extract, embed, derive or reuse credentials from the proprietary Ula
binary. The caller must provide a separately approved bootstrap connection
with EXECUTE permission for SEC.RESOLVE_USERNAME.

This module is intentionally not wired into the production UI until the
authorized bootstrap connection and employee permissions can be verified.
"""

from collections.abc import Callable
from typing import Protocol

from linkvideo_vpn_helper.services.ula_oracle_gateway import UlaReadError


class _Cursor(Protocol):
    def callfunc(self, name: str, result_type: type, parameters: list[str]): ...
    def close(self) -> None: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...
    def close(self) -> None: ...


def _validate_login(login: str) -> str:
    if not isinstance(login, str):
        raise UlaReadError("Укажите логин сотрудника Ula.")
    value = login.strip()
    if not value or len(value) > 128 or any(ord(ch) < 32 for ch in value):
        raise UlaReadError("Некорректный логин сотрудника Ula.")
    return value


def _validate_resolved(value: object) -> str:
    if not isinstance(value, str):
        raise UlaReadError("SEC.RESOLVE_USERNAME не вернула имя пользователя Oracle.")
    result = value.strip()
    if not result or len(result) > 128 or any(ord(ch) < 32 for ch in result):
        raise UlaReadError("SEC.RESOLVE_USERNAME вернула недопустимое имя Oracle.")
    return result


def resolve_employee_username(
    bootstrap_connect: Callable[[], _Connection], employee_login: str,
) -> str:
    """Resolve login with a separate permitted session; no fallback on error.

    This does NOT authenticate the employee. The personal password and granted
    roles must be verified on the subsequent employee Oracle session.
    """
    login = _validate_login(employee_login)
    conn = None
    try:
        conn = bootstrap_connect()
        cursor = conn.cursor()
        try:
            resolved = cursor.callfunc("sec.resolve_username", str, [login])
        finally:
            cursor.close()
        return _validate_resolved(resolved)
    except UlaReadError:
        raise
    except Exception as exc:
        raise UlaReadError(
            "Не удалось выполнить SEC.RESOLVE_USERNAME через разрешённую "
            "служебную сессию Oracle."
        ) from None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def connect_employee_two_stage(
    *, bootstrap_connect: Callable[[], _Connection],
    employee_connect: Callable[[str, str], _Connection],
    employee_login: str, employee_password: str,
) -> _Connection:
    """Resolve username, then authenticate with the employee's own password.

    A returned connection has NOT yet passed Ula business-role authorization.
    The caller must check granted roles before any customer data is read.
    """
    login = _validate_login(employee_login)
    if not isinstance(employee_password, str) or not employee_password:
        raise UlaReadError("Введите пароль сотрудника Ula.")
    resolved = resolve_employee_username(bootstrap_connect, login)
    try:
        return employee_connect(resolved, employee_password)
    except Exception as exc:
        raise UlaReadError(
            "Oracle отклонила второй этап авторизации или соединение недоступно."
        ) from None
