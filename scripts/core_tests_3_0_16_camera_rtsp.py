from __future__ import annotations

"""No-network regression: real B2O stream-URL contract and FFmpeg RTSP probe."""
import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.b2o_rtsp_probe import (
    parse_camera_stream, fetch_camera_stream,
)
from linkvideo_vpn_helper.services.camera_diagnostics import CameraDiagnostics
from linkvideo_vpn_helper.services.camera_live_probe import (
    _probe_rtsp, probe_live_camera,
)

app = QApplication.instance() or QApplication([])
secret = "FAKE-PASSWORD-FOR-TEST"
stream_url = (
    "rtsp://fixture:" + secret +
    "@vpn05.linkvideo.ru:10021/1/1"
)

stream = parse_camera_stream({"camera_stream_main": stream_url,
                              "camera_stream_sub": None})
assert stream is not None
assert stream.host == "vpn05.linkvideo.ru"
assert stream.port == 10021
assert stream.url == stream_url
assert secret not in repr(stream)
assert parse_camera_stream({"camera_stream_main": "rtsp://u:p@127.0.0.1:554/a"}) is None
assert parse_camera_stream({"camera_stream_main": "rtsp://u:p@evil.example:554/a"}) is None
assert parse_camera_stream({"camera_stream_main": "https://vpn05.linkvideo.ru/a"}) is None

class FakeB2O:
    normalize_camera_id = B2OService.normalize_camera_id
    def token(self):
        return "fixture-token"
    def request_json(self, url, token, timeout=8):
        assert url == (
            "https://api.b2o.goodline.info/ords/mobile/vc2/"
            "operators/241/cameras/167024/camera-streams-urls"
        )
        assert token == "fixture-token" and timeout == 8
        return {"camera_stream_main": stream_url, "camera_stream_sub": None}

loaded = fetch_camera_stream(FakeB2O(), "linkvideo_167024", 241)
assert loaded is not None and loaded.port == 10021

report = CameraDiagnostics(
    camera_id="linkvideo_167024", server="b2o-vcore271.video.goodline.info",
    ip="", rtsp_port=loaded.port, ping_text="", tcp_text="",
    rtsp_url=loaded.url, rtsp_host=loaded.host,
)

progress = (
    b"Input #0, rtsp, from '<redacted>':\n"
    b"  Stream #0:0: Video: h264 (High), yuv420p, 1920x1080, 25 fps\n"
    b"  Stream #0:1: Audio: aac (LC), 48000 Hz, stereo\n"
    b"Output #0, mpegts, to 'pipe:1':\n"
    b"frame=100\nout_time_us=4000000\nprogress=end\n"
)

def fake_ffmpeg(args, **kwargs):
    assert args[0] == "ffmpeg-test.exe"
    assert stream_url in args
    assert args[args.index("-t") + 1] == "4"
    assert kwargs["timeout"] == 14
    return SimpleNamespace(returncode=0, stdout=b"x" * 500_000,
                           stderr=progress)

with patch("linkvideo_vpn_helper.services.camera_live_probe.subprocess.run",
           side_effect=fake_ffmpeg):
    stats = _probe_rtsp(report, ffmpeg_path="ffmpeg-test.exe")
assert stats is not None
assert stats.video_codec == "H264"
assert stats.audio_codec == "AAC"
assert stats.resolution == "1920x1080"
assert round(stats.fps, 1) == 25.0
assert round(stats.bitrate_mbps, 2) == 1.0

with patch("linkvideo_vpn_helper.services.camera_live_probe._cached_ffmpeg",
           return_value="ffmpeg-test.exe"), patch(
    "linkvideo_vpn_helper.services.camera_live_probe.subprocess.run",
    side_effect=fake_ffmpeg
), patch("linkvideo_vpn_helper.services.camera_live_probe._ping_windows",
         return_value="ICMP: нет ответа."), patch(
    "linkvideo_vpn_helper.services.camera_live_probe._test_tcp",
    return_value="TCP:10021 — открыт с этого компьютера."
) as tcp:
    measure = probe_live_camera(report)
assert measure.source == "RTSP"
assert measure.resolution == "1920x1080"
assert measure.video_codec == "H264"
assert measure.audio_codec == "AAC"
assert measure.fps == "25.0"
assert "1.00" in measure.bitrate_mbps
assert "Поток получен" in measure.last_stream
tcp.assert_called_once_with("vpn05.linkvideo.ru", 10021)
assert secret not in measure.detail
assert secret not in measure.connectivity

print("CORE TESTS 3.0.16 B2O MAIN RTSP FFmpeg FPS/CODECS/BITRATE OK")
