from __future__ import annotations

import json
import re
from dataclasses import dataclass

from PySide6.QtCore import QSettings


@dataclass(slots=True, frozen=True)
class VPNServer:
    host: str
    country: str
    builtin: bool = False
    enabled: bool = True

    @property
    def display_name(self) -> str:
        return f"{self.host} · {self.country}"


class ServerRegistry:
    """Хранилище VPN-серверов.

    В 2.0 встроены только актуальные российские vpn01-vpn10, а также
    переданные пользователем региональные rb-vpn01 и kz-vpn01. Дополнительные
    серверы сохраняются через QSettings и переживают обновление программы.
    """

    SETTINGS_KEY = "vpn_servers/custom_v2"
    DISABLED_KEY = "vpn_servers/disabled_v2"

    def __init__(self, settings: QSettings):
        self.settings = settings

    @staticmethod
    def defaults() -> list[VPNServer]:
        result = [VPNServer(f"vpn{i:02d}.linkvideo.ru", "Россия", True) for i in range(1, 11)]
        result.extend([
            VPNServer("rb-vpn01.linkvideo.ru", "Беларусь", True),
            VPNServer("kz-vpn01.linkvideo.ru", "Казахстан", True),
        ])
        return result

    @staticmethod
    def detect_country(host: str) -> str:
        value = str(host or "").strip().lower()
        if value.startswith("kz-"):
            return "Казахстан"
        if value.startswith("rb-") or value.startswith("by-"):
            return "Беларусь"
        return "Россия"

    @staticmethod
    def normalize_host(host: str) -> str:
        value = str(host or "").strip().lower().rstrip(".")
        for prefix in ("http://", "https://"):
            if value.startswith(prefix):
                value = value[len(prefix):]
        return value.split("/", 1)[0].strip()

    @classmethod
    def validate_host(cls, host: str) -> str:
        value = cls.normalize_host(host)
        if not value:
            raise ValueError("Введите адрес VPN-сервера")
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", value):
            raise ValueError("Некорректное DNS-имя VPN-сервера")
        if "." not in value:
            raise ValueError("Укажите полное DNS-имя сервера")
        return value

    def _disabled(self) -> set[str]:
        raw = str(self.settings.value(self.DISABLED_KEY, "", str) or "").strip()
        if not raw:
            return set()
        try:
            data = json.loads(raw)
            return {self.normalize_host(x) for x in data if self.normalize_host(x)}
        except Exception:
            return set()

    def _save_disabled(self, values: set[str]) -> None:
        self.settings.setValue(self.DISABLED_KEY, json.dumps(sorted(values), ensure_ascii=False))

    def custom(self) -> list[VPNServer]:
        raw = str(self.settings.value(self.SETTINGS_KEY, "", str) or "").strip()
        if not raw:
            return []
        try:
            items = json.loads(raw)
        except Exception:
            return []
        disabled = self._disabled()
        result: list[VPNServer] = []
        seen: set[str] = set()
        for item in items if isinstance(items, list) else []:
            if isinstance(item, str):
                host = self.normalize_host(item)
                country = self.detect_country(host)
            elif isinstance(item, dict):
                host = self.normalize_host(item.get("host", ""))
                country = str(item.get("country") or self.detect_country(host)).strip()
            else:
                continue
            if not host or host in seen:
                continue
            seen.add(host)
            result.append(VPNServer(host, country or self.detect_country(host), False, host not in disabled))
        return result

    def all(self, include_disabled: bool = True) -> list[VPNServer]:
        disabled = self._disabled()
        result = [VPNServer(x.host, x.country, True, x.host not in disabled) for x in self.defaults()]
        seen = {x.host for x in result}
        for server in self.custom():
            if server.host not in seen:
                result.append(server)
                seen.add(server.host)
        if include_disabled:
            return result
        return [x for x in result if x.enabled]

    def hosts(self) -> list[str]:
        return [x.host for x in self.all(include_disabled=False)]

    def get(self, host: str) -> VPNServer:
        value = self.normalize_host(host)
        for item in self.all():
            if item.host == value:
                return item
        return VPNServer(value, self.detect_country(value), False, value not in self._disabled())

    def add(self, host: str, country: str | None = None) -> VPNServer:
        value = self.validate_host(host)
        existing = {x.host: x for x in self.all()}
        if value in existing:
            self.set_enabled(value, True)
            return self.get(value)
        server = VPNServer(value, (country or self.detect_country(value)).strip(), False, True)
        items = self.custom() + [server]
        self._save(items)
        return server

    def remove(self, host: str) -> bool:
        value = self.normalize_host(host)
        if value in {x.host for x in self.defaults()}:
            return False
        old = self.custom()
        new = [x for x in old if x.host != value]
        if len(new) == len(old):
            return False
        self._save(new)
        disabled = self._disabled()
        disabled.discard(value)
        self._save_disabled(disabled)
        return True

    def set_enabled(self, host: str, enabled: bool) -> None:
        value = self.normalize_host(host)
        disabled = self._disabled()
        if enabled:
            disabled.discard(value)
        else:
            disabled.add(value)
        self._save_disabled(disabled)

    def _save(self, items: list[VPNServer]) -> None:
        payload = [{"host": x.host, "country": x.country} for x in items if not x.builtin]
        self.settings.setValue(self.SETTINGS_KEY, json.dumps(payload, ensure_ascii=False))
