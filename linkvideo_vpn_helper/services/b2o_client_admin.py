from __future__ import annotations

"""B2O operator clients: explicit opt-in creation via the verified RU payload.

Other operators, account type IDs and option combinations remain read-only.
Network errors after sending a POST can leave an unknown creation outcome.
"""

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from linkvideo_vpn_helper.services.archive_service_core import B2OService


BASE_URL = "https://api.b2o.goodline.info/ords/mobile/vc2"
ADMIN_URL = "https://operator.b2o.goodline.info"
OPERATOR_BY_COUNTRY = {"Россия": 241, "Казахстан": 1721, "Беларусь": 1741}


def operator_for_country(country: str) -> int:
    try:
        operator = OPERATOR_BY_COUNTRY[country]
    except (KeyError, TypeError):
        raise ValueError("Неизвестный оператор B2O") from None
    if B2OService.operator_country(operator) != country:
        raise ValueError("Профиль B2O не совпадает со страной")
    return operator


def settings_path(country: str) -> str:
    return f"/operators/{operator_for_country(country)}/settings"


def creation_path(country: str) -> str:
    """Verified POST path for creation, not permission to submit it."""
    return f"/operators/{operator_for_country(country)}/clients"


def created_account_id(response: dict[str, Any]) -> int:
    """Parse the observed POST success response without guessing success.

    Never log the response; validate its shape and require a positive ID.
    A 200 response with no valid id_account is NOT confirmed creation.
    """
    if not isinstance(response, dict):
        raise ValueError("Сервер B2O вернул неожиданный результат создания")
    raw = response.get("id_account")
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise ValueError("B2O не подтвердил ID созданного клиента")
    string_id = str(raw)
    if not re.fullmatch(r"[1-9][0-9]{0,17}", string_id):
        raise ValueError("B2O вернул некорректный ID созданного клиента")
    return int(string_id)


def operator_admin_url() -> str:
    # We have an official origin from the application's current B2O code.
    # A specific /clients/new deep link has NOT been verified.
    return ADMIN_URL


@dataclass(frozen=True)
class B2OClientDraft:
    country: str
    name: str
    login: str
    password: str
    phone: str = ""
    account_type: str = ""
    create_in_billing: bool = False
    cross_sharing: bool = False

    def check(self) -> None:
        operator_for_country(self.country)
        if not self.name.strip() or not self.login.strip() or not self.password:
            raise ValueError("Заполните имя, логин и пароль")
        if len(self.name) > 200 or len(self.login) > 100:
            raise ValueError("Слишком длинное имя или логин")
        if not re.fullmatch(r"[a-zA-Z0-9_.@+-]{2,100}", self.login.strip()):
            raise ValueError("В логине есть недопустимые символы")
        if len(self.password) < 8:
            raise ValueError("Пароль должен содержать не меньше 8 символов")
        if len(self.phone) > 30:
            raise ValueError("Номер телефона слишком длинный")
        if not self.account_type:
            raise ValueError("Выберите тип аккаунта")
        # Check validates basic input. Only verified_payload authorises writes.

    def verified_payload(self) -> dict[str, str | int]:
        """Precisely match the single captured 2026-10-28 RU POST variant."""
        self.check()
        if self.country != "Россия":
            raise ValueError("Создание через API пока подтверждено только для России")
        if self.account_type != "1":
            raise ValueError("Для создания подтверждён только тип аккаунта 1")
        if not self.create_in_billing:
            raise ValueError("Пока подтверждено создание только с опцией «Завести в биллинг»")
        if self.cross_sharing:
            raise ValueError("Создание с общим доступом к камерам пока не подтверждено")
        return {
            "account_type": "1",
            "client_name": self.name.strip(),
            "cross_sharing_enabled": 0,
            "login": self.login.strip(),
            "password": self.password,
            "phone": self.phone.strip(),
            "push_to_billing": 1,
        }


class B2OOperatorReadOnly:
    def __init__(self, b2o: B2OService):
        self.b2o = b2o

    def read_client(self, country: str, account_id: int, token: str | None = None) -> dict[str, Any]:
        """Confirmed GET performed by B2O after client creation.

        Intentionally no POST here: request payload is still required.
        """
        operator_for_country(country)
        if isinstance(account_id, bool) or not isinstance(account_id, int) or not 0 < account_id < 10**18:
            raise ValueError("Некорректный ID клиента")
        provided = token if token is not None else self.b2o.token()
        if not provided:
            raise PermissionError("Сначала войдите в B2O.")
        url = BASE_URL + creation_path(country) + f"/{account_id}"
        result = self.b2o.request_json(url, provided)
        if not isinstance(result, dict) or str(result.get("id_account", "")) != str(account_id):
            raise ValueError("Сервер вернул данные другого клиента")
        return result

    def check_access(self, country: str, token: str | None = None) -> dict[str, Any]:
        operator = operator_for_country(country)
        provided = token if token is not None else self.b2o.token()
        if not provided:
            raise PermissionError("Сначала войдите в B2O во вкладке «Скачать архив».")
        url = BASE_URL + settings_path(country)
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "api.b2o.goodline.info":
            raise ValueError("Некорректный адрес B2O")
        response = self.b2o.request_json(url, provided)
        if not isinstance(response, dict):
            raise ValueError("B2O вернул неподдерживаемый формат настроек")
        # GET settings confirms operator access, but DOES NOT establish the
        # right to POST new customers. Never infer create permission.
        returned_operator = response.get("id_operator")
        if returned_operator is not None and str(returned_operator) != str(operator):
            raise ValueError("B2O вернул настройки другого оператора")
        return response


class B2OCreationOutcomeUnknown(RuntimeError):
    """POST may have been processed; user must check B2O before retrying."""


@dataclass(frozen=True)
class B2OCreatedClient:
    id_account: int
    read_back_verified: bool


class B2OOperatorClientManager(B2OOperatorReadOnly):
    """Single non-retrying POST followed by a separate read-back GET."""

    def create_client(self, draft: B2OClientDraft, token: str | None = None) -> B2OCreatedClient:
        payload = draft.verified_payload()  # Reject unverified options before any network I/O.
        provided = token if token is not None else self.b2o.token()
        if not provided:
            raise PermissionError("Войдите в B2O перед созданием клиента")
        url = BASE_URL + creation_path(draft.country)
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Origin": ADMIN_URL,
                "Referer": ADMIN_URL + "/clients",
                "User-Agent": "LinkVideo.Helper/2.0",
                "Token": provided,
            },
            method="POST",
        )
        # NEVER retry a POST automatically. Do not emit payload, token or response
        # into logs, exceptions or diagnostic UI; they may contain credentials.
        try:
            with urllib.request.urlopen(request, timeout=25) as reply:
                raw = reply.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code >= 500:
                raise B2OCreationOutcomeUnknown(
                    "B2O вернул ошибку сервера. Проверьте клиента в админке перед повтором."
                ) from None
            raise RuntimeError(f"B2O отклонил создание: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise B2OCreationOutcomeUnknown(
                "Связь прервалась. Клиент мог быть создан: проверьте админку перед повтором."
            ) from None
        try:
            account_id = created_account_id(json.loads(raw))
        except (ValueError, TypeError):
            raise B2OCreationOutcomeUnknown(
                "Ответ на POST не подтвердил ID. Проверьте клиента в админке перед повтором."
            ) from None
        try:
            read_back = self.read_client(draft.country, account_id, token=provided)
            verified = read_back.get("login") == draft.login.strip()
        except Exception:
            # A successful POST must not be retried merely because GET failed.
            verified = False
        return B2OCreatedClient(account_id, verified)
