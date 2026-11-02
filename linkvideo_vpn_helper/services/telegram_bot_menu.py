from __future__ import annotations

"""Telegram reply-keyboard navigation and paginated B2O camera inventory.

One bot per computer, a verified private-chat invite per Telegram account.
Sensitive camera RTSP URLs and service passwords never leave the bot host.
"""

from dataclasses import replace
from linkvideo_vpn_helper.services.telegram_vpn_operations import TelegramVPNOperations
import re
import time
from urllib.parse import urlencode

from linkvideo_vpn_helper.services.telegram_camera_agent import TelegramCameraAgent
from linkvideo_vpn_helper.services.camera_lookup import CameraChoice
from linkvideo_vpn_helper.services.camera_diagnostics import inspect_camera
from linkvideo_vpn_helper.services.b2o_camera_preview import fetch_camera_preview
from linkvideo_vpn_helper.services.b2o_rtsp_probe import fetch_camera_stream_with_status
from linkvideo_vpn_helper.services.camera_live_probe import probe_live_camera
from linkvideo_vpn_helper.services.camera_stream_format import (
    camera_address, evaluate_transcode, load_transcode_rules,
    readable_audio_codec, readable_video_codec,
)

_LOGIN = re.compile(r"[0-9]{10,12}\Z")
_ID = re.compile(r"[1-9][0-9]{0,17}\Z")
_PAGE_SIZE = 6
_MAX_RESULT = 250


def camera_state(status: str) -> str:
    value = str(status or "").strip().lower()
    if value in ("online", "active"):
        return "🟢 Онлайн по B2O"
    if value in ("offline", "error", "failed", "inactive"):
        return "🔴 Не работает по B2O"
    return "⚪ Статус B2O неизвестен"


def menu_keyboard():
    return {"keyboard": [
        [{"text": "🔎 Найти клиента"}, {"text": "🔐 Создать VPN"}],
        [{"text": "📋 Последние камеры"}, {"text": "ℹ️ Помощь"}],
    ], "resize_keyboard": True, "is_persistent": True}


class TelegramMenuAgent(TelegramCameraAgent):
    def __post_init__(self):
        super().__post_init__()
        self.waiting_login: set[int] = set()
        self.current_client: dict[int, str] = {}
        self.client_cameras: dict[int, tuple[CameraChoice, ...]] = {}
        self.waiting_vpn_login: set[int] = set()
        self.vpn_pending: dict[int, object] = {}
        self.vpn_operations = TelegramVPNOperations()

    def _request(self, method, payload=None, photo=None):
        # Native Bot API commands; the inherited implementation already
        # filters error response contents to prevent bot-token leakage.
        return super()._request(method, payload, photo)

    def _allowed(self, chat: int) -> bool:
        return (chat in self.allowed_users or
                bool(self.invites and self.invites.is_member(chat)))

    def _list_client(self, login: str) -> tuple[CameraChoice, ...]:
        if not _LOGIN.fullmatch(login):
            return ()
        token = self.b2o.token()
        if not token:
            raise RuntimeError("B2O не авторизован.")
        results = {}
        for page in range(1, 11):
            url = self.b2o.CAMERAS_URL.format(operator=241) + "?" + urlencode({
                "Search": login, "Count": 50, "Page": page,
                "Show_deleted_cameras": 0,
            })
            # The third parameter to B2O.request_json is HEADERS.
            # All filters must be encoded in the query string.
            payload = self.b2o.request_json(url, token, timeout=8)
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise RuntimeError("B2O не вернул список камер.")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                owner = str(
                    row.get("owner_login") or row.get("account_login")
                    or row.get("customer_login") or ""
                ).strip()
                number = str(row.get("id_camera") or "")
                if owner != login or not _ID.fullmatch(number):
                    continue
                results[number] = CameraChoice(
                    number, 241, "Россия",
                    str(row.get("short_name") or row.get("camera_name")
                        or "linkvideo_" + number)[:100],
                    owner, str(row.get("camera_status") or ""),
                )
            if len(rows) < 50 or len(results) >= _MAX_RESULT:
                break
        return tuple(results.values())[:_MAX_RESULT]

    def _show_client(self, chat: int, login: str):
        cameras = self._list_client(login)
        self.current_client[chat] = login
        self.client_cameras[chat] = cameras
        self.selection[chat] = {camera.camera_id: camera for camera in cameras}
        self.waiting_login.discard(chat)
        self._show_page(chat, 0)

    def _show_page(self, chat: int, page: int, message_id: int | None = None):
        cameras = self.client_cameras.get(chat, ())
        if not cameras:
            self._say(chat, "Камер клиента не найдено. Выбери «Найти клиента».")
            return
        count = len(cameras)
        last_page = (count - 1) // _PAGE_SIZE
        page = min(max(0, page), last_page)
        on = sum(c.status.lower() in ("online", "active") for c in cameras)
        off = sum(c.status.lower() in ("offline", "error", "failed", "inactive")
                  for c in cameras)
        shown = cameras[page * _PAGE_SIZE:(page + 1) * _PAGE_SIZE]
        summary = (
            f"Клиент {self.current_client.get(chat, '')}\n"
            f"Камер: {count} · Онлайн: {on} · Не работают: {off}\n"
            f"Страница {page+1}/{last_page+1}\n"
            "Нажми на название камеры:"
        )
        buttons = []
        for cam in shown:
            summary += "\n" + camera_state(cam.status) + " · " + cam.label[:54]
            buttons.append([{
                "text": cam.label[:48], "callback_data": "camera:" + cam.camera_id,
            }])
        page_row = []
        if page > 0:
            page_row.append({"text": "◀️ Назад", "callback_data": f"page:{page-1}"})
        if page < last_page:
            page_row.append({"text": "Вперёд ▶️", "callback_data": f"page:{page+1}"})
        if page_row:
            buttons.append(page_row)
        buttons.append([{"text": "🔎 Новый поиск", "callback_data": "find"}])
        body = {"chat_id": chat, "text": summary,
                "reply_markup": {"inline_keyboard": buttons}}
        if message_id is not None:
            body["message_id"] = message_id
            self._request("editMessageText", body)
        else:
            self._request("sendMessage", body)

    def _show_camera(self, chat: int, ident: str):
        choice = self.selection.get(chat, {}).get(ident)
        if choice is None:
            self._say(chat, "Камеры нет в текущем списке. Выполни поиск снова.")
            return
        self._say(chat, "Получаю превью и проверяю поток…")
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
            report = replace(report, rtsp_url=stream.url,
                             rtsp_host=stream.host, rtsp_port=stream.port)
        if stream is not None:
            from linkvideo_vpn_helper.services.camera_live_probe import _cached_ffmpeg
            if not _cached_ffmpeg():
                from linkvideo_vpn_helper.services.archive_service_core import ArchiveService
                try:
                    ArchiveService(self.b2o.settings).ensure_ffmpeg()
                except (OSError, ValueError, RuntimeError):
                    # The normal probe will state that FFmpeg is not available.
                    pass
        result = probe_live_camera(report)
        # The recent-archive fallback is not a CURRENT bitrate.
        current = result.source == "RTSP"
        bitrate = result.bitrate_mbps if current else "Не измерен"
        fps = result.fps if current else "Не измерен"
        codecs = "FFmpeg" if current else "справочно B2O"
        video = readable_video_codec(
            result.video_codec or report.metadata.get("nimble_main_video_codec")
        )
        audio = readable_audio_codec(
            result.audio_codec or report.metadata.get("nimble_main_audio_codec")
        )
        label = choice.label[:120]
        try:
            photo = fetch_camera_preview(
                choice.country, choice.camera_id, self.b2o.token()
            )
            if photo.startswith(b"\xff\xd8\xff") or photo.startswith(b"\x89PNG"):
                self._request("sendPhoto", {
                    "chat_id": chat, "caption": label,
                }, photo=photo)
        except (OSError, ValueError, TypeError):
            pass
        description = (
            f"{label}\n{camera_state(report.status)}\n"
            f"ID: {report.camera_id}\n"
            f"Адрес: {camera_address(report.metadata)}\n"
            f"Текущий битрейт (RTSP): {bitrate}\n"
            f"FPS (RTSP): {fps}\n"
            f"Видео/аудио ({codecs}): {video}, {audio}\n"
            f"Разрешение: {result.resolution or report.metadata.get('nimble_main_resolution') or 'нет данных'}\n"
            f"Транскод: {transcode}\n{note}\n"
            f"Диагностика: {result.detail if current else (stream_error or result.detail)}"
        )
        self._request("sendMessage", {
            "chat_id": chat, "text": description[:3500],
            "reply_markup": {"inline_keyboard": [
                [{"text": "🔄 Повторить замер", "callback_data": "camera:" + ident}],
                [{"text": "◀️ К списку", "callback_data": "back"}],
            ]},
        })

    def _begin_vpn(self, chat: int):
        self.waiting_login.discard(chat)
        self.waiting_vpn_login.add(chat)
        self.vpn_pending.pop(chat, None)
        self._say(chat, "Введи логин нового VPN-клиента. "
                  "По умолчанию: Россия, 3 NAT-порта, 1 учётная запись.")

    def _prepare_vpn(self, chat: int, login: str):
        # Never mutate RouterOS before the Telegram user confirms.
        self._say(chat, "Проверяю доступность VPN-серверов и свободный логин…")
        proposal = self.vpn_operations.prepare(login, "Россия", 3)
        self.waiting_vpn_login.discard(chat)
        self.vpn_pending[chat] = proposal
        notice = (
            "Подтверди создание VPN:\n"
            f"Страна: {proposal.country}\n"
            f"Сервер: {proposal.server}\n"
            f"Логин: {proposal.expected_login}\n"
            f"Учёток: 1 · NAT-портов: {proposal.ports}\n"
            "После подтверждения клиент будет реально создан на MikroTik."
        )
        self._request("sendMessage", {
            "chat_id": chat,
            "text": notice,
            "reply_markup": {"inline_keyboard": [[
                {"text": "✅ Создать VPN", "callback_data":
                 "vpn_confirm:" + proposal.request_id},
                {"text": "❌ Отмена", "callback_data": "vpn_cancel"},
            ]]},
        })

    def _confirm_vpn(self, chat: int, token: str):
        proposal = self.vpn_pending.get(chat)
        if proposal is None or proposal.request_id != token:
            self._say(chat, "Заявка недоступна или уже выполнена. Создай новую.")
            return
        # Pop before any network calls: a repeated callback cannot run twice.
        self.vpn_pending.pop(chat, None)
        self._say(chat, "Создаю VPN на MikroTik через проверенный механизм Helper…")
        record = self.vpn_operations.execute(chat, proposal)
        # Return exactly the human-readable VPN record the desktop emits.
        # This includes a new VPN password, only to the invited PRIVATE chat.
        self._say(chat, "✅ VPN создан\n\n" + record.copy_text())

    def _handle_callback(self, callback: dict):
        sender = callback.get("from") or {}
        message = callback.get("message") or {}
        chat_obj = message.get("chat") or {}
        ident = sender.get("id")
        if (type(ident) is not int or not self._allowed(ident) or
                chat_obj.get("id") != ident or chat_obj.get("type") != "private"):
            return
        callback_id = str(callback.get("id") or "")
        if callback_id:
            self._request("answerCallbackQuery", {"callback_query_id": callback_id})
        data = str(callback.get("data") or "")
        if data == "vpn_cancel":
            self.vpn_pending.pop(ident, None)
            self._say(ident, "Создание VPN отменено.")
        elif re.fullmatch(r"vpn_confirm:[0-9a-f]{24}", data):
            self._confirm_vpn(ident, data.partition(":")[2])
        elif data == "find":
            self.waiting_login.add(ident)
            self._say(ident, "Введи логин клиента (10–12 цифр).")
        elif data == "back":
            self._show_page(ident, 0)
        elif re.fullmatch(r"page:[0-9]{1,3}", data):
            self._show_page(ident, int(data[5:]), message.get("message_id"))
        elif re.fullmatch(r"camera:[1-9][0-9]{0,17}", data):
            self._show_camera(ident, data[7:])

    def handle_update(self, update: dict):
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            try:
                self._handle_callback(callback)
            except Exception:
                sender = (callback.get("from") or {}).get("id")
                if type(sender) is int and self._allowed(sender):
                    self._say(sender, "Не удалось выполнить действие. Проверь соединение и настройки бота.")
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        sender = message.get("from") or {}
        chat_obj = message.get("chat") or {}
        chat = sender.get("id")
        if (type(chat) is not int or chat_obj.get("type") != "private"
                or chat_obj.get("id") != chat):
            return
        raw = str(message.get("text") or "").strip()
        command = raw.split(maxsplit=1)
        verb = command[0].split("@", 1)[0].lower() if raw.startswith("/") else ""
        param = command[1].strip() if len(command) > 1 else ""
        if not self._allowed(chat):
            if (verb == "/start" and param and self.invites is not None
                    and self.invites.accept(chat, param)):
                self._say(chat, "Приглашение принято. Доступ открыт!")
                self._request("sendMessage", {
                    "chat_id": chat, "text": "Главное меню",
                    "reply_markup": menu_keyboard(),
                })
            elif verb == "/start":
                self._say(chat, "Доступ только по ссылке-приглашению.")
            return
        now = time.monotonic()
        if now - self.last_request.get(chat, -10000) < 0.8:
            return
        self.last_request[chat] = now
        try:
            if verb in ("/start", "/help") or raw == "ℹ️ Помощь":
                self._say(chat, "LinkVideo · поиск камер\n"
                          "🔎 Найти клиента — поиск по логину\n"
                          "📋 Последние камеры — открыть последний список\n"
                          "🔐 Создать VPN — проверка и подтверждение создания\n"
                          "/client ЛОГИН — быстрый поиск")
                self._request("sendMessage", {
                    "chat_id": chat, "text": "Выбери действие:",
                    "reply_markup": menu_keyboard(),
                })
            elif verb == "/client":
                if _LOGIN.fullmatch(param):
                    self._show_client(chat, param)
                else:
                    self._say(chat, "Укажи логин клиента из 10–12 цифр.")
            elif raw == "🔐 Создать VPN" or verb == "/vpn":
                self._begin_vpn(chat)
            elif raw == "🔎 Найти клиента":
                self.waiting_login.add(chat)
                self._say(chat, "Введи логин клиента (10–12 цифр).")
            elif raw == "📋 Последние камеры":
                self._show_page(chat, 0)
            elif chat in self.waiting_vpn_login:
                self._prepare_vpn(chat, raw)
            elif chat in self.waiting_login:
                if _LOGIN.fullmatch(raw):
                    self._show_client(chat, raw)
                else:
                    self._say(chat, "Логин клиента должен содержать 10–12 цифр.")
            elif verb == "/camera" and _ID.fullmatch(param):
                self._show_camera(chat, param)
            elif raw:
                self._say(chat, "Выбери «Найти клиента» в меню.")
        except Exception:
            self._say(chat, "Не удалось выполнить операцию. Проверь настройки "
                      "B2O или MikroTik на компьютере с ботом.")

    def run_forever(self):
        self._request("setMyCommands", {"commands": [
            {"command": "start", "description": "Главное меню"},
            {"command": "client", "description": "Поиск камер по логину"},
            {"command": "vpn", "description": "Создать VPN клиента"},
            {"command": "help", "description": "Помощь"},
        ]})
        while True:
            updates = self._request("getUpdates", {
                "offset": self.offset, "timeout": 25,
                "allowed_updates": ["message", "callback_query"],
            })
            items = updates.get("result") if isinstance(updates, dict) else None
            if not isinstance(items, list):
                time.sleep(4)
                continue
            for item in items[:100]:
                if not isinstance(item, dict):
                    continue
                try:
                    self.handle_update(item)
                finally:
                    self.offset = max(self.offset, int(item.get("update_id") or 0) + 1)
