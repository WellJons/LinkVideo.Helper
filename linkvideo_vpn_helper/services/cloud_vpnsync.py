from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

import requests


DEFAULT_CLOUD_HOST = "192.168.88.141"
DEFAULT_CLOUD_PORT = 8787


class CloudConnectionError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi_protect(value: str) -> str:
    raw = str(value or "").encode("utf-8")
    if not raw:
        return ""
    if sys.platform != "win32":
        # Development-only fallback. Production Helper is Windows and uses DPAPI.
        return "fallback:" + base64.urlsafe_b64encode(raw).decode("ascii")
    buffer = ctypes.create_string_buffer(raw)
    in_blob = _DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    out_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "LinkVideo.Helper VPNSync",
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return "dpapi:" + base64.urlsafe_b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if text.startswith("fallback:"):
        try:
            return base64.urlsafe_b64decode(text[9:].encode("ascii")).decode("utf-8")
        except Exception:
            return ""
    if not text.startswith("dpapi:") or sys.platform != "win32":
        return ""
    try:
        encrypted = base64.urlsafe_b64decode(text[6:].encode("ascii"))
        buffer = ctypes.create_string_buffer(encrypted)
        in_blob = _DataBlob(len(encrypted), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        out_blob = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        CRYPTPROTECT_UI_FORBIDDEN = 0x1
        if not crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(out_blob),
        ):
            return ""
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception:
        return ""


@dataclass
class CloudServerConfig:
    host: str = DEFAULT_CLOUD_HOST
    port: int = DEFAULT_CLOUD_PORT
    username: str = ""
    password: str = ""
    use_tls: bool = False
    remember: bool = True

    @property
    def base_url(self) -> str:
        host = str(self.host or "").strip().rstrip("/")
        if host.startswith("http://"):
            host = host[7:]
        elif host.startswith("https://"):
            host = host[8:]
        scheme = "https" if self.use_tls else "http"
        return f"{scheme}://{host}:{int(self.port)}"

    def validate(self) -> None:
        if not str(self.host or "").strip():
            raise ValueError("Укажите IP-адрес или доменное имя облачного сервера")
        if not (1 <= int(self.port) <= 65535):
            raise ValueError("Некорректный порт облачного сервера")
        if not str(self.username or "").strip():
            raise ValueError("Укажите логин облачного сервера")
        if not str(self.password or ""):
            raise ValueError("Укажите пароль облачного сервера")


class CloudConfigStore:
    PREFIX = "cloud/vpnsync"

    def __init__(self, settings) -> None:
        self.settings = settings

    def load(self) -> CloudServerConfig:
        remember = bool(self.settings.value(f"{self.PREFIX}/remember", True, bool))
        encrypted = str(self.settings.value(f"{self.PREFIX}/password_dpapi", "", str) or "")
        password = _dpapi_unprotect(encrypted) if remember else ""
        return CloudServerConfig(
            host=str(self.settings.value(f"{self.PREFIX}/host", DEFAULT_CLOUD_HOST, str) or DEFAULT_CLOUD_HOST),
            port=int(self.settings.value(f"{self.PREFIX}/port", DEFAULT_CLOUD_PORT, int) or DEFAULT_CLOUD_PORT),
            username=str(self.settings.value(f"{self.PREFIX}/username", "", str) or ""),
            password=password,
            use_tls=bool(self.settings.value(f"{self.PREFIX}/use_tls", False, bool)),
            remember=remember,
        )

    def save(self, config: CloudServerConfig) -> None:
        config.validate()
        self.settings.setValue(f"{self.PREFIX}/host", str(config.host).strip())
        self.settings.setValue(f"{self.PREFIX}/port", int(config.port))
        self.settings.setValue(f"{self.PREFIX}/username", str(config.username).strip())
        self.settings.setValue(f"{self.PREFIX}/use_tls", bool(config.use_tls))
        self.settings.setValue(f"{self.PREFIX}/remember", bool(config.remember))
        if config.remember:
            self.settings.setValue(f"{self.PREFIX}/password_dpapi", _dpapi_protect(config.password))
        else:
            self.settings.remove(f"{self.PREFIX}/password_dpapi")
        self.settings.sync()

    def clear_password(self) -> None:
        self.settings.remove(f"{self.PREFIX}/password_dpapi")
        self.settings.sync()


class CloudVPNSyncClient:
    def __init__(self, settings, *, timeout: float = 8.0) -> None:
        self.store = CloudConfigStore(settings)
        self.timeout = max(2.0, float(timeout))
        self._config = self.store.load()
        self._token = ""
        self._token_expires_at = 0.0
        self._session = requests.Session()

    @property
    def config(self) -> CloudServerConfig:
        return self._config

    def set_config(self, config: CloudServerConfig, *, save: bool = True) -> None:
        config.validate()
        if save:
            self.store.save(config)
        self._config = config
        self._token = ""
        self._token_expires_at = 0.0

    def _url(self, path: str) -> str:
        return self._config.base_url + "/" + str(path or "").lstrip("/")

    def health(self) -> dict[str, Any]:
        try:
            response = self._session.get(self._url("/health"), timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise CloudConnectionError(f"Облачный сервер недоступен: {exc}") from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise CloudConnectionError("Облачный сервер вернул некорректный health-ответ")
        return payload

    def login(self) -> dict[str, Any]:
        self._config.validate()
        try:
            response = self._session.post(
                self._url("/v1/auth/login"),
                json={"username": self._config.username, "password": self._config.password},
                timeout=self.timeout,
            )
        except Exception as exc:
            raise CloudConnectionError(f"Не удалось подключиться к облачному серверу: {exc}") from exc
        if response.status_code == 401:
            raise CloudConnectionError("Неверный логин или пароль облачного сервера")
        try:
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise CloudConnectionError(f"Ошибка авторизации облачного сервера: {exc}") from exc
        token = str(payload.get("access_token") or "")
        if not token:
            raise CloudConnectionError("Облачный сервер не выдал сессионный токен")
        expires_in = max(300, int(payload.get("expires_in") or 3600))
        self._token = token
        self._token_expires_at = time.time() + expires_in - min(60, expires_in // 10)
        return payload

    def _ensure_token(self) -> None:
        if not self._token or time.time() >= self._token_expires_at:
            self.login()

    def request(self, method: str, path: str, **kwargs) -> Any:
        self._ensure_token()
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {self._token}"
        try:
            response = self._session.request(
                str(method).upper(),
                self._url(path),
                headers=headers,
                timeout=kwargs.pop("timeout", self.timeout),
                **kwargs,
            )
            if response.status_code == 401:
                self._token = ""
                self._ensure_token()
                headers["Authorization"] = f"Bearer {self._token}"
                response = self._session.request(
                    str(method).upper(),
                    self._url(path),
                    headers=headers,
                    timeout=kwargs.pop("timeout", self.timeout),
                    **kwargs,
                )
            response.raise_for_status()
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
        except CloudConnectionError:
            raise
        except Exception as exc:
            raise CloudConnectionError(f"Ошибка запроса к облачному серверу: {exc}") from exc

    def test_connection(self) -> dict[str, Any]:
        health = self.health()
        login = self.login()
        me = self.request("GET", "/v1/auth/me")
        return {"health": health, "login": login, "me": me}

    def servers(self) -> list[dict[str, Any]]:
        payload = self.request("GET", "/v1/servers")
        return list(payload or [])

    def search_clients(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        payload = self.request("GET", "/v1/clients/search", params={"q": query, "limit": int(limit)})
        return list(payload or [])

    def activity(self, limit: int = 200, *, login: str = "", source: str = "") -> list[dict[str, Any]]:
        payload = self.request(
            "GET",
            "/v1/activity",
            params={"limit": int(limit), "login": login, "source": source},
        )
        return list(payload or [])
