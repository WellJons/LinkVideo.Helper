from __future__ import annotations

"""Friendly Google service-account setup for the VPN Sheets mirror."""

import json
import os
import shutil
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QPushButton


_INSTALLED = False
_EXTRA_PAGE_PATCHED = False


def _valid_service_account(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    required = ("client_email", "private_key", "token_uri")
    if str(payload.get("type", "") or "").strip() != "service_account":
        raise ValueError("Выбранный JSON не является Google Service Account")
    if not all(str(payload.get(name, "") or "").strip() for name in required):
        raise ValueError("В JSON отсутствуют обязательные поля Google Service Account")
    return payload


def _install_selected_key(coordinator, selected: str) -> tuple[bool, str]:
    source = Path(str(selected or "").strip())
    if not source.is_file():
        return False, "Файл ключа не найден"
    try:
        payload = _valid_service_account(source)
    except Exception as exc:
        return False, str(exc)

    # Store the operator-selected key in the current Windows user's AppData.
    # This avoids requiring elevation to write ProgramData and avoids keeping the
    # credential in Downloads/Desktop where it can be accidentally removed.
    app_data = str(os.getenv("APPDATA", "") or "").strip()
    destination = source
    if app_data:
        try:
            target_dir = Path(app_data) / "LinkVideo" / "Helper"
            target_dir.mkdir(parents=True, exist_ok=True)
            destination = target_dir / "google_sheets_service_account.json"
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
        except Exception:
            destination = source

    try:
        coordinator.settings.setValue("sheets/service_account_file", str(destination))
        coordinator.settings.sync()
    except Exception:
        pass

    try:
        from linkvideo_vpn_helper.services.vpn_sheets_sync import GoogleSheetsBackend, VPNSheetsSyncService
        backend = GoogleSheetsBackend.from_settings(coordinator.settings)
        if backend is None:
            return False, "Ключ сохранён, но Helper не смог его открыть"
        coordinator.backend = backend
        coordinator.sync_service = VPNSheetsSyncService(coordinator.vpn_service, backend)
        email = str(payload.get("client_email", "") or "").strip()
        backend.source_path = str(destination)
        backend.service_account_email = email
        return True, f"Google Sheets подключён · {email}"
    except Exception as exc:
        return False, f"Не удалось подключить Google Sheets: {exc}"


def install_vpn_sheets_key_ui() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    import linkvideo_vpn_helper.ui.vpn_sheets_sync_integration as integration

    integration.VPNSyncCoordinator.config_hint = staticmethod(lambda: (
        "Ключ Google Sheets не найден. Helper ищет Service Account JSON автоматически "
        "в папках LinkVideo.Helper/LinkVideo\\Helper или можно выбрать файл кнопкой «Выбрать JSON»."
    ))

    original_page_patch = integration._patch_vpn_servers_page

    def enhanced_page_patch():
        global _EXTRA_PAGE_PATCHED
        original_page_patch()
        if _EXTRA_PAGE_PATCHED:
            return

        from linkvideo_vpn_helper.ui.pages.vpn_servers_page import VPNServersPage
        original_build = VPNServersPage._build

        def enhanced_build(self):
            original_build(self)
            coordinator = integration._COORDINATOR
            sync_btn = getattr(self, "sheets_sync_btn", None)
            status = getattr(self, "sheets_sync_status", None)
            if coordinator is None or sync_btn is None or status is None:
                return

            container = sync_btn.parentWidget()
            layout = container.layout() if container is not None else None
            if layout is None:
                return

            key_btn = QPushButton("Сменить ключ" if coordinator.is_configured() else "Выбрать JSON")
            key_btn.setProperty("role", "ghost")

            def describe_key():
                backend = getattr(coordinator, "backend", None)
                if backend is None:
                    status.setText(coordinator.config_hint())
                    key_btn.setText("Выбрать JSON")
                    return
                email = str(
                    getattr(backend, "service_account_email", "")
                    or getattr(backend, "service_account_info", {}).get("client_email", "")
                    or "Service Account"
                )
                source = str(getattr(backend, "source_path", "") or "").strip()
                filename = Path(source).name if source else "JSON подключён"
                status.setText(
                    f"Google Sheets подключён · {email} · {filename} · автосверка каждые 5 минут"
                )
                key_btn.setText("Сменить ключ")

            def choose_key():
                selected, _ = QFileDialog.getOpenFileName(
                    self,
                    "Выберите Google Service Account JSON",
                    "",
                    "Google Service Account (*.json);;JSON (*.json)",
                )
                if not selected:
                    return
                ok, message = _install_selected_key(coordinator, selected)
                if ok:
                    describe_key()
                else:
                    status.setText(message)

            key_btn.clicked.connect(choose_key)
            # Put credential setup before the primary Sync action.
            try:
                layout.insertWidget(max(1, layout.count() - 1), key_btn)
            except Exception:
                layout.addWidget(key_btn)
            self.sheets_key_btn = key_btn
            describe_key()

        VPNServersPage._build = enhanced_build
        _EXTRA_PAGE_PATCHED = True

    integration._patch_vpn_servers_page = enhanced_page_patch
    _INSTALLED = True
