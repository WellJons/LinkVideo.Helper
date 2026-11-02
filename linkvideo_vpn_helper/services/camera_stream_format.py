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



def expected_side_task(camera: dict) -> tuple[str, str]:
    """LinkVideo media rules: based on the available encoded stream codecs.

    This is a *recommendation*, not proof the existing side-task runs.
    A transcoding output may differ from its original input; compare source
    codecs when B2O makes them available.
    """
    video = readable_video_codec(
        camera.get("source_video_codec") or camera.get("camera_video_codec")
        or camera.get("nimble_main_video_codec")
    )
    audio = readable_audio_codec(
        camera.get("source_audio_codec") or camera.get("camera_audio_codec")
        or camera.get("nimble_main_audio_codec")
    )
    if video not in ("H.264", "H.265"):
        return "Не определён", "Исходный видеокодек неизвестен или не H.264/H.265."
    if audio == "AAC":
        return "Не требуется", f"{video} + AAC: транскод не требуется."
    if audio == "Не указан":
        muted = str(camera.get("mute_sound") or "0").lower() in ("1", "true")
        sound_needed = str(camera.get("need_sound") or "0").lower() in ("1", "true")
        if muted or not sound_needed:
            return "Не требуется", "Аудиодорожки нет; mute включён или звук не требуется."
        return "Без звука — проверить mute", (
            "Аудиодорожка отсутствует: транскод не обязателен, "
            "но при включённом звуке нужно проверить настройку mute."
        )
    if video == "H.264":
        return "Regular RTMP_push side-task", (
            f"H.264 + {audio}: для приведения аудио к AAC "
            "требуется Regular RTMP_push side-task."
        )
    return "Custom RTSP side-task", (
        f"H.265 + {audio}: для приведения аудио к AAC "
        "требуется Custom RTSP side-task."
    )


def _configured_side_task(camera: dict) -> str:
    """Only accept an explicit side-task type, never infer from numeric IDs."""
    for key in ("side_task_type", "sidetask_type", "side_task_mode",
                "side_task_name", "transcoding_task_type"):
        raw = camera.get(key)
        if not isinstance(raw, str):
            continue
        value = raw.casefold().replace("-", "_")
        if "rtmp" in value and "push" in value:
            return "Regular RTMP_push side-task"
        if "rtsp" in value and ("custom" in value or "side" in value):
            return "Custom RTSP side-task"
        if value in ("none", "disabled", "off"):
            return "Не требуется"
    return ""


def assess_linkvideo_transcode(camera: dict) -> tuple[str, str]:
    """Apply the company codec rule without falsely confirming side-task state."""
    expected, reason = expected_side_task(camera)
    configured = _configured_side_task(camera)
    if expected in ("Не определён", "Без звука — проверить mute"):
        return expected, reason
    if not configured:
        if expected == "Не требуется":
            return "Не требуется", reason + (
                " Активность side-task в этих полях B2O не подтверждена."
            )
        return expected, reason + (
            " Тип фактически настроенной side-task в карточке B2O не указан; "
            "совпадение с требованием пока не проверено."
        )
    if configured == expected:
        return "Соответствует", reason + " Настройка side-task совпадает."
    return "Несоответствие", (
        reason + f" В B2O задано: {configured}; требуется: {expected}."
    )


def evaluate_transcode(camera: dict, rules: object) -> tuple[str, str]:
    """Compare B2O configuration only against an explicit operator policy.

    Rules format: {"default": {"video_codec": "H.264"},
                   "tariffs": {"742": {"video_codec": "H.265",
                                        "transcoding_server": 12}},
                   "cameras": {"167024": {...}}}
    Camera > tariff > default. No policy means no red/green verdict.
    """
    summary, _note = transcode_summary(camera)
    if not isinstance(rules, dict) or not rules:
        return assess_linkvideo_transcode(camera)
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
        return assess_linkvideo_transcode(camera)
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
    getter = getattr(settings, "value", None)
    if not callable(getter):
        return {}
    value = str(getter("camera/transcode_rules_json", "", str) or "")
    if not value or len(value) > 20000:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}
