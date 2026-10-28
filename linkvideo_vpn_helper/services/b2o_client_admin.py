from __future__ import annotations

"""B2O operator admin client creation: known read-only contract.

The GET route and three operator ids are confirmed by the B2O UI.
The creation POST method, request body and privileges are NOT confirmed.
Do not add a write helper before reviewing the actual browser request.
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
