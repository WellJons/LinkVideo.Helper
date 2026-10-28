from __future__ import annotations

"""CRM token GET transport must be safe before live header format is known."""

from linkvideo_vpn_helper.services.billing_gateway import (
    BillingNotConfigured, BillingPermissionDenied, BillingValidationError,
)
from linkvideo_vpn_helper.services.billing_token_transport import BillingTokenTransport


class DummyResponse:
    def __init__(self, code=200, body=None):
        self.status_code = code
        self.body = {"success": True, "response": []} if body is None else body

    def json(self):
        return self.body


class DummySession:
    def __init__(self, code=200):
        self.code = code
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return DummyResponse(self.code)

    def close(self):
        self.closed = True


session = DummySession()
transport = BillingTokenTransport("fake-token", _session=session)
assert not transport.ready
try:
    transport.get_json("/api/accounts/725")
    raise AssertionError("No validated header mode must be fail-closed")
except BillingNotConfigured:
    pass
assert not session.calls
assert "fake-token" not in repr(transport)

transport.header_mode = "token"
assert transport.ready
assert transport.get_json("/api/accounts/725")["success"]
assert session.calls[0][0] == "https://api-billing-global.linkvideo.ru/api/accounts/725"
assert session.calls[0][1]["headers"] == {
    "Accept": "application/json", "token": "fake-token",
}
assert session.calls[0][1]["allow_redirects"] is False
assert session.calls[0][1]["timeout"] == 8

transport.header_mode = "authorization_bearer"
transport.get_json("/api/tariffs/list/0")
assert session.calls[-1][1]["headers"]["Authorization"] == "Bearer fake-token"

transport.header_mode = "authorization_raw"
transport.get_json("/api/cameras/archive-depth/1")
assert session.calls[-1][1]["headers"]["Authorization"] == "fake-token"

for invalid in (
    "/api/accounts/../../auth/login", "/auth/login", "/api/accounts/725?x=1",
    "https://attacker.test", "//attacker.test/hello",
    "/api/tariffs/create/1", "/api/accounts/725/cameras/delete",
    "/api/accounts/search/%2Fadmin", "/api/accounts/search/%252Fadmin",
):
    before = len(session.calls)
    try:
        transport.get_json(invalid)
        raise AssertionError("Unexpected remote path accepted")
    except BillingValidationError:
        pass
    assert len(session.calls) == before

transport.header_mode = "unverified_header"
try:
    transport.get_json("/api/accounts/725")
    raise AssertionError("Unverified header mode accepted")
except BillingNotConfigured:
    pass
transport.header_mode = "authorization_raw"

# No manual header setup needed: try the token header first. On a
# 401 alone, another known same-origin authentication header may be tried.
class HeaderNegotiationSession(DummySession):
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        headers = kwargs.get("headers", {})
        if "token" in headers:
            return DummyResponse(401)
        if headers.get("Authorization", "").startswith("Bearer "):
            return DummyResponse(200)
        return DummyResponse(401)

negotiation = HeaderNegotiationSession()
auto = BillingTokenTransport("fake-token", header_mode="auto", _session=negotiation)
assert auto.ready
assert auto.get_json("/api/accounts/725")["success"] is True
assert auto.header_mode == "authorization_bearer"
assert len(negotiation.calls) == 2
auto.get_json("/api/accounts/725/cameras")
assert len(negotiation.calls) == 3
assert negotiation.calls[-1][1]["headers"]["Authorization"] == "Bearer fake-token"
auto.close()

# 403 must not trigger fallback: it may be a genuine permission denial.
denied = DummySession(code=403)
no_retry = BillingTokenTransport("fake-token", header_mode="auto", _session=denied)
try:
    no_retry.get_json("/api/accounts/725")
    raise AssertionError("Permission denial ignored")
except BillingPermissionDenied:
    pass
assert len(denied.calls) == 1
no_retry.close()

session.code = 401
try:
    transport.get_json("/api/accounts/725/cameras")
    raise AssertionError("Expired token response accepted")
except BillingPermissionDenied:
    pass

transport.close()
assert not transport.ready
assert not transport.token and session.closed

for bad_token in ("", "bad\r\nvalue"):
    try:
        BillingTokenTransport(bad_token, header_mode="token", _session=DummySession())
        raise AssertionError("Invalid token accepted")
    except BillingValidationError:
        pass

print("CORE TESTS BILLING TOKEN GET TRANSPORT FAIL-CLOSED OK")
