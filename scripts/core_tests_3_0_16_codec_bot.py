from __future__ import annotations

"""No-network codec/transcode mapping and Telegram access-control regression."""

from types import SimpleNamespace
from unittest.mock import patch

from linkvideo_vpn_helper.services.camera_stream_format import (
    readable_video_codec, readable_audio_codec, transcode_summary,
    camera_address, evaluate_transcode,
)
from linkvideo_vpn_helper.services.telegram_camera_agent import (
    TelegramCameraAgent, allowed_ids_from_env,
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
assert "Правила" in evaluate_transcode(cam, {})[1]
pol = {"tariffs": {"742": {"video_codec": "H.264", "transcoding_server": 74}}}
assert "соответствуют" in evaluate_transcode(cam, pol)[1]

assert allowed_ids_from_env("100001,200002") == frozenset({100001,200002})
for bad in ("", " ", "0", "abc", "123,xyz"):
    try:
        allowed_ids_from_env(bad)
        raise AssertionError("Accepted invalid Telegram allowlist")
    except ValueError:
        pass

class FakeB2O:
    CAMERAS_URL = "https://api.b2o.goodline.info/ords/mobile/vc2/operators/{operator}/cameras"
    def token(self):
        return "fixture-session"
    def request_json(self, url, token, headers=None, timeout=8):
        assert token == "fixture-session"
        return {"data": [
            {"id_camera": 1, "owner_login": "89001234567",
             "short_name": "Вход", "camera_status": "online"},
            {"id_camera": 2, "owner_login": "other",
             "short_name": "Чужая камера"},
        ]}

agent = TelegramCameraAgent(
    "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    frozenset({100001}), FakeB2O(),
)
sent = []
agent._request = lambda method, payload, photo=None: sent.append((method, payload)) or {}
denied = {"message": {"from": {"id": 99},
                      "chat": {"id": 99, "type": "private"},
                      "text": "/client 89001234567"}}
agent.handle_update(denied)
assert sent == []
group = {"message": {"from": {"id": 100001},
                     "chat": {"id": -1234, "type": "supergroup"},
                     "text": "/client 89001234567"}}
agent.handle_update(group)
assert sent == []
allowed = {"message": {"from": {"id": 100001},
                       "chat": {"id": 100001, "type": "private"},
                       "text": "/client 89001234567"}}
with patch("linkvideo_vpn_helper.services.telegram_camera_agent.fetch_camera_preview",
           side_effect=ValueError("fixture: no photo")):
    agent.handle_update(allowed)
assert "Вход" in sent[-1][1]["text"]
assert "Чужая камера" not in sent[-1][1]["text"]
assert len(agent.selection[100001]) == 1
assert "1" in agent.selection[100001]
print("CORE TESTS 3.0.16 CAMERA CODECS / TELEGRAM ALLOWLIST OK")
