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



def evaluate_transcode(camera: dict, rules: object) -> tuple[str, str]:
    """Compare B2O configuration only against an explicit operator policy.

    Rules format: {"default": {"video_codec": "H.264"},
                   "tariffs": {"742": {"video_codec": "H.265",
                                        "transcoding_server": 12}},
                   "cameras": {"167024": {...}}}
    Camera > tariff > default. No policy means no red/green verdict.
    """
    summary, note = transcode_summary(camera)
    if not isinstance(rules, dict):
        return summary, "Правила проверки транскода не настроены."
    groups = (("cameras", str(camera.get("id_camera") or "")),
              ("tariffs", str(camera.get("id_tariff") or "")))
    rule = None
    for group, key in groups:
        section = rules.get(group)
        if key and isinstance(section, dict) and isinstance(section.get(key), dict):
            rule = section[key]
            break
    if rule is None:
        rule = rules.get("default")
    if not isinstance(rule, dict) or not rule:
        return summary, "Правила проверки транскода не настроены."
    problems = []
    for item, parser, label in (
        ("video_codec", readable_video_codec, "Видеокодек"),
        ("audio_codec", readable_audio_codec, "Аудиокодек"),
    ):
        expected = rule.get(item)
        if expected:
            current = parser(camera.get(
                "nimble_main_video_codec" if item == "video_codec"
                else "nimble_main_audio_codec"
            ))
            if current == "Не указан":
                problems.append(f"{label}: фактическое значение неизвестно")
            elif current != parser(expected):
                problems.append(f"{label}: B2O {current}, требуется {parser(expected)}")
    if "transcoding_server" in rule:
        wanted = str(rule["transcoding_server"] or "")
        current = str(camera.get("id_transcoding_server") or "")
        if wanted != current:
            problems.append(
                f"Сервер транскода: B2O {current or 'нет'}, "
                f"ожидается {wanted or 'не задан'}"
            )
    if problems:
        return "Несоответствие", "; ".join(problems)
    return summary, "Параметры B2O соответствуют заданным правилам."


def load_transcode_rules(settings) -> dict:
    """Read a bounded local policy; never fetch remote executable rules."""
    import json
    value = str(settings.value("camera/transcode_rules_json", "", str) or "")
    if not value or len(value) > 20000:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}
