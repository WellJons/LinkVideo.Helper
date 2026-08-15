from __future__ import annotations

import socket
from dataclasses import dataclass
from enum import Enum

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIError


class ErrorKind(str, Enum):
    AUTH = "AUTH"
    TIMEOUT = "TIMEOUT"
    DNS = "DNS"
    REFUSED = "REFUSED"
    NETWORK = "NETWORK"
    API = "API"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION = "VALIDATION"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class OperationCancelled(Exception):
    """Cooperative cancellation requested by the user (Esc)."""


@dataclass(slots=True)
class OperationError:
    kind: ErrorKind
    message: str
    technical: str = ""

    @property
    def short(self) -> str:
        return self.message


def classify_exception(exc: BaseException) -> OperationError:
    text = str(exc or "").strip()
    low = text.lower()

    if isinstance(exc, OperationCancelled):
        return OperationError(ErrorKind.CANCELLED, "Операция отменена", text)
    if isinstance(exc, socket.timeout) or "timed out" in low or "timeout" in low:
        return OperationError(ErrorKind.TIMEOUT, "Сервер не ответил за отведённое время", text)
    if isinstance(exc, socket.gaierror) or "getaddrinfo" in low or "name or service not known" in low:
        return OperationError(ErrorKind.DNS, "Не удалось определить адрес сервера", text)
    if isinstance(exc, ConnectionRefusedError) or "actively refused" in low or "connection refused" in low:
        return OperationError(ErrorKind.REFUSED, "RouterOS API недоступен на этом сервере (соединение отклонено)", text)
    if isinstance(exc, RouterOSAPIError):
        if any(x in low for x in (
            "invalid user", "invalid password", "invalid user name or password",
            "not logged", "cannot log", "login failed", "authentication", "authoriz",
            "неверный логин", "неверный пароль", "авториза",
        )):
            return OperationError(ErrorKind.AUTH, "Неверный логин или пароль MikroTik", text)
        if "fatal" in low:
            return OperationError(ErrorKind.API, "RouterOS API завершил соединение", text)
        return OperationError(ErrorKind.API, text or "Ошибка RouterOS API", text)
    if isinstance(exc, ValueError):
        return OperationError(ErrorKind.VALIDATION, text or "Некорректные данные", text)
    if isinstance(exc, OSError):
        return OperationError(ErrorKind.NETWORK, "Ошибка сетевого подключения", text)
    return OperationError(ErrorKind.UNKNOWN, text or "Неизвестная ошибка", text)
