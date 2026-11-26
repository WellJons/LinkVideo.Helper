"""Offline dependency regression: no production connections, no real credentials."""
from __future__ import annotations

import base64
import json
import os
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch


def _deny(*args, **kwargs):
    raise AssertionError("Network disabled in runtime self-test")


def _imports():
    # Keep explicit imports so the frozen build checks the same lazy service paths
    # as the real adapters, not just worker startup.
    from linkvideo_vpn_helper.services import (
        archive_service, archive_service_core, archive_diagnosis_engine,
        billing_auth_contract, billing_login_probe, billing_token_transport,
        billing_editable_gateway, billing_tariff_filter, billing_catalog,
        billing_gateway, support_templates, camera_lookup, b2o_camera_preview,
        vpn_camera_port_match, vpn_address_allocator, vpn_automation_service,
        agent_service, vpn_background_service, vpn_backup_service,
        vpn_restore_service, cloud_vpnsync, vpn_sheets_sync, camera_diagnostics,
        camera_stream_format, b2o_rtsp_probe, camera_live_probe,
        agent_b2o_preview, b2o_client_admin,
    )
    from desktop_v4 import archive_report, archive_command, template_migration, backup_inventory
    return support_templates.TemplateStore, vpn_sheets_sync.GoogleSheetsBackend


def _decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _google_signing(backend_type):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from google.auth.transport.requests import Request
    from urllib.parse import parse_qs

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode("ascii")
    info = {"type": "service_account", "client_email": "offline@example.invalid",
            "private_key": pem, "private_key_id": "offline",
            "token_uri": "https://oauth2.googleapis.com/token", "project_id": "offline"}
    calls = []

    class Response:
        status = 200
        data = b'{"access_token":"offline-token","expires_in":3600,"token_type":"Bearer"}'
        headers = {}

    def fake_request(self, url, method="GET", body=None, headers=None, **kwargs):
        assert url == info["token_uri"] and method == "POST"
        assertion = parse_qs(body.decode() if isinstance(body, bytes) else body)["assertion"][0]
        head, claims, signature = assertion.split(".")
        key.public_key().verify(_decode(signature), (head + "." + claims).encode("ascii"),
                                padding.PKCS1v15(), hashes.SHA256())
        assert json.loads(_decode(claims))["iss"] == info["client_email"]
        calls.append(True)
        return Response()

    with patch.object(Request, "__call__", fake_request):
        backend = backend_type(info, spreadsheet_id="offline")
        assert backend._access_token() == "offline-token"
        assert backend._access_token() == "offline-token"
        assert len(calls) == 1


def run(build):
    # All disk writes are confined to a temporary profile. Importing services and
    # building the workspace do not authenticate or contact any live server.
    with tempfile.TemporaryDirectory(prefix="lv4-runtime-check-") as tmp:
        with patch.dict(os.environ, {"LV4_DATA_DIR": tmp}), \
                patch.object(socket.socket, "connect", _deny), \
                patch.object(socket.socket, "connect_ex", _deny), \
                patch("socket.getaddrinfo", _deny):
            TemplateStore, GoogleSheetsBackend = _imports()
            workspace = build()
            assert workspace.dispatch("status", {})["authenticated"] is False
            registry = workspace.registry
            registry.add("offline.example.invalid", "Казахстан")
            registry.set_enabled("offline.example.invalid", False)
            assert any(r.host == "offline.example.invalid" and not r.enabled for r in registry.all())
            store = TemplateStore(Path(tmp) / "templates")
            ident = store.save("Проверка", "Тестовый ответ", "Прочее")
            assert store.get(ident).body == "Тестовый ответ"
            _google_signing(GoogleSheetsBackend)
    print("LV4_RUNTIME_SELFTEST OK: lazy services, settings, SQLite, Google RSA; network blocked")
    return 0
