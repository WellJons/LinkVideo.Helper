from __future__ import annotations

"""Offline tests for authorized Ula two-stage auth. No network or real secrets."""

from linkvideo_vpn_helper.services.ula_oracle_gateway import UlaReadError
from linkvideo_vpn_helper.services.ula_two_stage_auth import (
    connect_employee_two_stage, resolve_employee_username,
)


class StubCursor:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.closed = result, error, False
        self.calls = []

    def callfunc(self, name, result_type, arguments):
        self.calls.append((name, result_type, arguments))
        if self.error:
            raise self.error
        return self.result

    def close(self):
        self.closed = True


class StubConnection:
    def __init__(self, cursor):
        self.c = cursor
        self.closed = False

    def cursor(self):
        return self.c

    def close(self):
        self.closed = True


cur = StubCursor("EMPLOYEE_DB_NAME")
bootstrap = StubConnection(cur)
steps = []


def bootstrap_connect():
    steps.append("bootstrap")
    return bootstrap


def employee_connect(username, password):
    assert bootstrap.closed and cur.closed
    steps.append("employee")
    assert username == "EMPLOYEE_DB_NAME"
    assert password == "synthetic-secret"
    return StubConnection(StubCursor())


conn = connect_employee_two_stage(
    bootstrap_connect=bootstrap_connect,
    employee_connect=employee_connect,
    employee_login="test.employee", employee_password="synthetic-secret",
)
assert steps == ["bootstrap", "employee"]
assert cur.calls == [("sec.resolve_username", str, ["test.employee"])]
assert not conn.closed

for result in (None, "", "\n", "X" * 129, "BAD\x00USER", 42):
    c = StubConnection(StubCursor(result))
    employee_called = []
    try:
        connect_employee_two_stage(
            bootstrap_connect=lambda: c,
            employee_connect=lambda *_: employee_called.append(1),
            employee_login="test.employee", employee_password="synthetic-secret",
        )
    except UlaReadError:
        pass
    else:
        raise AssertionError("Invalid result must fail closed")
    assert c.closed and c.c.closed and not employee_called

c = StubConnection(StubCursor(error=RuntimeError("ORA-01017: hidden-password")))
try:
    resolve_employee_username(lambda: c, "test.employee")
except UlaReadError as exc:
    assert "hidden-password" not in str(exc)
else:
    raise AssertionError("Unexpected resolver success")
assert c.closed and c.c.closed

called = []
try:
    connect_employee_two_stage(
        bootstrap_connect=lambda: called.append("bootstrap"),
        employee_connect=lambda *_: called.append("employee"),
        employee_login="test.employee", employee_password="",
    )
except UlaReadError:
    pass
else:
    raise AssertionError("Empty employee password accepted")
assert not called

c = StubConnection(StubCursor("EMPLOYEE_DB_NAME"))
try:
    connect_employee_two_stage(
        bootstrap_connect=lambda: c,
        employee_connect=lambda *_: (_ for _ in ()).throw(
            RuntimeError("DB connection string and secret")),
        employee_login="test.employee", employee_password="synthetic-secret",
    )
except UlaReadError as exc:
    assert "secret" not in str(exc)
else:
    raise AssertionError("Unexpected employee connection success")
assert c.closed

print("CORE TESTS 3.0.16 ULA TWO-STAGE AUTH OFFLINE OK")
