from __future__ import annotations

"""Ensure CRM HAR summaries cannot leak credentials or customer identifiers."""

import importlib.util
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "scripts/inspect_billing_har.py"
spec = importlib.util.spec_from_file_location("inspect_billing_har", path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

har = {"log": {"entries": [
    {
        "_resourceType": "fetch",
        "request": {
            "method": "GET",
            "url": "https://crm.linkvideo.ru/api/v1/tariffs/89143397169?token=super_secret&client=private",
            "headers": [{"name": "authorization", "value": "Bearer super_secret"}],
        },
        "response": {"status": 200, "content": {"mimeType": "application/json", "text": "private billing data"}},
    },
    {
        "_resourceType": "xhr",
        "request": {
            "method": "POST",
            "url": "https://crm.linkvideo.ru/api/v2/clients/evstegneev.na/update",
            "postData": {"text": "mypassword"},
        },
        "response": {"status": 403, "content": {"mimeType": "application/json"}},
    },
    {
        "_resourceType": "fetch",
        "request": {"method": "GET", "url": "https://other.example/api/v1/tariffs/89143397169"},
        "response": {"status": 200, "content": {"mimeType": "application/json"}},
    },
    {
        "_resourceType": "fetch",
        "request": {"method": "GET", "url": "https://crm.linkvideo.ru/assets/js/chunk.js"},
        "response": {"status": 200, "content": {"mimeType": "text/javascript"}},
    },
]}}
results = module.summarize(har)
assert sum(results.values()) == 2
output = "\n".join(str(x) for x in results)
assert "GET" in output and "POST" in output
assert "/api/v1/tariffs/:value" in output
assert "/api/v2/clients/:value/:value" in output
for secret in ("super_secret", "89143397169", "evstegneev", "mypassword", "private"):
    assert secret not in output
assert module.safe_route("/api/v1/subscriptions/abcXYZ").endswith("/:value")
print("CORE TESTS BILLING HAR REDACTION OK")
