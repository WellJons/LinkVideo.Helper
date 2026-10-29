from __future__ import annotations

"""Offline regression for the captured Ula Oracle service contract.

All rows are synthetic; no real client data, credentials or DB connection.
"""

from decimal import Decimal

from linkvideo_vpn_helper.services.ula_oracle_gateway import (
    SERVICES_CALL, UlaOracleReadOnlyGateway, UlaReadError, parse_ula_services,
)

assert "crm.get_client_services_list" in SERVICES_CALL
assert ":id_client" in SERVICES_CALL and ":result" in SERVICES_CALL
assert not any(word in SERVICES_CALL.upper() for word in ("INSERT ", "UPDATE ", "DELETE ", "MERGE "))

names = (
    "SERVICE_NAME", "COSTS", "ID_CONTRACT_SERVICE", "TARIFF_NAME",
    "DISCOUNT", "STATUS", "IS_ACTIVE_SERVICE", "DOG_NUM", "COST_CONNECT",
)
values = (
    ("Архив камеры", Decimal("1540.50"), 32, "ЮЛ 30 дней",
     Decimal("10"), "Действует", 1, "Д-001", Decimal("0")),
)
expected = parse_ula_services(names, values)
assert len(expected) == 1
assert expected[0].tariff_name == "ЮЛ 30 дней"
assert expected[0].costs_text == "1540.50"  # do not round / apply assumed discount
assert expected[0].discount_text == "10"
assert expected[0].connection_cost_text == "0"
assert expected[0].contract_service_id == "32"
assert expected[0].status_text == "Действует"
assert expected[0].contract_number == "Д-001"

for columns, rows in (
    (("TARIFF_NAME", "COSTS"), (("test", "100"),)),
    (("TARIFF_NAME", "COSTS", "COSTS", "ID_CONTRACT_SERVICE", "SERVICE_NAME"), ()),
    (names, (("short",),)),
):
    try:
        parse_ula_services(columns, rows)
    except UlaReadError:
        pass
    else:
        raise AssertionError("Ula must reject incomplete or ambiguous Oracle result")


class FakeResult:
    description = [(item,) for item in names]
    def __init__(self):
        self.remaining = list(values)
        self.closed = False
    def fetchmany(self, size):
        assert size <= 100
        batch, self.remaining = self.remaining[:size], self.remaining[size:]
        return batch
    def close(self):
        self.closed = True


class FakeVar:
    def __init__(self, value):
        self.value = value
    def getvalue(self):
        return self.value


class FakeCursor:
    def __init__(self, result):
        self.result = result
        self.called = False
        self.bindings = None
        self.closed = False
    def var(self, data_type):
        assert data_type == "CURSOR"
        return FakeVar(self.result)
    def execute(self, statement, **kwargs):
        assert statement == SERVICES_CALL
        assert kwargs["id_client"] == 71
        assert kwargs["is_non_active"] == 0
        assert kwargs["is_group_client"] == 0
        self.called = True
        self.bindings = kwargs
    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self):
        self.result = FakeResult()
        self.control = FakeCursor(self.result)
        self.closed = False
    def cursor(self):
        return self.control
    def close(self):
        self.closed = True


fake = FakeConnection()
gateway = UlaOracleReadOnlyGateway(lambda: fake, cursor_type="CURSOR")
records = gateway.list_services(71, include_non_active=False, group_client=False)
assert records == expected
assert fake.control.called
assert fake.control.closed and fake.result.closed and fake.closed
for bad in ("", "0", "-2", "abc", "71; DELETE", 0, None):
    try:
        gateway.list_services(bad, include_non_active=False, group_client=False)
    except UlaReadError:
        pass
    else:
        raise AssertionError("Ula client ID was not validated")
try:
    gateway.list_services(1, include_non_active=None, group_client=False)
except UlaReadError:
    pass
else:
    raise AssertionError("Unconfirmed Ula flags must be explicitly boolean")

print("CORE TESTS 3.0.16 ULA READONLY SERVICES OK")
