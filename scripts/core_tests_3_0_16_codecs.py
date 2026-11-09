from __future__ import annotations

"""No-network codec and transcode mapping regressions."""


from linkvideo_vpn_helper.services.camera_stream_format import (
    readable_video_codec, readable_audio_codec, transcode_summary,
    camera_address, evaluate_transcode, expected_side_task, assess_linkvideo_transcode,
)

assert readable_video_codec("avc1.4d0033") == "H.264"
assert readable_video_codec("h264") == "H.264"
assert readable_video_codec("hvc1.1.6") == "H.265"
assert readable_video_codec("hevc") == "H.265"
assert readable_audio_codec("mp4a.40.2") == "AAC"
assert readable_audio_codec("pcm_alaw") == "G.711A"
assert readable_audio_codec("pcmu") == "G.711U"
assert camera_address({"address": None}) == "Адрес в карточке B2O не указан"
assert camera_address({"address": "Производственная 16"}) == "Производственная 16"
assert transcode_summary({"id_transcoding_server": None})[0] == "Не задан"
assert "Сверка профиля" in transcode_summary({"id_transcoding_server": 74})[1]
cam = {"id_camera": 167024, "id_tariff": 742,
       "id_transcoding_server": 74, "nimble_main_video_codec": "avc1.4d0033"}
pol = {"tariffs": {"742": {"video_codec": "H.265", "transcoding_server": 17}}}
assert evaluate_transcode(cam, pol)[0] == "Несоответствие"
assert "H.265" in evaluate_transcode(cam, pol)[1]
assert assess_linkvideo_transcode(cam)[0] == "Не требуется"
assert "H.264 + AAC" in assess_linkvideo_transcode(cam)[1] if cam.get("nimble_main_audio_codec") == "mp4a.40.2" else True
pol = {"tariffs": {"742": {"video_codec": "H.264", "transcoding_server": 74}}}
assert "соответствуют" in evaluate_transcode(cam, pol)[1]


# Built-in LinkVideo policy: H.264/H.265 with AAC require no side-task.
for codec in ("avc1.640029", "hvc1.1.6"):
    assert expected_side_task({
        "nimble_main_video_codec": codec,
        "nimble_main_audio_codec": "mp4a.40.2",
    })[0] == "Не требуется"
assert expected_side_task({
    "nimble_main_video_codec": "avc1.640029",
    "nimble_main_audio_codec": "pcm_alaw",
})[0] == "Regular RTMP_push side-task"
assert expected_side_task({
    "nimble_main_video_codec": "hvc1.1.6",
    "nimble_main_audio_codec": "pcmu",
})[0] == "Custom RTSP side-task"
assert assess_linkvideo_transcode({
    "nimble_main_video_codec": "hvc1.1.6",
    "nimble_main_audio_codec": "pcmu",
})[0] == "Custom RTSP side-task"
assert assess_linkvideo_transcode({
    "nimble_main_video_codec": "hvc1.1.6",
    "nimble_main_audio_codec": "pcmu",
    "side_task_type": "Regular RTMP_push side-task",
})[0] == "Несоответствие"
assert assess_linkvideo_transcode({
    "nimble_main_video_codec": "hvc1.1.6",
    "nimble_main_audio_codec": "pcmu",
    "side_task_type": "Custom RTSP side-task",
})[0] == "Соответствует"
assert expected_side_task({
    "nimble_main_video_codec": "hvc1.1.6",
    "nimble_main_audio_codec": None,
    "need_sound": 1, "mute_sound": 0,
})[0] == "Без звука — проверить mute"
assert expected_side_task({
    "nimble_main_video_codec": "hvc1.1.6",
    "nimble_main_audio_codec": None,
    "need_sound": 1, "mute_sound": 1,
})[0] == "Не требуется"

print("CORE TESTS 3.0.16 CAMERA CODECS OK")
