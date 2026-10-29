from __future__ import annotations

"""Ula / Russian billing: verified read-only services contract.

The captured Oracle PL/SQL call is deliberately the only database operation
in this module. Connection creation and cursor type come from the caller;
no credentials, server names or Oracle client DLLs are embedded in Helper.

COSTS is the amount reported by Ula. Its exact billing semantics (period,
discount, VAT) must be checked against two test Ula screens before labelling
it a monthly charge.
"""

from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence


SERVICES_CALL = """
begin
  :result := crm.get_client_services_list(
    p_id_client       => :id_client,
    p_is_non_active   => :is_non_active,
    p_is_group_client => :is_group_client
  );
end;
"""

SEARCH_CALL = """
begin
  :result := devdb.get_clients_by_filter_ula(:search_text, :type);
end;
"""

@dataclass(frozen=True)
class UlaClientHit:
    client_id: str
    name: str
    client_type: str = ""


def parse_ula_clients(columns: Sequence[str], records: Iterable[Sequence[object]]) -> tuple[UlaClientHit, ...]:
    headers = tuple(str(value or "").upper() for value in columns)
    if "ID_CLIENT" not in headers or len(set(headers)) != len(headers):
        raise UlaReadError("Поиск Ula не вернул уникальное поле ID_CLIENT.")
    idx = {name: i for i, name in enumerate(headers)}
    result: list[UlaClientHit] = []
    for row in records:
        if len(result) >= 200:
            raise UlaReadError("Ula вернула слишком много результатов поиска.")
        if len(row) != len(headers):
            raise UlaReadError("Некорректная строка поиска Ula.")
        ident = _cell(row[idx["ID_CLIENT"]])
        if not ident.isascii() or not ident.isdecimal() or int(ident) <= 0:
            raise UlaReadError("Некорректный ID клиента Ula.")
        result.append(UlaClientHit(
            client_id=ident,
            name=_cell(row[idx["CLIENT_NAME"]]) if "CLIENT_NAME" in idx else "",
            client_type=_cell(row[idx["ID_CLIENT_TYPE"]]) if "ID_CLIENT_TYPE" in idx else "",
        ))
    return tuple(result)


MAX_SERVICES = 3000
_REQUIRED_COLUMNS = frozenset(("ID_CONTRACT_SERVICE", "SERVICE_NAME", "TARIFF_NAME", "COSTS"))
_OPTIONAL_COLUMNS = ("DISCOUNT", "STATUS", "IS_ACTIVE_SERVICE", "DOG_NUM", "COST_CONNECT")


@dataclass(frozen=True)
class UlaService:
    contract_service_id: str
    service_name: str
    tariff_name: str
    costs_text: str
    discount_text: str = ""
    status_text: str = ""
    active_flag: str = ""
    contract_number: str = ""
    connection_cost_text: str = ""


class UlaReadError(RuntimeError):
    pass


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    # Oracle NUMBER commonly arrives as Decimal, never round it via float.
    from decimal import Decimal
    if isinstance(value, Decimal):
        return str(value)
    raise UlaReadError("Ula вернула поле услуги в неподдерживаемом формате.")


def parse_ula_services(columns: Sequence[str], records: Iterable[Sequence[object]]) -> tuple[UlaService, ...]:
    """Map Oracle cursor columns by name; fail closed on unexpected data."""
    headers = tuple(str(field or "").strip().upper() for field in columns)
    if not headers or len(headers) != len(set(headers)):
        raise UlaReadError("Ula вернула пустые или повторяющиеся имена столбцов.")
    missing = _REQUIRED_COLUMNS.difference(headers)
    if missing:
        raise UlaReadError(
            "В ответе Ula отсутствуют обязательные поля услуг: " + ", ".join(sorted(missing))
        )
    index = {name: position for position, name in enumerate(headers)}
    parsed: list[UlaService] = []
    for record in records:
        if len(parsed) >= MAX_SERVICES:
            raise UlaReadError("Ula вернула слишком много услуг. Запрос ограничен для безопасности.")
        if not isinstance(record, (list, tuple)) or len(record) != len(headers):
            raise UlaReadError("Ula вернула неполную строку услуги.")
        def read(name: str) -> str:
            return _cell(record[index[name]]) if name in index else ""
        parsed.append(UlaService(
            contract_service_id=read("ID_CONTRACT_SERVICE"),
            service_name=read("SERVICE_NAME"),
            tariff_name=read("TARIFF_NAME"),
            costs_text=read("COSTS"),
            discount_text=read("DISCOUNT"),
            status_text=read("STATUS"),
            active_flag=read("IS_ACTIVE_SERVICE"),
            contract_number=read("DOG_NUM"),
            connection_cost_text=read("COST_CONNECT"),
        ))
    return tuple(parsed)


class UlaOracleReadOnlyGateway:
    """Invoke the captured function with binds; caller owns Oracle credentials.

    connection_factory must return a new, owned DB-API connection.
    cursor_type is oracledb.DB_TYPE_CURSOR / cx_Oracle.CURSOR, injected by
    the caller so importing Helper never requires Oracle libraries.
    """

    def __init__(self, connection_factory: Callable[[], Any], cursor_type: Any):
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connect = connection_factory
        self._cursor_type = cursor_type

    def search_clients(self, search_text: str, *, search_type: int = 0) -> tuple[UlaClientHit, ...]:
        # Search mode 0 is a test default, not yet confirmed by Oracle captures.
        # A failed response is shown to the operator; never silently try
        # alternative modes that could expose an unintended client.
        query = str(search_text or "").strip()
        if len(query) < 2 or len(query) > 100:
            raise UlaReadError("Введите не менее двух символов имени или идентификатора клиента.")
        if type(search_type) is not int or search_type not in (0, 1, 2):
            raise UlaReadError("Неподдерживаемый режим поиска Ula.")
        connection = self._connect()
        cursor = None
        result_cursor = None
        try:
            cursor = connection.cursor()
            out = cursor.var(self._cursor_type)
            cursor.execute(
                SEARCH_CALL,
                result=out,
                search_text=query,
                type=search_type,
            )
            result_cursor = out.getvalue()
            if result_cursor is None or not result_cursor.description:
                raise UlaReadError("Поиск Ula не вернул курсор результата.")
            headers = [col[0] for col in result_cursor.description]
            rows = result_cursor.fetchmany(201)
            return parse_ula_clients(headers, rows)
        finally:
            for obj in (result_cursor, cursor, connection):
                if obj is not None:
                    with suppress(Exception):
                        obj.close()

    def list_services(
        self, client_id: int | str, *,
        include_non_active: bool,
        group_client: bool,
    ) -> tuple[UlaService, ...]:
        raw_id = str(client_id).strip()
        if not raw_id.isascii() or not raw_id.isdecimal() or int(raw_id) <= 0:
            raise UlaReadError("Для получения услуг требуется положительный ID клиента Ula.")
        if not isinstance(include_non_active, bool) or not isinstance(group_client, bool):
            raise UlaReadError("Не определены параметры состояния и группы клиента Ula.")

        connection = self._connect()
        cursor = None
        result_cursor = None
        try:
            cursor = connection.cursor()
            result_bind = cursor.var(self._cursor_type)
            cursor.execute(
                SERVICES_CALL,
                result=result_bind,
                id_client=int(raw_id),
                is_non_active=int(include_non_active),
                is_group_client=int(group_client),
            )
            result_cursor = result_bind.getvalue()
            if result_cursor is None or not result_cursor.description:
                raise UlaReadError("Oracle не вернула курсор со списком услуг.")
            column_names = [field[0] for field in result_cursor.description]

            def capped_rows():
                count = 0
                while True:
                    batch = result_cursor.fetchmany(100)
                    if not batch:
                        return
                    for item in batch:
                        count += 1
                        if count > MAX_SERVICES:
                            raise UlaReadError("Превышено допустимое количество услуг Ula.")
                        yield item

            return parse_ula_services(column_names, capped_rows())
        finally:
            # Oracle can raise during cursor cleanup after a more relevant
            # query failure. Close all three resources independently without
            # masking the original exception or skipping connection.close().
            for obj in (result_cursor, cursor, connection):
                if obj is not None:
                    with suppress(Exception):
                        obj.close()
