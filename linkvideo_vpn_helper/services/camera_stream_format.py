from __future__ import annotations

"""Human-readable stream labels and conservative B2O transcoding assessment."""

import re


def readable_video_codec(value: object) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "Не указан"
    if raw.startswith(("avc1", "avc3", "h264", "libx264")):
        return "H.264"
    if raw.startswith(("hev1", "hvc1", "h265", "hevc", "libx265")):
        return "H.265"
    if raw.startswith(("av01", "av1")):
        return "AV1"
    if raw.startswith(("vp09", "vp9")):
        return "VP9"
    if raw.startswith(("mp4v", "mpeg4")):
        return "MPEG-4"
    return str(value or "Не указан")[:45]


def readable_audio_codec(value: object) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "Не указан"
    if raw.startswith(("mp4a", "aac")):
        return "AAC"
    if raw.startswith(("pcma", "alaw", "pcm_alaw", "g711a", "g.711a")):
        return "G.711A"
    if raw.startswith(("pcmu", "mulaw", "pcm_mulaw", "g711u", "g.711u")):
        return "G.711U"
    if raw.startswith(("opus",)):
        return "Opus"
    if raw.startswith(("mp3", "mp2", "mpga")):
        return "MP3" if raw.startswith("mp3") else "MPEG Audio"
    if raw.startswith(("g722", "adpcm_g722")):
        return "G.722"
    return str(value or "Не указан")[:45]


def transcode_summary(camera: dict) -> tuple[str, str]:
    """Read B2O config; no unsupported 'wrong transcode' inference.

    Correctness requires an operator-defined policy: valid profiles can vary
    by client, tariff, source equipment and archive player.
    """
    server_id = camera.get("id_transcoding_server")
    profile = camera.get("transcoding_profile") or camera.get("transcode_profile")
    enabled = camera.get("is_transcoding")
    if server_id not in (None, "", 0, "0"):
        if not str(server_id).isdigit():
            return "Неизвестный транскод", "Параметры сервера требуют проверки."
        return (f"Сервер транскода #{server_id}", 
                "Сверка профиля невозможна без правил для тарифа и камеры.")
    if enabled in (1, True, "1"):
        return "Транскод включён", "Админка не указала сервер транскода."
    if profile:
        return (f"Профиль: {str(profile)[:50]}", 
                "Сверка профиля невозможна без правил для тарифа.")
    return "Не задан", "Нет признаков включённого транскода в карточке B2O."


def camera_address(camera: dict) -> str:
    for key in ("address", "installation_address", "installation_location",
                "location_address", "camera_address"):
        value = camera.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:250]
    return "Адрес в карточке B2O не указан"
