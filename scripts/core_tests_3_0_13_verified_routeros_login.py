from __future__ import annotations

"""Real authentication gate regression without live MikroTik credentials."""

from pathlib import Path

from linkvideo_vpn_helper.services.routeros_login_verification import (
    LoginRejected, LoginUnavailable, verify_routeros_user,
)
from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIError

ROOT = Path(__file__).resolve().parents[1]


class FakeRouterOS:
    plans = {}
    connected = []

    def __init__(self, host, username, password, port=8728, timeout=4.5):
        self.host = host
        self.username = username
        self.password = password

    def __enter__(self):
        self.connected.append(self.host)
        problem, _rows = self.plans[self.host]
        if problem is not None:
            raise problem
        return self

    def __exit__(self, *args):
        return None

    def print(self, path, params=None):
        assert path == "/user"
        assert params == {".proplist": "name,group,disabled"}
        return self.plans[self.host][1]


def cases(plans, user="evstegneev.na", password="correct"):
    FakeRouterOS.plans = plans
    FakeRouterOS.connected = []
    return verify_routeros_user(
        user, password, list(plans), client_factory=FakeRouterOS
    )


def must_reject(plans, expected_type, expected_part, *, user="evstegneev.na", password="correct"):
    try:
        cases(plans, user, password)
    except expected_type as exc:
        assert expected_part.casefold() in str(exc).casefold(), (exc, expected_part)
    else:
        raise AssertionError("Invalid credentials unexpectedly opened Helper")


def main() -> None:
    full = [{"name": "evstegneev.na", "group": "full", "disabled": "false"}]
    valid = cases({"vpn01": (None, full)})
    assert (valid.username, valid.group, valid.server) == ("evstegneev.na", "full", "vpn01")

    # Successful API login is insufficient without a matching /user record.
    must_reject({"vpn01": (None, [])}, LoginRejected, "не найден")
    must_reject(
        {"vpn01": (None, [{"name": "another", "group": "full", "disabled": "false"}])},
        LoginRejected, "не найден"
    )
    must_reject(
        {"vpn01": (None, [{"name": "evstegneev.na", "group": "full", "disabled": "true"}])},
        LoginRejected, "отключена"
    )
    must_reject(
        {"vpn01": (None, [{"name": "evstegneev.na", "group": "read", "disabled": "false"}])},
        LoginRejected, "full"
    )

    # Explicit invalid credentials must NOT fail over to another RouterOS
    # with a potentially different account database.
    must_reject(
        {"vpn01": (RouterOSAPIError("invalid user name or password"), []),
         "vpn02": (None, full)},
        LoginRejected, "логин или пароль"
    )
    assert FakeRouterOS.connected == ["vpn01"]

    # API /user rejection is also not an offline/network failure.
    class DeniedUserList(FakeRouterOS):
        def print(self, path, params=None):
            raise RouterOSAPIError("not enough permissions")

    try:
        verify_routeros_user(
            "evstegneev.na", "correct", ["vpn01", "vpn02"],
            client_factory=DeniedUserList,
        )
    except LoginRejected as exc:
        assert "/user" in str(exc)
    else:
        raise AssertionError("/user permission failure was ignored")

    # Actual connectivity failures may try another known VPN server.
    found = cases({
        "vpn01": (ConnectionRefusedError("offline"), []),
        "vpn02": (None, full),
    })
    assert found.server == "vpn02"
    assert FakeRouterOS.connected == ["vpn01", "vpn02"]

    must_reject(
        {"vpn01": (TimeoutError("offline"), []), "vpn02": (OSError("offline"), [])},
        LoginUnavailable, "связаться"
    )
    must_reject({"vpn01": (None, full)}, LoginRejected, "введите", password="")

    # Authenticate BEFORE MainWindow and never accept a login by checking only
    # non-empty fields or pre-existing QSettings credentials.
    app = (ROOT / "linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")
    form = (ROOT / "linkvideo_vpn_helper/ui/login_window.py").read_text(encoding="utf-8")
    assert "login.exec()" in app
    assert "saved_username and saved_password" not in app
    assert app.index("login.exec()") < app.index("window = MainWindow(service, credentials, settings)")
    assert "verify_routeros_user(user, password, hosts)" in form
    assert form.index("verify_routeros_user(user, password, hosts)") < form.index("self.accept()")
    assert form.index("self.settings.setValue(\"password\", password)") > form.index("def _on_verified")
    assert "QTimer.singleShot(0, self._auto_verify_saved)" in form
    assert 'self._verification_signals.finished.connect(self._on_verified)' in form

    print("CORE TESTS: ROUTEROS VERIFIED LOGIN GATE OK")


if __name__ == "__main__":
    main()
