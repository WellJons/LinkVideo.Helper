from __future__ import annotations

"""Offline HLS regressions and manual FFmpeg command safety.

Never call real B2O/Nimble endpoints: all HTTP requests and FFmpeg execution
are patched. These reproduce nested variants, skipped HLS gaps and expired
signed playlists.
"""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from linkvideo_vpn_helper.services.archive_service import (
    ArchiveService, ArchiveCamera, ArchiveDiscovery,
)
from linkvideo_vpn_helper.services import archive_download_methods as dl


start = datetime(2026, 11, 5, 4, 0, tzinfo=timezone.utc).timestamp()
master = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=720x576
chunks?quality=middle
"""
chunks = """#EXTM3U
#EXT-X-TARGETDURATION:10
#EXT-X-PROGRAM-DATE-TIME:2026-11-05T04:00:00Z
#EXTINF:10.0,
part-0.ts
#EXTINF:10.0,
#EXT-X-GAP
missing.ts
#EXT-X-PROGRAM-DATE-TIME:2026-11-05T04:00:20Z
#EXTINF:10.0,
part-2.ts
#EXT-X-ENDLIST
"""
srv = ArchiveService.__new__(ArchiveService)
camera = ArchiveCamera(
    "123456", "123456", "vcore.example.org", "camera_test",
    "wmsAuthSign=secret", 0, {},
)
assert srv._first_nested_playlist(master, "https://vcore.example.org/main/playlist.m3u8") == (
    "https://vcore.example.org/main/chunks?quality=middle"
)
seg = srv._parse_hls_segments(chunks, "https://vcore.example.org/main/chunks", start)
assert len(seg) == 2, seg
assert [(round(s["start"]-start),round(s["end"]-start)) for s in seg] == [
    (0,10),(20,30)]
assert srv._parse_hls_segments(chunks, "https://vcore.example.org/main/chunks",
                              start + 60)[0]["start"] == start

urls = []
def fetch(url, timeout):
    urls.append(url)
    return chunks if "chunks?" in url else master
with patch.object(srv, "_http_hls_text",side_effect=fetch):
    result,errors = srv._probe_hls_host(
        "vcore.example.org",camera,start,start+30,timeout=5)
assert result is not None and result["segments"] == 2
assert result["duration"] == 20.0, result
assert len(result["slices"]) == 2, result["slices"]
assert any("chunks?" in u for u in urls), urls
assert not any("missing.ts" in u for u in urls)

# A media playlist whose absolute timestamps are outside the request must
# not be counted as covering that requested period.
with patch.object(srv, "_http_hls_text",return_value=chunks):
    result,errors = srv._probe_hls_host(
        "vcore.example.org",camera,start+120,start+160,timeout=5)
assert result is None and errors, (result,errors)

# The total cannot exceed the requested window even if EXTINF overlaps.
overlap = """#EXTM3U
#EXT-X-PROGRAM-DATE-TIME:2026-11-05T04:00:00Z
#EXTINF:30,
a.ts
#EXT-X-PROGRAM-DATE-TIME:2026-11-05T04:00:10Z
#EXTINF:30,
b.ts
"""
with patch.object(srv,"_http_hls_text",return_value=overlap):
    result,errors=srv._probe_hls_host(
        "vcore.example.org",camera,start,start+30,timeout=5)
assert result is not None and result["duration"] == 30.0, result

assert dl.validate_manual_ffmpeg_command(dl.MANUAL_FFMPEG_TEMPLATE)
assert dl.validate_manual_ffmpeg_command(dl.MANUAL_FFMPEG_NO_AUDIO_TEMPLATE)
for bad in (
    'cmd /c ffmpeg -i "{input}" "{output}"',
    'ffmpeg -i "file.txt" "{output}"',
    'ffmpeg -i "{input}" target.mp4',
    'ffmpeg -i "{input}" -i "other" "{output}"',
):
    try:
        dl.validate_manual_ffmpeg_command(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe manual command accepted: "+bad)

args = dl.ffmpeg_manual_args(
    r"C:\Tools\ffmpeg.exe",
    "https://vcore.example.org/video.m3u8?wmsAuthSign=secret",
    Path(r"C:\Users\Editor Name\Video\output.download.mp4"),
    dl.MANUAL_FFMPEG_TEMPLATE,
)
assert args[0] == r"C:\Tools\ffmpeg.exe"
assert "-headers" in args and "Origin:" in args[args.index("-headers")+1]
assert args[-1] == r"C:\Users\Editor Name\Video\output.download.mp4"
assert args[args.index("-i")+1].endswith("wmsAuthSign=secret")
assert "secret" not in dl._safe_hls_error(
    "HTTP error https://host/path.m3u8?wmsAuthSign=secret")

MP4 = b"\x00\x00\x00\x18ftypisom" + b"\0"*12
with TemporaryDirectory(prefix="lvh_hls_regression_") as td:
    tmp=Path(td)
    outcome=tmp/"hls.mp4"
    calls=[]
    def ffmpeg_run(cmd,**kwargs):
        calls.append(cmd)
        target=Path(cmd[-1])
        if len(calls)==1:
            target.write_bytes(b"not-a-video")
            return 0, "Invalid data: https://dvr/playlist.m3u8?token=topsecret"
        target.write_bytes(MP4)
        return 0, ""
    with patch.object(dl,"_run_ffmpeg_progress",side_effect=ffmpeg_run):
        success,error=dl._ffmpeg_hls_attempts(
            lambda attempt:["ffmpeg","-i","https://dvr/a.m3u8",str(outcome)],
            outcome,base_done=0,item_duration=15,total=15)
    assert success and len(calls)==2
    assert dl._is_probable_mp4(outcome)

    discovery=ArchiveDiscovery(
        camera=camera,requested_start=start,requested_end=start+20,
        slices=[],gaps=[],checked_hosts=["vcore.example.org"],errors=[],
        hls_fallback_url="https://vcore.example.org/main/old.m3u8?wmsAuthSign=expired",
        hls_fallback_host="vcore.example.org",
        hls_fallback_method="player playlist",hls_fallback_duration=20,
    )
    service=SimpleNamespace(
        ensure_ffmpeg=lambda *_: "ffmpeg",
        _probe_hls_host=lambda *a,**k: (
            {"url":"https://vcore.example.org/main/new.m3u8?wmsAuthSign=new"},[]),
        _lv_archive_manual_command=dl.MANUAL_FFMPEG_TEMPLATE,
    )
    captured=[]
    def mock_progress(cmd,**kwargs):
        captured.append(cmd)
        if len(captured)==1:
            return 1,"HTTP 403 old signed link"
        Path(cmd[-1]).write_bytes(MP4)
        return 0,""
    with patch.object(dl,"_run_ffmpeg_progress",side_effect=mock_progress):
        result=dl._download_with_ffmpeg_audio(service,discovery,tmp/"archive.mp4")
    assert result.output.is_file() and dl._is_probable_mp4(result.output)
    assert len(captured)==2
    assert "old.m3u8" in captured[0][captured[0].index("-i")+1]
    assert "new.m3u8" in captured[1][captured[1].index("-i")+1]

    # Automatic audio fallback keeps video in copy mode, but converts
    # incompatible G.711/PCM audio into AAC on the second attempt.
    service._lv_archive_manual_command = ""
    captured.clear()
    def codec_retry(cmd, **kwargs):
        captured.append(cmd)
        if len(captured) == 1:
            return 1, "Could not write header for audio codec pcm_alaw"
        Path(cmd[-1]).write_bytes(MP4)
        return 0, ""
    with patch.object(dl,"_run_ffmpeg_progress",side_effect=codec_retry):
        result=dl._download_with_ffmpeg_audio(
            service, discovery, tmp / "audio-fallback.mp4")
    assert result.output.is_file()
    assert "-c:a" in captured[1] and "aac" in captured[1]
    assert "-c:v" in captured[1] and "copy" in captured[1]

print("CORE TESTS 3.0.18 HLS PLAYLIST AND MANUAL FFMPEG OK")
