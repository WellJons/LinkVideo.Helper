from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import QSettings

from linkvideo_vpn_helper.services.errors import OperationCancelled


@dataclass(slots=True)
class ArchiveCamera:
    camera_id: str
    label: str
    server: str
    stream_name: str
    signature: str
    timezone_offset: int
    raw: dict = field(default_factory=dict)
    candidate_hosts: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ArchiveSlice:
    host: str
    app: str
    stream: str
    start: float
    end: float
    signature: str
    source: str = "main"

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(slots=True)
class ArchiveGap:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(slots=True)
class ReserveTransferEvent:
    server_from: str
    server_to: str
    start: float | None
    end: float | None
    status: str = ""
    status_description: str = ""
    return_status: int = 0

    def overlaps(self, start_ts: float, end_ts: float, margin: float = 0.0) -> bool:
        left = self.start if self.start is not None else float("-inf")
        right = self.end if self.end is not None else float("inf")
        return left <= end_ts + margin and right >= start_ts - margin


@dataclass(slots=True)
class ArchiveDiscovery:
    camera: ArchiveCamera
    requested_start: float
    requested_end: float
    slices: list[ArchiveSlice]
    gaps: list[ArchiveGap]
    checked_hosts: list[str]
    errors: list[str]
    reserve_events: list[ReserveTransferEvent] = field(default_factory=list)
    hls_fallback_url: str = ""
    hls_fallback_duration: float = 0.0
    hls_fallback_segments: int = 0
    hls_fallback_host: str = ""
    hls_fallback_method: str = ""
    hls_fallback_hosts: list[str] = field(default_factory=list)

    @property
    def requested_duration(self) -> float:
        return max(0.0, self.requested_end - self.requested_start)

    @property
    def covered_duration(self) -> float:
        if self.slices:
            return sum(x.duration for x in self.slices)
        return max(0.0, float(self.hls_fallback_duration or 0.0))

    @property
    def has_downloadable_archive(self) -> bool:
        return bool(self.slices or self.hls_fallback_url)

    @property
    def coverage_percent(self) -> float:
        return (self.covered_duration / self.requested_duration * 100.0) if self.requested_duration else 0.0


@dataclass(slots=True)
class ArchiveDownloadResult:
    output: Path
    downloaded_slices: list[ArchiveSlice]
    failed_slices: list[ArchiveSlice]
    errors: list[str]
    direct_download_duration: float = 0.0

    @property
    def downloaded_duration(self) -> float:
        if self.direct_download_duration > 0:
            return self.direct_download_duration
        return sum(x.duration for x in self.downloaded_slices)

    @property
    def failed_duration(self) -> float:
        return sum(x.duration for x in self.failed_slices)

    @property
    def partial(self) -> bool:
        return bool(self.failed_slices)


class B2OService:
    LOGIN_URL = "https://api.b2o.goodline.info/ords/mobile/vc2/auth/login"

    # Операторы LinkVideo фиксированы по стране. Эти значения не являются
    # пользовательской настройкой и не должны редактироваться вручную.
    OPERATOR_PROFILES = {
        241: {
            "country": "Россия",
            "camera_prefix": "linkvideo_",
            "cluster": "linkvideo",
            "archive_servers": [],
        },
        1721: {
            "country": "Казахстан",
            "camera_prefix": "linkvideokz_",
            "cluster": "linkvideokz",
            "archive_servers": [
                "b2o-cold-reserve-59",
                "kz-vcore01.video.goodline.info",
                "kz-vcoreA.video.goodline.info",
            ],
        },
        1741: {
            "country": "Беларусь",
            "camera_prefix": "linkvideoby_",
            "cluster": "linkvideoby",
            "archive_servers": [
                "rb-vcore01.video.goodline.info",
                "rb-agent-vcore01.video.goodline.info",
                "rb-agent-test-vcore01.video.goodline.info",
            ],
        },
    }

    # Веб-страница/логи reserve-transfers отображают локальные даты в UTC+7.
    # Это НЕ часовой пояс камеры. Сравнение всегда делаем через epoch.
    RESERVE_LOG_TZ_OFFSET = 7
    CAMERA_URL = "https://api.b2o.goodline.info/ords/mobile/vc2/operators/{operator}/cameras/{camera}"
    CAMERAS_URL = "https://api.b2o.goodline.info/ords/mobile/vc2/operators/{operator}/cameras"
    SERVERS_URL = "https://api.b2o.goodline.info/ords/mobile/vc2/operators/{operator}/servers"
    DVR_SERVERS_URL = "https://api.b2o.goodline.info/ords/mobile/vc2/operators/{operator}/dvr-servers"
    MEDIA_SERVERS_URL = "https://api.b2o.goodline.info/ords/mobile/vc2/operators/{operator}/media-servers"
    RESERVE_URL = "https://api.b2o.goodline.info/ords/mobile/vc2/admin/reserve-transfers"

    def __init__(self, settings: QSettings):
        self.settings = settings
        self._dvr_servers_memory: dict[int, tuple[float, list[str]]] = {}

    def token(self) -> str:
        return str(self.settings.value("b2o/token", "", str) or "").strip()

    def login_name(self) -> str:
        return str(self.settings.value("b2o/login", "", str) or "").strip()

    def login(self, login: str, password: str) -> str:
        device = str(self.settings.value("b2o/device_id", "", str) or "").strip()
        if not device:
            device = f"LVHELPER-{int(time.time())}"
            self.settings.setValue("b2o/device_id", device)
        payload = json.dumps({"login": login, "password": password, "id_device": device, "id_platform": 3}).encode("utf-8")
        req = urllib.request.Request(
            self.LOGIN_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://admin.b2o.goodline.info",
                "Referer": "https://admin.b2o.goodline.info/login",
                "User-Agent": "LinkVideo.Helper/2.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"B2O: HTTP {exc.code} при авторизации") from exc
        token = str(data.get("token") or "").strip()
        if not token:
            raise RuntimeError("B2O не вернул token")
        self.settings.setValue("b2o/login", login.strip())
        self.settings.setValue("b2o/token", token)
        return token

    def clear_token(self):
        self.settings.remove("b2o/token")

    @staticmethod
    def normalize_vcore_host(value: str) -> str:
        host = str(value or "").strip().lower().replace("http://", "").replace("https://", "").strip("/")
        # B2O admin sometimes exposes a short server name (for example
        # b2o-cold-reserve-59). For known archive prefixes Helper normalizes it
        # to the same DNS suffix used by vcore hosts.
        if "." not in host and re.fullmatch(r"(?:b2o|mass|kz|rb)-[a-z0-9_-]+", host, re.I):
            host += ".video.goodline.info"
        return host

    def request_json(self, url: str, token: str, headers: dict | None = None, timeout: int = 25) -> dict:
        h = {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://operator.b2o.goodline.info",
            "Referer": "https://operator.b2o.goodline.info/cameras/1",
            "User-Agent": "LinkVideo.Helper/2.0",
            "Token": token,
        }
        if headers:
            h.update({str(k): str(v) for k, v in headers.items()})
        req = urllib.request.Request(url, headers=h, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                self.clear_token()
                raise RuntimeError("Авторизация B2O истекла. Выполните вход заново.") from exc
            raise RuntimeError(f"B2O: HTTP {exc.code} при запросе данных") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"B2O недоступен: {exc}") from exc

    @classmethod
    def valid_operator_id(cls, value, default: int = 241) -> int:
        try:
            operator_id = int(value)
        except Exception:
            operator_id = int(default)
        return operator_id if operator_id in cls.OPERATOR_PROFILES else int(default)

    @classmethod
    def operator_country(cls, operator_id: int) -> str:
        operator_id = cls.valid_operator_id(operator_id)
        return str(cls.OPERATOR_PROFILES[operator_id]["country"])

    @classmethod
    def operator_prefix(cls, operator_id: int) -> str:
        operator_id = cls.valid_operator_id(operator_id)
        return str(cls.OPERATOR_PROFILES[operator_id]["camera_prefix"])

    @classmethod
    def operator_cluster(cls, operator_id: int) -> str:
        operator_id = cls.valid_operator_id(operator_id)
        return str(cls.OPERATOR_PROFILES[operator_id].get("cluster") or "")

    @classmethod
    def _extract_dvr_server_hosts(cls, payload) -> list[str]:
        """Извлекает DVR/Nimble host из ответа вкладки «Медиа серверы».

        API разных операторов может возвращать немного разную структуру JSON,
        поэтому парсер намеренно не привязан к одному имени поля.
        """
        found: list[str] = []
        seen: set[str] = set()

        def add(value: str, key_hint: str = ""):
            raw = str(value or "").strip()
            if not raw:
                return
            low = raw.lower().replace("http://", "").replace("https://", "").strip("/")
            low = low.split("/", 1)[0].split(":", 1)[0].strip()
            hint = str(key_hint or "").lower()
            likely_key = any(part in hint for part in ("server", "host", "domain", "dns", "url", "name"))
            likely_value = any(part in low for part in ("vcore", "reserve", "agent"))
            if not (likely_key or likely_value):
                return
            host = cls.normalize_vcore_host(low)
            if not host.endswith(".video.goodline.info"):
                return
            if not any(part in host for part in ("vcore", "reserve", "agent")):
                return
            if host not in seen:
                seen.add(host)
                found.append(host)

        def walk(node, key_hint: str = ""):
            if isinstance(node, dict):
                for key, value in node.items():
                    if isinstance(value, (dict, list)):
                        walk(value, str(key))
                    elif isinstance(value, str):
                        add(value, str(key))
            elif isinstance(node, list):
                for value in node:
                    walk(value, key_hint)
            elif isinstance(node, str):
                add(node, key_hint)

        walk(payload)
        return found

    def _cached_dvr_servers(self, operator_id: int) -> tuple[float, list[str]]:
        operator_id = self.valid_operator_id(operator_id)
        if operator_id in self._dvr_servers_memory:
            return self._dvr_servers_memory[operator_id]
        raw = str(self.settings.value(f"archive/dvr_servers_cache/{operator_id}", "", str) or "").strip()
        if raw:
            try:
                data = json.loads(raw)
                stamp = float(data.get("timestamp") or 0.0)
                items = [
                    self.normalize_vcore_host(x)
                    for x in (data.get("items") or [])
                    if self.normalize_vcore_host(x).endswith(".video.goodline.info")
                ]
                result = (stamp, list(dict.fromkeys(items)))
                self._dvr_servers_memory[operator_id] = result
                return result
            except Exception:
                pass
        return 0.0, []

    def cached_dvr_servers(self, operator_id: int) -> list[str]:
        return list(self._cached_dvr_servers(operator_id)[1])

    def dvr_servers(self, operator_id: int, *, force: bool = False) -> list[str]:
        """Актуальный внутренний реестр media/DVR серверов B2O.

        В 3.0.3 список больше не показывается сотруднику и не является основным
        способом поиска архива. Он нужен только как короткий аварийный fallback.
        Helper сначала пробует общий список ``/servers`` (его использует раздел
        серверов операторской части), затем старые ``dvr-servers`` и
        ``media-servers``. Ответы объединяются и кэшируются.
        """
        operator_id = self.valid_operator_id(operator_id)
        stamp, cached = self._cached_dvr_servers(operator_id)
        if not force and cached and (time.time() - stamp) < 900:
            return list(cached)

        token = self.token()
        if not token:
            return list(cached)

        found: list[str] = []
        last_error: Exception | None = None
        # /servers соответствует общей серверной инвентаризации оператора и
        # поэтому имеет приоритет. Старые endpoints нужны только если он не
        # доступен на конкретной версии B2O.
        for template in (self.SERVERS_URL, self.DVR_SERVERS_URL, self.MEDIA_SERVERS_URL):
            try:
                payload = self.request_json(
                    template.format(operator=int(operator_id)),
                    token,
                    headers={"Count": "500", "Page": "1"},
                    timeout=8,
                )
                current = self._extract_dvr_server_hosts(payload)
                if current:
                    found = list(dict.fromkeys(current))
                    break
            except Exception as exc:
                last_error = exc
                continue

        if found:
            stamp = time.time()
            data = {"timestamp": stamp, "items": found}
            self.settings.setValue(
                f"archive/dvr_servers_cache/{operator_id}",
                json.dumps(data, ensure_ascii=False),
            )
            self._dvr_servers_memory[operator_id] = (stamp, found)
            return list(found)
        if force and last_error is not None and not cached:
            raise RuntimeError(f"B2O не вернул список серверов: {last_error}")
        return list(cached)

    @classmethod
    def builtin_archive_servers(cls, operator_id: int) -> list[str]:
        operator_id = cls.valid_operator_id(operator_id)
        result: list[str] = []
        for host in cls.OPERATOR_PROFILES[operator_id].get("archive_servers") or []:
            clean = cls.normalize_vcore_host(str(host))
            if clean and clean not in result:
                result.append(clean)
        return result

    def custom_archive_servers(self, operator_id: int) -> list[str]:
        operator_id = self.valid_operator_id(operator_id)
        raw = str(self.settings.value(f"archive/custom_servers/{operator_id}", "", str) or "").strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except Exception:
            data = []
        result: list[str] = []
        for host in data if isinstance(data, list) else []:
            clean = self.normalize_vcore_host(str(host))
            if clean and clean.endswith(".video.goodline.info") and clean not in result:
                result.append(clean)
        return result

    def archive_servers(self, operator_id: int, *, online: bool = False, force: bool = False) -> list[str]:
        result: list[str] = []
        remote = self.dvr_servers(operator_id, force=force) if online else self._cached_dvr_servers(operator_id)[1]
        for host in list(remote) + self.builtin_archive_servers(operator_id) + self.custom_archive_servers(operator_id):
            clean = self.normalize_vcore_host(host)
            if clean and clean not in result:
                result.append(clean)
        return result

    def add_custom_archive_server(self, operator_id: int, host: str) -> str:
        operator_id = self.valid_operator_id(operator_id)
        clean = self.normalize_vcore_host(host)
        if not clean or not clean.endswith(".video.goodline.info"):
            raise ValueError("Введите имя архивного сервера, например kz-vcore02.video.goodline.info")
        if clean in self.builtin_archive_servers(operator_id):
            return clean
        items = self.custom_archive_servers(operator_id)
        if clean not in items:
            items.append(clean)
            self.settings.setValue(f"archive/custom_servers/{operator_id}", json.dumps(items, ensure_ascii=False))
        return clean

    def remove_custom_archive_server(self, operator_id: int, host: str) -> None:
        operator_id = self.valid_operator_id(operator_id)
        clean = self.normalize_vcore_host(host)
        items = [x for x in self.custom_archive_servers(operator_id) if x != clean]
        self.settings.setValue(f"archive/custom_servers/{operator_id}", json.dumps(items, ensure_ascii=False))

    @classmethod
    def detect_operator_id(cls, value: str, default: int = 241) -> int:
        raw = str(value or "").strip().lower()
        if raw.startswith("linkvideokz_"):
            return 1721
        if raw.startswith("linkvideoby_"):
            return 1741
        if raw.startswith("linkvideo_"):
            return 241
        return cls.valid_operator_id(default)

    @classmethod
    def normalize_camera_id(cls, value: str, operator_id: int = 241) -> tuple[str, str]:
        raw = str(value or "").strip()
        m = re.search(r"(\d+)", raw)
        if not m:
            raise ValueError(
                "Введите ID камеры, например 207728, linkvideo_207728, "
                "linkvideokz_268527 или linkvideoby_268552"
            )
        number = m.group(1)
        resolved = cls.detect_operator_id(raw, operator_id)
        return number, f"{cls.operator_prefix(resolved)}{number}"

    @classmethod
    def resolve_operator_id(cls, camera_id: str, selected_operator_id: int = 241) -> int:
        return cls.detect_operator_id(camera_id, selected_operator_id)

    def camera(self, operator_id: int, camera_id: str) -> ArchiveCamera:
        operator_id = self.resolve_operator_id(camera_id, operator_id)
        number, label = self.normalize_camera_id(camera_id, operator_id)
        token = self.token()
        if not token:
            raise RuntimeError("Требуется авторизация B2O")
        data = self.request_json(self.CAMERA_URL.format(operator=int(operator_id), camera=number), token)
        if isinstance(data.get("data"), dict):
            data = dict(data["data"])
        elif isinstance(data.get("data"), list) and data["data"]:
            data = dict(data["data"][0])
        server = self.normalize_vcore_host(data.get("server_name") or data.get("server") or "")
        stream = str(data.get("main_stream_name") or data.get("nimble_id") or label).strip()
        signature = str(data.get("signature") or data.get("wmsAuthSign") or "").strip()
        try:
            tz = int(data.get("timezone") or data.get("time_zone") or 7)
        except Exception:
            tz = 7
        if not server:
            raise RuntimeError("B2O не вернул server_name камеры")
        if not signature:
            raise RuntimeError("B2O не вернул signature/wmsAuthSign")
        # B2O-ответы разных версий могут содержать дополнительные vcore в
        # полях истории/резерва. Извлекаем их без предположений о структуре.
        raw_text = json.dumps(data, ensure_ascii=False)
        candidates = []
        # Collect any B2O/Nimble host returned by the operator API, including
        # regional kz-/rb- clusters and agent/cold-reserve servers.
        for host in re.findall(r"[a-z0-9][a-z0-9_.-]*\.video\.goodline\.info", raw_text, re.I):
            host = self.normalize_vcore_host(host)
            if host and host not in candidates:
                candidates.append(host)
        for host in re.findall(r"(?:b2o|mass|kz|rb)-[a-z0-9_-]+", raw_text, re.I):
            host = self.normalize_vcore_host(host)
            if host and host not in candidates:
                candidates.append(host)
        if server not in candidates:
            candidates.insert(0, server)
        return ArchiveCamera(number, label, server, stream, signature, tz, dict(data), candidates)

    def cameras_on_server(self, operator_id: int, server_name: str, limit: int = 15) -> tuple[list[dict], dict]:
        """Возвращает онлайн-камеры текущего B2O vcore для массового сравнения.

        Это диагностический список, а не полный экспорт камер. B2O Search может
        вернуть лишние записи, поэтому server_name дополнительно проверяется
        точным сравнением на стороне Helper.
        """
        token = self.token()
        if not token:
            raise RuntimeError("Требуется авторизация B2O")
        clean_server = self.normalize_vcore_host(server_name)
        result: list[dict] = []
        debug = {"pages": 0, "found_total": 0, "online_same_server": 0, "errors": []}
        seen: set[str] = set()
        count = 50
        max_pages = 6
        limit = max(1, min(30, int(limit or 15)))

        for page in range(1, max_pages + 1):
            try:
                data = self.request_json(
                    self.CAMERAS_URL.format(operator=int(operator_id)),
                    token,
                    {
                        "Count": str(count),
                        "Page": str(page),
                        "Search": clean_server,
                        "Show_deleted_cameras": "0",
                    },
                    20,
                )
            except Exception as exc:
                debug["errors"].append(f"page={page}: {exc}")
                break

            items = data.get("data") or []
            if not isinstance(items, list):
                items = []
            debug["pages"] = page
            try:
                debug["found_total"] = int(data.get("total") or data.get("count") or len(items))
            except Exception:
                debug["found_total"] = len(items)

            for cam in items:
                if not isinstance(cam, dict):
                    continue
                server = self.normalize_vcore_host(cam.get("server_name") or "")
                if server != clean_server:
                    continue
                status = str(cam.get("camera_status") or "").strip().lower()
                if status and status != "online":
                    continue
                camera_id = str(cam.get("id_camera") or "").strip()
                if not camera_id or camera_id in seen:
                    continue
                seen.add(camera_id)
                result.append(dict(cam))
                if len(result) >= limit:
                    debug["online_same_server"] = len(result)
                    return result, debug
            if len(items) < count:
                break

        debug["online_same_server"] = len(result)
        return result, debug

    @classmethod
    def _parse_reserve_dt(cls, value: str, default_tz_hours: int | None = None) -> float | None:
        if not value:
            return None
        text = str(value).strip()
        if default_tz_hours is None:
            default_tz_hours = cls.RESERVE_LOG_TZ_OFFSET
        try:
            if text.endswith("Z") and "T" in text:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
        for fmt in ("%d.%m.%Y/%H:%M:%S", "%d.%m.%Y %H:%M:%S"):
            try:
                dt = datetime.strptime(text, fmt).replace(tzinfo=timezone(timedelta(hours=default_tz_hours)))
                return dt.timestamp()
            except Exception:
                pass
        return None

    def reserve_transfers(self, primary_server: str, start_ts: float, end_ts: float, max_pages: int = 6) -> list[ReserveTransferEvent]:
        """Возвращает события переезда/резерва, пересекающие выбранный период.

        Важное правило времени:
        * start_ts/end_ts уже являются абсолютным временем (epoch), полученным из
          локального времени камеры с её timezone_offset;
        * строковые даты страницы reserve-transfers без явного offset всегда
          интерпретируются как UTC+7, потому что именно так их показывает B2O admin.

        Пример: камера UTC+3, запрос 05:00–05:05 => epoch 02:00–02:05 UTC.
        Переезд в 05:03 камеры будет в admin как 09:03 UTC+7 — это тот же epoch.
        """
        token = self.token()
        if not token:
            return []
        primary = self.normalize_vcore_host(primary_server)
        events: list[ReserveTransferEvent] = []
        seen_ids: set[str] = set()
        # Страницы B2O не дают надёжного server-side фильтра по периоду, поэтому
        # оставляем разумный запас вокруг окна, как в проверенной логике 1.1.2.
        window_start, window_end = start_ts - 12 * 3600, end_ts + 12 * 3600

        for return_status in (1, 0):
            for page in range(1, max_pages + 1):
                headers = {
                    "Origin": "https://admin.b2o.goodline.info",
                    "Referer": "https://admin.b2o.goodline.info/reserve-transfers",
                    "count": "50",
                    "page": str(page),
                    "return_status": str(return_status),
                    "token": token,
                }
                try:
                    payload = self.request_json(self.RESERVE_URL, token, headers, 15)
                except Exception:
                    break
                rows = payload.get("data") or []
                if not rows:
                    break

                for report in rows:
                    for group in report.get("groups") or []:
                        group_from = self.normalize_vcore_host((group.get("server_from") or {}).get("name") or "")
                        if group_from != primary:
                            continue
                        for transfer in group.get("transfers") or []:
                            transfer_id = str(transfer.get("id_reserve_transfer") or "")
                            if transfer_id and transfer_id in seen_ids:
                                continue
                            if transfer_id:
                                seen_ids.add(transfer_id)

                            server_from = self.normalize_vcore_host(
                                ((transfer.get("server_from") or group.get("server_from") or {}).get("name") or primary)
                            )
                            server_to = self.normalize_vcore_host((transfer.get("server_to") or {}).get("name") or "")
                            if not server_to:
                                continue
                            start = self._parse_reserve_dt(
                                transfer.get("start_dt") or group.get("start_date") or report.get("start_date"),
                                self.RESERVE_LOG_TZ_OFFSET,
                            )
                            end = self._parse_reserve_dt(
                                transfer.get("update_dt") or report.get("update_dt"),
                                self.RESERVE_LOG_TZ_OFFSET,
                            )
                            # Незавершённый перенос считается продолжающимся до текущего времени.
                            effective_end = end if end is not None else time.time()
                            if start is not None and not (start <= window_end and effective_end >= window_start):
                                continue
                            events.append(ReserveTransferEvent(
                                server_from=server_from or primary,
                                server_to=server_to,
                                start=start,
                                end=end,
                                status=str(transfer.get("status") or ""),
                                status_description=str(
                                    transfer.get("status_description")
                                    or group.get("status_description")
                                    or report.get("status_description")
                                    or ""
                                ),
                                return_status=int(return_status),
                            ))

                total = int(payload.get("total") or 0)
                count = int(payload.get("count") or 50)
                if page * count >= total:
                    break

        events.sort(key=lambda x: (x.start is None, x.start or 0.0, x.server_to))
        return events

    def reserve_hosts(self, primary_server: str, start_ts: float, end_ts: float, max_pages: int = 6) -> list[str]:
        found: list[str] = []
        for event in self.reserve_transfers(primary_server, start_ts, end_ts, max_pages):
            for host in (event.server_from, event.server_to):
                host = self.normalize_vcore_host(host)
                if host and host != self.normalize_vcore_host(primary_server) and host not in found:
                    found.append(host)
        return found



class ArchiveService:
    def __init__(self, settings: QSettings):
        self.settings = settings
        self._dvr_servers_memory: dict[int, tuple[float, list[str]]] = {}
        self.b2o = B2OService(settings)

    def _history_hosts(self, camera_id: str) -> list[str]:
        key = f"archive/host_history/{str(camera_id or '').strip()}"
        raw = str(self.settings.value(key, "", str) or "").strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except Exception:
            return []
        result = []
        for host in data if isinstance(data, list) else []:
            host = str(host or "").strip().lower()
            if host and host.endswith(".video.goodline.info") and host not in result:
                result.append(host)
        return result[:12]

    def _remember_history_hosts(self, camera_id: str, hosts: list[str]) -> None:
        key = f"archive/host_history/{str(camera_id or '').strip()}"
        merged = []
        for host in list(hosts or []) + self._history_hosts(camera_id):
            host = str(host or "").strip().lower()
            if host and host.endswith(".video.goodline.info") and host not in merged:
                merged.append(host)
        self.settings.setValue(key, json.dumps(merged[:12], ensure_ascii=False))

    def _global_history_hosts(self, operator_id: int = 241) -> list[str]:
        operator_id = self.b2o.valid_operator_id(operator_id)
        raw = str(self.settings.value(f"archive/global_host_history_v3/{operator_id}", "", str) or "").strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except Exception:
            return []
        result: list[str] = []
        for host in data if isinstance(data, list) else []:
            host = self.b2o.normalize_vcore_host(host)
            if host.endswith(".video.goodline.info") and host not in result:
                result.append(host)
        return result[:120]

    def _remember_global_hosts(self, operator_id: int, hosts: list[str]) -> None:
        operator_id = self.b2o.valid_operator_id(operator_id)
        merged: list[str] = []
        for host in list(hosts or []) + self._global_history_hosts(operator_id):
            host = self.b2o.normalize_vcore_host(host)
            if host.endswith(".video.goodline.info") and host not in merged:
                merged.append(host)
        self.settings.setValue(f"archive/global_host_history_v3/{operator_id}", json.dumps(merged[:120], ensure_ascii=False))

    def _deep_candidate_hosts(self, primary_server: str, operator_id: int = 241, camera_id: str = "") -> list[str]:
        """Короткий fallback-пул без перебора выдуманных vcore1..500.

        Приоритет: история именно этой камеры -> реальные серверы из B2O ->
        глобальная история. Даже если оператор вернёт сотни серверов, Helper не
        превращает короткий запрос архива в полный сетевой скан всего парка.
        """
        primary = self.b2o.normalize_vcore_host(primary_server)
        result: list[str] = []
        seen: set[str] = {primary} if primary else set()

        def add(host: str):
            host = self.b2o.normalize_vcore_host(host)
            if host and host.endswith(".video.goodline.info") and host not in seen:
                seen.add(host)
                result.append(host)

        operator_id = self.b2o.valid_operator_id(operator_id)
        for host in self._history_hosts(camera_id):
            add(host)
        for host in self._global_history_hosts(operator_id):
            add(host)

        # Реестр /servers может быть большим. Он используется только как
        # аварийный хвост после player playlist и reserve-transfer.
        try:
            remote = self.b2o.archive_servers(operator_id, online=True)
        except Exception:
            remote = self.b2o.archive_servers(operator_id, online=False)
        for host in remote:
            add(host)

        # 72 параллельных кандидата — уже достаточно широкий fallback, но это
        # не 500+ последовательных vcore как в старой реализации.
        return result[:72]

    @staticmethod
    def _stream_candidates(stream_name: str, camera: dict) -> list[tuple[str, str]]:
        values: list[str] = []
        for value in (stream_name, camera.get("main_stream_name"), camera.get("stream_name"), camera.get("nimble_id")):
            value = str(value or "").strip().strip("/")
            if value and value not in values:
                values.append(value)
        result: list[tuple[str, str]] = []
        def add(app: str, stream: str):
            if app and stream and (app, stream) not in result:
                result.append((app, stream))
        for value in values:
            if "/" in value:
                app, stream = value.split("/", 1); add(app, stream); continue
            if value.endswith("_main") or value.endswith("_sub"):
                add("vcrf", value); add("main", value.rsplit("_", 1)[0])
            else:
                add("main", value); add("vcrf", f"{value}_main"); add("sub", value); add("vcrf", f"{value}_sub")
        return result

    @staticmethod
    def _parse_any_ts(value) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            v = float(value)
            if v > 10_000_000_000:
                v /= 1000.0
            return v if v > 1_000_000_000 else None
        raw = str(value).strip()
        if not raw:
            return None
        m = re.search(r"\d{13}", raw)
        if m: return int(m.group(0)) / 1000.0
        m = re.search(r"\d{10}", raw)
        if m: return float(int(m.group(0)))
        try: return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except Exception: return None

    def _parse_dvr_timeline(self, raw: str, start_ts: float, end_ts: float) -> list[tuple[float, float]]:
        intervals: list[tuple[float, float]] = []
        def value_by_keys(d: dict, keys: tuple[str, ...]):
            low = {str(k).lower(): v for k, v in d.items()}
            for key in keys:
                if key in d and d[key] is not None: return d[key]
                if key.lower() in low and low[key.lower()] is not None: return low[key.lower()]
            return None
        def add(start_v=None, end_v=None, dur_v=None):
            s = self._parse_any_ts(start_v); e = self._parse_any_ts(end_v)
            try: dur = float(str(dur_v).replace(",", ".")) if dur_v is not None else None
            except Exception: dur = None
            if dur and dur > 864000: dur /= 1000.0
            if s is None and e is not None and dur: s=e-dur
            if e is None and s is not None and dur: e=s+dur
            if s is None or e is None or e <= s: return
            if e <= start_ts or s >= end_ts: return
            intervals.append((max(start_ts,s), min(end_ts,e)))
        def walk(obj):
            if isinstance(obj, dict):
                sv=value_by_keys(obj,("start","start_time","startTime","begin","from","timestamp","time"))
                ev=value_by_keys(obj,("end","end_time","endTime","stop","to"))
                dv=value_by_keys(obj,("duration","dur","length","len"))
                if sv is not None and (ev is not None or dv is not None): add(sv,ev,dv)
                for v in obj.values(): walk(v)
            elif isinstance(obj,list):
                for v in obj: walk(v)
        try: walk(json.loads(raw))
        except Exception: pass
        intervals.sort(); merged: list[list[float]]=[]
        for s,e in intervals:
            if not merged or s > merged[-1][1]+1: merged.append([s,e])
            else: merged[-1][1]=max(merged[-1][1],e)
        return [(a,b) for a,b in merged]

    @staticmethod
    def _http_text(url: str, timeout: int = 15) -> str:
        req=urllib.request.Request(url,headers={"User-Agent":"LinkVideo.Helper/2.0"})
        with urllib.request.urlopen(req,timeout=timeout) as response:
            return response.read().decode("utf-8","replace")

    @staticmethod
    def _hls_headers() -> dict[str, str]:
        return {
            "User-Agent": "LinkVideo.Helper/2.1",
            "Origin": "https://test-desktop-player-b2o.elct.ru",
            "Referer": "https://test-desktop-player-b2o.elct.ru/",
        }

    @classmethod
    def _http_hls_text(cls, url: str, timeout: int = 20) -> str:
        req = urllib.request.Request(url, headers=cls._hls_headers())
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")

    @staticmethod
    def _parse_hls_duration(raw: str) -> tuple[int, float]:
        durations = []
        for match in re.finditer(r"#EXTINF:([0-9]+(?:[\.,][0-9]+)?)", str(raw or ""), re.I):
            try:
                durations.append(float(match.group(1).replace(",", ".")))
            except Exception:
                pass
        return len(durations), sum(durations)

    @staticmethod
    def _first_nested_playlist(raw: str, base_url: str) -> str:
        for line in str(raw or "").splitlines():
            value = line.strip()
            if value and not value.startswith("#") and ".m3u8" in value.lower():
                return urllib.parse.urljoin(base_url, value)
        return ""

    @staticmethod
    def _parse_playlist_datetime(value: str) -> float | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None

    @classmethod
    def _parse_hls_segments(cls, raw: str, playlist_url: str, assumed_start: float) -> list[dict]:
        """Разбирает тот же chunks playlist, который получает web-player.

        Важная часть — host берётся из реальной URI каждого сегмента, а не из
        текущего ``server_name`` камеры. Поэтому после переезда плейлист сам
        показывает, на каком vcore/mass-vcore лежала запись в выбранное время.
        """
        segments: list[dict] = []
        pending_duration: float | None = None
        pending_program_time: float | None = None
        cursor = float(assumed_start)
        base_host = (urllib.parse.urlparse(playlist_url).hostname or "").lower()
        for raw_line in str(raw or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.upper().startswith("#EXTINF:"):
                m = re.search(r"#EXTINF:([0-9]+(?:[\.,][0-9]+)?)", line, re.I)
                try:
                    pending_duration = float(m.group(1).replace(",", ".")) if m else 0.0
                except Exception:
                    pending_duration = 0.0
                continue
            if line.upper().startswith("#EXT-X-PROGRAM-DATE-TIME:"):
                pending_program_time = cls._parse_playlist_datetime(line.split(":", 1)[1])
                continue
            if line.startswith("#"):
                continue
            # master playlist URI — это не медиасегмент
            if ".m3u8" in line.lower():
                continue
            if pending_duration is None:
                continue
            url = urllib.parse.urljoin(playlist_url, line)
            host = (urllib.parse.urlparse(url).hostname or base_host or "").lower()
            start = float(pending_program_time if pending_program_time is not None else cursor)
            end = start + max(0.0, float(pending_duration or 0.0))
            segments.append({"url": url, "host": host, "start": start, "end": end, "duration": max(0.0, end-start)})
            cursor = end
            pending_program_time = None
            pending_duration = None
        return segments

    @staticmethod
    def _slices_from_hls_segments(segments: list[dict], app: str, stream: str, signature: str, source: str = "player") -> list[ArchiveSlice]:
        out: list[ArchiveSlice] = []
        for seg in segments:
            host = str(seg.get("host") or "").strip().lower()
            start = float(seg.get("start") or 0.0)
            end = float(seg.get("end") or 0.0)
            if not host or end <= start:
                continue
            if out and out[-1].host == host and start <= out[-1].end + 1.5:
                out[-1].end = max(out[-1].end, end)
            else:
                out.append(ArchiveSlice(host, app, stream, start, end, signature, source))
        return out

    def _probe_hls_host(self, host: str, camera: ArchiveCamera, start_ts: float, end_ts: float, timeout: int = 8) -> tuple[dict | None, list[str]]:
        """Проверяет архив через player playlist и извлекает реальный DVR host.

        Это основной путь 3.0.3. Обычно нужен один master + один nested запрос,
        вместо сканирования сотен vcore через management port 8086.
        """
        clean = str(host or "").replace("http://", "").replace("https://", "").strip("/")
        errors: list[str] = []
        for app, stream in self._stream_candidates(camera.stream_name, camera.raw)[:3]:
            fake = ArchiveSlice(clean, app, stream, start_ts, end_ts, camera.signature, "player")
            master_url = self.playlist_url(fake)
            try:
                master = self._http_hls_text(master_url, timeout)
                selected_raw = master
                selected_url = master_url
                method = "player playlist"
                nested_url = self._first_nested_playlist(master, master_url)
                if nested_url:
                    nested = self._http_hls_text(nested_url, timeout)
                    m_count, m_total = self._parse_hls_duration(master)
                    n_count, n_total = self._parse_hls_duration(nested)
                    if n_count >= m_count and n_total >= m_total:
                        selected_raw = nested
                        selected_url = nested_url
                        method = "player playlist → chunks"
                segments = self._parse_hls_segments(selected_raw, selected_url, start_ts)
                if not segments:
                    errors.append(f"{clean}: player playlist пуст для {app}/{stream}")
                    continue
                slices = self._slices_from_hls_segments(segments, app, stream, camera.signature, "player")
                hosts_count: dict[str, int] = {}
                for seg in segments:
                    h = str(seg.get("host") or "").strip().lower()
                    if h:
                        hosts_count[h] = hosts_count.get(h, 0) + 1
                hosts = [h for h, _count in sorted(hosts_count.items(), key=lambda item: (-item[1], item[0]))]
                dominant = hosts[0] if hosts else clean
                total = sum(float(seg.get("duration") or 0.0) for seg in segments)
                return {
                    "host": dominant,
                    "hosts": hosts,
                    "app": app,
                    "stream": stream,
                    "url": selected_url,
                    "master_url": master_url,
                    "resolved_url": selected_url,
                    "segments": len(segments),
                    "duration": min(float(end_ts - start_ts), float(total)),
                    "method": method,
                    "slices": slices,
                }, errors
            except Exception as exc:
                errors.append(f"{clean}: HLS {app}/{stream}: {exc}")
        return None, errors

    def _probe_host(self, host: str, camera: ArchiveCamera, start_ts: float, end_ts: float, source: str) -> tuple[list[ArchiveSlice], list[str]]:
        errors=[]
        clean = str(host or "").replace("http://","").replace("https://","").strip("/")
        for app, stream in self._stream_candidates(camera.stream_name, camera.raw):
            url=f"http://{clean}:8086/manage/dvr_status/{app}/{stream}?timeline=true"
            try:
                raw=self._http_text(url,15)
                intervals=self._parse_dvr_timeline(raw,start_ts,end_ts)
                if intervals:
                    return [ArchiveSlice(clean,app,stream,s,e,camera.signature,source) for s,e in intervals],errors
                errors.append(f"{clean}: timeline пуст для {app}/{stream}")
            except Exception as exc:
                errors.append(f"{clean}: {app}/{stream}: {exc}")
        return [],errors

    def _probe_host_fast(self, host: str, camera: ArchiveCamera, start_ts: float, end_ts: float, source: str = "deep") -> list[ArchiveSlice]:
        """Укороченная проверка одного неизвестного vcore для глубокого поиска."""
        clean = str(host or "").replace("http://", "").replace("https://", "").strip("/")
        # Сначала наиболее вероятные варианты. На неизвестных хостах не делаем
        # четыре длинных попытки по 15 секунд.
        candidates = self._stream_candidates(camera.stream_name, camera.raw)[:2]
        for app, stream in candidates:
            url = f"http://{clean}:8086/manage/dvr_status/{app}/{stream}?timeline=true"
            try:
                raw = self._http_text(url, 4)
            except Exception:
                continue
            intervals = self._parse_dvr_timeline(raw, start_ts, end_ts)
            if intervals:
                return [ArchiveSlice(clean, app, stream, s, e, camera.signature, source) for s, e in intervals]
        return []

    def _deep_search_missing(
        self,
        camera: ArchiveCamera,
        start_ts: float,
        end_ts: float,
        known_slices: list[ArchiveSlice],
        already_checked: set[str],
        progress: Callable[[str, str], None] | None = None,
        cancel_event=None,
    ) -> tuple[list[ArchiveSlice], list[str], int]:
        """Последний fallback только по реально известным B2O серверам.

        Проверяем HTTPS player playlist параллельно. Management :8086 и полный
        перебор b2o-vcore1..500 здесь больше не используются.
        """
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("Операция отменена пользователем")
        candidates: list[str] = []
        seen = {self.b2o.normalize_vcore_host(x) for x in already_checked}
        # Сначала дополнительные hosts, которые B2O уже вернул прямо в карточке камеры.
        for host in list(camera.candidate_hosts or []):
            clean = self.b2o.normalize_vcore_host(host)
            if clean and clean not in seen:
                seen.add(clean)
                candidates.append(clean)
        for host in self._deep_candidate_hosts(camera.server, self.b2o.resolve_operator_id(camera.label, 241), camera.label):
            clean = self.b2o.normalize_vcore_host(host)
            if clean and clean not in seen:
                seen.add(clean)
                candidates.append(clean)
        if not candidates:
            return [], [], 0

        found: list[ArchiveSlice] = []
        checked_hosts: list[str] = []
        total = len(candidates)
        checked_count = 0
        workers = min(24, max(1, total))
        if progress:
            progress("Проверяю резервные DVR", f"Плейлист не закрыл весь период · кандидатов из B2O: {total}")
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="archive-player-fallback") as pool:
            futures = {
                pool.submit(self._probe_hls_host, host, camera, start_ts, end_ts, 5): host
                for host in candidates
            }
            for future in as_completed(futures):
                if cancel_event is not None and cancel_event.is_set():
                    for item in futures:
                        item.cancel()
                    raise OperationCancelled("Операция отменена пользователем")
                host = futures[future]
                checked_count += 1
                checked_hosts.append(host)
                try:
                    info, _errs = future.result()
                except Exception:
                    info = None
                if info:
                    found.extend(info.get("slices") or [])
                    learned = list(info.get("hosts") or [])
                    if learned:
                        self._remember_global_hosts(self.b2o.resolve_operator_id(camera.label, 241), learned)
                current_plan = self._build_plan(list(known_slices) + found, start_ts, end_ts)
                if current_plan and not self._gaps(current_plan, start_ts, end_ts):
                    for item in futures:
                        item.cancel()
                    break
                if progress and (checked_count % 8 == 0 or info):
                    progress("Проверяю резервные DVR", f"Проверено {checked_count}/{total}" + (f" · найдено на {info.get('host')}" if info else ""))
        return found, checked_hosts, checked_count

    @staticmethod
    def _local_to_epoch(dt: datetime, offset: int) -> float:
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=timezone(timedelta(hours=int(offset))))
        return dt.timestamp()

    def diagnose_epoch(self, camera_id: str, operator_id: int, start_ts: float, end_ts: float, *, fast: bool = False, cancel_event=None) -> ArchiveDiscovery:
        """Быстрая проверка камеры в уже известном абсолютном временном окне.

        Используется для корреляции с другими камерами. Не запускает глубокий
        перебор vcore и reserve-поиск для каждой сравниваемой камеры, иначе одна
        диагностика могла бы превратиться в сотни сетевых запросов.
        """
        if end_ts <= start_ts:
            raise ValueError("Конец периода должен быть позже начала")
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("Операция отменена пользователем")
        camera = self.b2o.camera(operator_id, camera_id)
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("Операция отменена пользователем")
        if fast:
            slices = self._probe_host_fast(camera.server, camera, start_ts, end_ts, "compare")
            errors = [] if slices else [f"{camera.server}: быстрый DVR timeline не подтвердил архив"]
        else:
            slices, errors = self._probe_host(camera.server, camera, start_ts, end_ts, "compare")
        plan = self._build_plan(slices, start_ts, end_ts)
        gaps = self._gaps(plan, start_ts, end_ts)
        return ArchiveDiscovery(
            camera=camera,
            requested_start=float(start_ts),
            requested_end=float(end_ts),
            slices=plan,
            gaps=gaps,
            checked_hosts=[camera.server],
            errors=errors,
            reserve_events=[],
        )

    def discover(self, camera_id: str, operator_id: int, start_local: datetime, end_local: datetime, progress: Callable[[str, str], None] | None = None, cancel_event=None) -> ArchiveDiscovery:
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("Операция отменена пользователем")
        if progress:
            progress("Получаю данные камеры", "Запрашиваю B2O…")
        camera = self.b2o.camera(operator_id, camera_id)
        start_ts = self._local_to_epoch(start_local, camera.timezone_offset)
        end_ts = self._local_to_epoch(end_local, camera.timezone_offset)
        if end_ts <= start_ts:
            raise ValueError("Конец периода должен быть позже начала")

        errors: list[str] = []
        checked: list[str] = []
        operator_id = self.b2o.resolve_operator_id(camera.label, operator_id)

        # 1. Главный быстрый путь: ровно тот playlist_dvr_range, который
        # используется плеером. В реальном chunks playlist URI сегментов сами
        # раскрывают фактический DVR host за выбранное время.
        if progress:
            progress("Определяю DVR по плейлисту", f"{camera.server} · выбранный период")
        player_info, player_errors = self._probe_hls_host(camera.server, camera, start_ts, end_ts, 8)
        errors.extend(player_errors)
        if camera.server not in checked:
            checked.append(camera.server)
        player_slices: list[ArchiveSlice] = []
        player_hosts: list[str] = []
        if player_info:
            player_slices = list(player_info.get("slices") or [])
            player_hosts = list(player_info.get("hosts") or [])
            for host in player_hosts:
                if host not in checked:
                    checked.append(host)
            plan = self._build_plan(player_slices, start_ts, end_ts)
            gaps = self._gaps(plan, start_ts, end_ts)
            if player_hosts:
                self._remember_history_hosts(camera.label, player_hosts)
                self._remember_global_hosts(operator_id, player_hosts)
            # Если плеер уже отдал весь период, больше НИ ОДИН сервер не сканируем.
            if plan and not gaps:
                return ArchiveDiscovery(
                    camera, start_ts, end_ts, plan, [], checked, errors, [],
                    hls_fallback_url=str(player_info.get("url") or ""),
                    hls_fallback_duration=float(player_info.get("duration") or 0.0),
                    hls_fallback_segments=int(player_info.get("segments") or 0),
                    hls_fallback_host=str(player_info.get("host") or camera.server),
                    hls_fallback_method=str(player_info.get("method") or "player playlist"),
                    hls_fallback_hosts=player_hosts,
                )
        else:
            plan = []
            gaps = [ArchiveGap(start_ts, end_ts)]

        # 2. Только если сам player playlist не закрыл период — смотрим реальные
        # события reserve-transfer. Для короткого запроса обычно достаточно первых
        # двух страниц, вместо 12 последовательных запросов как раньше.
        if progress:
            progress("Проверяю переезды архива", "Ищу reserve-transfer только для непокрытого периода…")
        reserve_events = self.b2o.reserve_transfers(camera.server, start_ts, end_ts, max_pages=2)
        reserve_hosts: list[str] = []
        for event in reserve_events:
            for host in (event.server_to, event.server_from):
                clean = self.b2o.normalize_vcore_host(host)
                if clean and clean != camera.server and clean not in reserve_hosts:
                    reserve_hosts.append(clean)

        extra_slices: list[ArchiveSlice] = []
        if reserve_hosts:
            with ThreadPoolExecutor(max_workers=min(8, len(reserve_hosts)), thread_name_prefix="archive-reserve-player") as pool:
                futures = {pool.submit(self._probe_hls_host, host, camera, start_ts, end_ts, 6): host for host in reserve_hosts}
                for future in as_completed(futures):
                    host = futures[future]
                    if cancel_event is not None and cancel_event.is_set():
                        raise OperationCancelled("Операция отменена пользователем")
                    if host not in checked:
                        checked.append(host)
                    try:
                        info, errs = future.result()
                        errors.extend(errs)
                    except Exception as exc:
                        info = None
                        errors.append(f"{host}: {exc}")
                    if info:
                        extra_slices.extend(info.get("slices") or [])
                        learned = list(info.get("hosts") or [])
                        self._remember_history_hosts(camera.label, learned)
                        self._remember_global_hosts(operator_id, learned)

        all_slices = list(player_slices) + extra_slices
        plan = self._build_plan(all_slices, start_ts, end_ts)
        gaps = self._gaps(plan, start_ts, end_ts)

        # 3. Последний fallback — только реальный список серверов B2O + история.
        # Никаких b2o-vcore1..500.
        deep_slices: list[ArchiveSlice] = []
        if gaps:
            deep_slices, deep_checked, deep_count = self._deep_search_missing(
                camera, start_ts, end_ts, all_slices, set(checked), progress, cancel_event
            )
            if deep_slices:
                all_slices.extend(deep_slices)
                plan = self._build_plan(all_slices, start_ts, end_ts)
                gaps = self._gaps(plan, start_ts, end_ts)
            checked.extend(x for x in deep_checked if x not in checked)
            if deep_count:
                errors.append(f"Fallback по реальному реестру B2O: проверено {deep_count} серверов")

        confirmed_hosts = list(dict.fromkeys([x.host for x in plan]))
        if confirmed_hosts:
            self._remember_history_hosts(camera.label, confirmed_hosts)
            self._remember_global_hosts(operator_id, confirmed_hosts)

        # Если никаких дополнительных серверов не нашли, оставляем исходный
        # player URL даже при частичном покрытии — найденные сегменты всё равно
        # можно скачать напрямую. Если план дополнен reserve/fallback, скачивание
        # пойдёт по объединённым slices.
        only_player = bool(player_info) and not extra_slices and not deep_slices
        return ArchiveDiscovery(
            camera, start_ts, end_ts, plan, gaps, checked, errors, reserve_events,
            hls_fallback_url=str(player_info.get("url") or "") if (player_info and only_player) else "",
            hls_fallback_duration=float(player_info.get("duration") or 0.0) if player_info else 0.0,
            hls_fallback_segments=int(player_info.get("segments") or 0) if player_info else 0,
            hls_fallback_host=str(player_info.get("host") or "") if player_info else "",
            hls_fallback_method=str(player_info.get("method") or "") if player_info else "",
            hls_fallback_hosts=player_hosts,
        )

    @staticmethod
    def _build_plan(slices: list[ArchiveSlice], start: float, end: float) -> list[ArchiveSlice]:
        priorities={"reserve":0,"player":1,"main":2,"deep":3}
        items=sorted(slices,key=lambda x:(x.start,priorities.get(x.source,5),-x.end))
        out=[]; cursor=start
        while cursor < end-0.5:
            cover=[x for x in items if x.start <= cursor+1 and x.end > cursor+0.5]
            if not cover:
                future=[x.start for x in items if x.start > cursor+1]
                if not future: break
                cursor=min(future); continue
            chosen=max(cover,key=lambda x:(x.end,-priorities.get(x.source,5)))
            part_end=min(end,chosen.end)
            if part_end<=cursor: break
            out.append(ArchiveSlice(chosen.host,chosen.app,chosen.stream,cursor,part_end,chosen.signature,chosen.source)); cursor=part_end
        compact=[]
        for x in out:
            if compact and compact[-1].host==x.host and compact[-1].app==x.app and compact[-1].stream==x.stream and x.start<=compact[-1].end+1:
                compact[-1].end=max(compact[-1].end,x.end)
            else: compact.append(x)
        return compact

    @staticmethod
    def _gaps(slices: list[ArchiveSlice], start: float, end: float) -> list[ArchiveGap]:
        gaps=[]; cursor=start
        for x in sorted(slices,key=lambda z:z.start):
            if x.start>cursor+1: gaps.append(ArchiveGap(cursor,x.start))
            cursor=max(cursor,x.end)
        if cursor<end-1: gaps.append(ArchiveGap(cursor,end))
        return gaps

    @staticmethod
    def playlist_url(slice_: ArchiveSlice) -> str:
        duration=max(1,int(round(slice_.duration))); start=int(round(slice_.start))
        sig=slice_.signature[1:] if slice_.signature.startswith(("?","&")) else slice_.signature
        if sig and not sig.startswith("wmsAuthSign="): sig="wmsAuthSign="+sig
        base=f"https://{slice_.host}/{slice_.app}/{slice_.stream}/playlist_dvr_range-{start}-{duration}.m3u8"
        return base + (("?"+sig) if sig else "")

    @staticmethod
    def tools_dir() -> Path:
        """Writable directory for downloaded helper binaries.

        Installed copies live under Program Files and a normal user cannot create
        ``{app}\\tools`` there. Keep downloaded FFmpeg in LocalAppData instead.
        """
        local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if local:
            return Path(local) / "LinkVideo.Helper" / "tools"
        return Path(tempfile.gettempdir()) / "LinkVideo.Helper" / "tools"

    @staticmethod
    def _ffmpeg_candidates() -> list[Path]:
        import sys

        candidates: list[Path] = []
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            candidates.extend([
                exe_dir / "_internal" / "tools" / "ffmpeg.exe",
                exe_dir / "tools" / "ffmpeg.exe",
            ])
            meipass = getattr(sys, "_MEIPASS", "")
            if meipass:
                candidates.append(Path(meipass) / "tools" / "ffmpeg.exe")
        else:
            candidates.append(Path(__file__).resolve().parents[2] / "tools" / "ffmpeg.exe")

        candidates.append(ArchiveService.tools_dir() / "ffmpeg.exe")
        found = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
        if found:
            candidates.append(Path(found))

        result: list[Path] = []
        seen: set[str] = set()
        for item in candidates:
            key = str(item).lower()
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result

    @staticmethod
    def _ffmpeg_usable(path: Path) -> bool:
        """Do not trust a cached/bundled ffmpeg.exe just because the file exists."""
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            result = subprocess.run(
                [str(path), "-version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                creationflags=flags,
            )
            return result.returncode == 0 and "ffmpeg version" in (result.stdout or "").lower()
        except Exception:
            return False

    def ensure_ffmpeg(self, progress: Callable[[str, str], None] | None = None, cancel_event=None) -> str:
        """Return the validated LocalAppData/system FFmpeg executable."""
        from linkvideo_vpn_helper.services.archive_download_methods import _download_ffmpeg

        return _download_ffmpeg(self, progress, cancel_event)

    def download(
        self,
        discovery: ArchiveDiscovery,
        output: Path,
        progress: Callable[[dict], None] | None = None,
        cancel_event=None,
    ) -> ArchiveDownloadResult:
        """Download through the explicitly selected, cancellable transport."""
        from linkvideo_vpn_helper.services.archive_download_methods import (
            DEFAULT_ARCHIVE_DOWNLOAD_METHOD,
            _download_curl,
            _download_no_audio,
            _download_with_ffmpeg_audio,
        )

        method = str(
            getattr(self, "_lv_archive_download_method", DEFAULT_ARCHIVE_DOWNLOAD_METHOD)
            or DEFAULT_ARCHIVE_DOWNLOAD_METHOD
        )
        if method == "curl":
            return _download_curl(self, discovery, output, progress, cancel_event)
        if method == "ffmpeg_no_audio":
            return _download_no_audio(self, discovery, output, progress, cancel_event)
        return _download_with_ffmpeg_audio(self, discovery, output, progress, cancel_event)

    @staticmethod
    def format_local(ts: float, offset: int) -> str:
        return datetime.fromtimestamp(float(ts),timezone(timedelta(hours=int(offset)))).strftime("%d.%m.%Y %H:%M:%S")

    @staticmethod
    def format_reserve_log_time(ts: float) -> str:
        return datetime.fromtimestamp(
            float(ts), timezone(timedelta(hours=B2OService.RESERVE_LOG_TZ_OFFSET))
        ).strftime("%d.%m.%Y %H:%M:%S")

    @staticmethod
    def reserve_log_period(start_ts: float, end_ts: float) -> tuple[str, str]:
        return (
            ArchiveService.format_reserve_log_time(start_ts),
            ArchiveService.format_reserve_log_time(end_ts),
        )
