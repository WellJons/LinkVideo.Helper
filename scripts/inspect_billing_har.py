from __future__ import annotations

"""Summarize a DevTools HAR without exposing credentials or customer records.

Usage:
    python scripts/inspect_billing_har.py path/to/capture.har

The input HAR is never copied/uploaded. Prints ONLY a bounded set of request
methods, allowlisted route segments, status codes and counts. No request body,
cookies, headers, query parameters, tokens, usernames or raw URLs are printed.
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_HOST = "crm.linkvideo.ru"
# Unknown path segments are data: redact even if they *look* harmless.
ROUTE_PARTS = frozenset({
    "api", "v1", "v2", "v3", "v4", "auth", "login", "logout",
    "me", "current", "session", "sessions", "refresh", "profile",
    "accounts", "account", "users", "user", "clients", "client",
    "customers", "customer", "contracts", "contract", "tariffs",
    "tariff", "plans", "plan", "prices", "price", "billing",
    "invoices", "invoice", "payments", "payment", "subscriptions",
    "subscription", "cameras", "camera", "devices", "device",
    "history", "search", "filter", "list", "details", "detail",
    "info", "balance", "status", "settings", "organizations",
    "organization", "companies", "company", "graphql", "gql",
    "get", "all", "getall", "get-all", "update", "check",
})
STATIC_SUFFIXES = (
    ".js", ".css", ".png", ".jpg", ".jpeg", ".webp",
    ".svg", ".woff", ".woff2", ".ico", ".map", ".ttf",
)


def safe_route(path: str) -> str:
    components = [segment for segment in path.split("/") if segment]
    result = []
    for component in components[:8]:
        text = component.lower()
        result.append(text if text in ROUTE_PARTS else ":value")
    if len(components) > 8:
        result.append("…")
    return "/" + "/".join(result)


def summarize(data: dict, host: str = DEFAULT_HOST) -> Counter:
    output = Counter()
    entries = data.get("log", {}).get("entries", [])
    if not isinstance(entries, list):
        raise ValueError("Expected HAR log.entries list")
    for entry in entries[:50000]:
        if not isinstance(entry, dict):
            continue
        request = entry.get("request") or {}
        response = entry.get("response") or {}
        if not isinstance(request, dict) or not isinstance(response, dict):
            continue
        url = request.get("url", "")
        if not isinstance(url, str):
            continue
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != host:
            continue
        if parsed.path.lower().endswith(STATIC_SUFFIXES):
            continue
        method = str(request.get("method", "")).upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            continue
        resource_type = str(entry.get("_resourceType", "")).lower()
        mime = str((response.get("content") or {}).get("mimeType", "")).lower()
        # Include requests with API-looking routes even if the browser omitted
        # the proprietary _resourceType field.
        is_api = resource_type in {"xhr", "fetch"} or "/api/" in parsed.path.lower() or "json" in mime or method != "GET"
        if not is_api:
            continue
        raw_status = response.get("status", 0)
        try:
            status = int(raw_status)
        except (TypeError, ValueError):
            status = 0
        status = status if 100 <= status <= 599 else 0
        output[(method, safe_route(parsed.path), status)] += 1
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("har", type=Path, help="Local HAR export. Never upload the raw HAR.")
    args = parser.parse_args()
    with args.har.open("r", encoding="utf-8-sig") as src:
        data = json.load(src)
    counts = summarize(data)
    print("Billing request map (sanitized, no headers, query or bodies):")
    if not counts:
        print("No matching HTTPS fetch/XHR requests for crm.linkvideo.ru.")
    for (method, route, status), amount in sorted(counts.items()):
        print(f"{method:6} {route:55} {status or '-':>3} ×{amount}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
