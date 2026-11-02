from __future__ import annotations

"""Read-only Telegram camera helper, disabled until the operator configures it.

Uses the B2O session of the *same Windows user* as LinkVideo.Helper. Access
requires an explicit Telegram user-ID allowlist AND a private chat. No RTSP
credentials, B2O tokens or camera streaming URLs go to Telegram.
"""

import io
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from urllib.request import Request, urlopen

from PySide6.QtCore import QSettings

from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.b2o_camera_preview import fetch_camera_preview
from linkvideo_vpn_helper.services.b2o_rtsp_probe import fetch_camera_stream_with_status
from linkvideo_vpn_helper.services.camera_diagnostics import inspect_camera
from linkvideo_vpn_helper.services.camera_live_probe import probe_live_camera
from linkvideo_vpn_helper.services.camera_lookup import CameraChoice
from linkvideo_vpn_helper.services.camera_stream_format import (
    readable_audio_codec, readable_video_codec, evaluate_transcode,
    load_transcode_rules,
)
from dataclasses import replace

_API_ROOT = "https://api.telegram.org/bot"
_LOGIN = re.compile(r"[0-9]{10,12}\Z")
_TOKEN = re.compile(r"[0-9]{6,14}:[A-Za-z0-9_-]{20,}\Z")
_MAX_RESULT = 20


def allowed_ids_from_env(value: str) -> frozenset[int]:
    values = [x.strip() for x in (value or "").split(",")]
    if (not values or len(values) > 50 or
            any(not v.isascii() or not v.isdecimal() or not 1 <= len(v) <= 20
                for v in values)):
        raise ValueError("LINKVIDEO_TELEGRAM_ALLOWED_IDS: укажите числовые ID через запятую.")
    result = frozenset(int(x) for x in values)
    if not result or 0 in result:
        raise ValueError("Список разрешённых Telegram ID не задан.")
    return result


@dataclass
class TelegramCameraAgent:
    bot_token: str = field(repr=False)
    allowed_users: frozenset[int]
    b2o: B2OService = field(repr=False)
    offset: int = 0
    selection: dict[int, dict[str, CameraChoice]] = field(default_factory=dict, repr=False)
    last_request: dict[int, float] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        if not _TOKEN.fullmatch(self.bot_token):
            raise ValueError("LINKVIDEO_TELEGRAM_BOT_TOKEN некорректен.")
        if not self.allowed_users:
            raise ValueError("Нужен непустой allowlist Telegram ID.")

    def _request(self, method: str, payload: dict | None = None,
                 photo: bytes | None = None):
        if method not in ("getUpdates", "sendMessage", "sendPhoto"):
            raise ValueError("Неизвестный метод Telegram.")
        data = payload or {}
        if photo is None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        else:
            if not isinstance(photo, bytes) or len(photo) > 8 * 1024 * 1024:
                raise ValueError("Недопустимое изображение камеры.")
            boundary = "LV" + secrets.token_hex(12)
            fields = []
            for key, value in data.items():
                if key not in ("chat_id", "caption"):
                    continue
                fields.append(
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n"
                    .encode() + str(value).encode("utf-8") + b"\r\n"
                )
            fields.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
                f"filename=\"camera.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".encode()
                + photo + b"\r\n"
            )
            body = b"".join(fields) + f"--{boundary}--\r\n".encode()
            content_type = f"multipart/form-data; boundary={boundary}"
        endpoint = _API_ROOT + self.bot_token + "/" + method
        request = Request(endpoint, data=body, method="POST", headers={
            "Content-Type": content_type, "User-Agent": "LinkVideo.Helper Telegram Agent",
        })
        # Do not include urllib exceptions/URLs in logs: they contain bot token.
        try:
            with urlopen(request, timeout=37 if method == "getUpdates" else 15) as response:
                parsed = json.loads(response.read(2_000_001).decode("utf-8"))
            if not parsed.get("ok"):
                return {}
            return parsed
        except Exception:
            return {}

    def _say(self, chat: int, message: str):
        self._request("sendMessage", {"chat_id": chat, "text": message[:3800]})

    def _list_client(self, login: str) -> tuple[CameraChoice, ...]:
        """Exact-owner inventory: never show fuzzy B2O Search neighbors."""
        if not _LOGIN.fullmatch(login):
            return ()
        token = self.b2o.token()
        if not token:
            raise RuntimeError("Сначала подключите админку в LinkVideo.Helper.")
        out = {}
        for page in range(1, 5):
            payload = self.b2o.request_json(
                self.b2o.CAMERAS_URL.format(operator=241), token,
                {"Search": login, "Count": "50", "Page": str(page),
                 "Show_deleted_cameras": "0"}, timeout=8,
            )
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise RuntimeError("Ответ B2O не содержит список камер.")
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                owner = str(raw.get("owner_login") or raw.get("account_login")
                            or raw.get("customer_login") or "")
                if owner.casefold() != login.casefold():
                    continue
                number = str(raw.get("id_camera") or "")
                if not number.isascii() or not number.isdecimal() or int(number) <= 0:
                    continue
                out[number] = CameraChoice(
                    number, 241, "Россия",
                    str(raw.get("camera_name") or raw.get("short_name")
                        or "linkvideo_" + number)[:120],
                    owner, str(raw.get("camera_status") or ""),
                )
            if len(rows) < 50 or len(out) >= _MAX_RESULT:
                break
        return tuple(out.values())[:_MAX_RESULT]

    def _show_client(self, chat: int, login: str):
        cameras = self._list_client(login)
        self.selection[chat] = {cam.camera_id: cam for cam in cameras}
        if not cameras:
            self._say(chat, "Подтверждённых камер этого клиента не найдено.")
            return
        lines = [f"Камеры клиента: {len(cameras)}",
                 "Выберите /camera ID, чтобы получить диагностику и превью:"]
        for cam in cameras:
            lines.append(f"• {cam.label} — {cam.camera_id} ({cam.status or 'нет статуса'})")
        self._say(chat, "\n".join(lines))
        # Limit photos to avoid flooding Telegram channels and leaking inventories.
        for cam in cameras[:4]:
            try:
                blob = fetch_camera_preview(cam.country, cam.camera_id, self.b2o.token())
                if blob[:3] == b"\xff\xd8\xff":
                    self._request("sendPhoto", {
                        "chat_id": chat,
                        "caption": f"{cam.label} · {cam.camera_id}",
                    }, photo=blob)
            except Exception:
                continue

    def _show_camera(self, chat: int, ident: str):
        choice = self.selection.get(chat, {}).get(ident)
        if choice is None:
            self._say(chat, "Выберите ID из последнего списка /client.")
            return
        report = inspect_camera(
            self.b2o, choice.camera_id, choice.operator_id,
            with_preview=False, with_network=False,
        )
        transcode, note = evaluate_transcode(
            report.metadata, load_transcode_rules(self.b2o.settings)
        )
        stream, stream_error = fetch_camera_stream_with_status(
            self.b2o, choice.camera_id, choice.operator_id
        )
        if stream is not None:
            report = replace(report, rtsp_url=stream.url, rtsp_host=stream.host,
                             rtsp_port=stream.port)
        measure = probe_live_camera(report)
        from linkvideo_vpn_helper.services.camera_stream_format import camera_address
        message = (
            f"{choice.label}\nКамера: {report.camera_id}\n"
            f"B2O: {report.status or 'неизвестно'}\n"
            f"Адрес: {camera_address(report.metadata)}\n"
            f"Битрейт: {measure.bitrate_mbps}\n"
            f"FPS: {measure.fps or 'не измерен'}\n"
            f"Видео: {readable_video_codec(measure.video_codec or report.metadata.get('nimble_main_video_codec'))}\n"
            f"Аудио: {readable_audio_codec(measure.audio_codec or report.metadata.get('nimble_main_audio_codec'))}\n"
            f"Разрешение: {measure.resolution or report.metadata.get('nimble_main_resolution') or 'нет данных'}\n"
            f"Транскод: {transcode}\n{note}\n"
            f"Проверка: {measure.detail if measure.source == 'RTSP' else (stream_error or measure.detail)}"
        )
        self._say(chat, message)

    def handle_update(self, update: dict):
        message = update.get("message")
        if not isinstance(message, dict):
            return
        sender = message.get("from") or {}
        chat_obj = message.get("chat") or {}
        if (sender.get("id") not in self.allowed_users or
                chat_obj.get("type") != "private" or
                sender.get("id") != chat_obj.get("id")):
            return
        chat = int(chat_obj["id"])
        command = str(message.get("text") or "").strip()
        if not command.startswith("/"):
            return
        now = time.monotonic()
        if now - self.last_request.get(chat, -10000) < 3:
            return
        self.last_request[chat] = now
        chunks = command.split(maxsplit=1)
        verb = chunks[0].split("@", 1)[0].lower()
        argument = chunks[1].strip() if len(chunks) > 1 else ""
        try:
            if verb in ("/start", "/help"):
                self._say(chat, "LinkVideo · камеры\n/client ЛОГИН — найти камеры\n"
                          "/camera ID — диагностика и битрейт\n"
                          "Только приватный чат, доступ по Telegram ID.")
            elif verb == "/client":
                if not _LOGIN.fullmatch(argument):
                    self._say(chat, "Укажите логин клиента из 10–12 цифр.")
                else:
                    self._show_client(chat, argument)
            elif verb == "/camera":
                self._show_camera(chat, argument)
        except Exception:
            self._say(chat, "Не удалось выполнить запрос. Проверьте авторизацию B2O "
                      "в Helper и повторите поиск.")

    def run_forever(self):
        while True:
            result = self._request("getUpdates", {
                "offset": self.offset, "timeout": 25,
                "allowed_updates": ["message"],
            })
            updates = result.get("result") if isinstance(result, dict) else None
            if not isinstance(updates, list):
                time.sleep(4)
                continue
            for update in updates[:100]:
                if not isinstance(update, dict):
                    continue
                try:
                    self.handle_update(update)
                finally:
                    self.offset = max(self.offset, int(update.get("update_id") or 0) + 1)


def main():
    token = os.environ.get("LINKVIDEO_TELEGRAM_BOT_TOKEN", "").strip()
    allowed = allowed_ids_from_env(
        os.environ.get("LINKVIDEO_TELEGRAM_ALLOWED_IDS", "")
    )
    b2o = B2OService(QSettings("LinkVideo", "LinkVideo.Helper"))
    if not b2o.token():
        raise RuntimeError("Сначала подключите админку в LinkVideo.Helper.")
    TelegramCameraAgent(token, allowed, b2o).run_forever()


if __name__ == "__main__":
    main()
