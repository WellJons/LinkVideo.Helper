from __future__ import annotations

"""B2O operator client creation: confirmed endpoint, pending payload schema.

POST /operators/{id}/clients returned 200 with JSON {"id_account": N}.
The *request body* and permissions remain unknown; no write may occur
until the captured request payload is reviewed and mapped explicitly.
"""

import re
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
        # The enum values/ids and payload keys are not confirmed yet;
        # this is validation of the local draft, not a write contract.


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
