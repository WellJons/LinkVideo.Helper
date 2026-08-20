from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from linkvideo_vpn_helper.services import archive_download_methods as methods


class _Slice:
    host = "b2o-vcore214.video.goodline.info"
    app = "main"
    stream = "linkvideo_207713"
    start = 1_760_000_000.4
    end = 1_760_000_300.6


def test_method_contract() -> None:
    assert methods.ARCHIVE_DOWNLOAD_METHODS == (
        ("1. FFmpeg", "ffmpeg"),
        ("2. Curl", "curl"),
        ("3. Без звука", "ffmpeg_no_audio"),
    )
    assert methods.DEFAULT_ARCHIVE_DOWNLOAD_METHOD == "ffmpeg"


def test_curl_url_contract() -> None:
    url = methods.curl_export_url(_Slice())
    assert url == (
        "http://b2o-vcore214.video.goodline.info:8086/manage/dvr/export_mp4/"
        "main/linkvideo_207713?start=1760000000&end=1760000301"
    ), url


def test_no_audio_contract() -> None:
    args = methods.ffmpeg_no_audio_args(
        r"C:\Tools\ffmpeg.exe",
        "https://dvr/main/camera/playlist.m3u8",
        Path(r"C:\Temp\archive.mp4"),
    )
    assert "-c:v" in args and "copy" in args
    assert "-an" in args
    assert "-c" not in args, "no-audio path must not accidentally use generic -c copy"


def _fake_ffmpeg_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("ffmpeg-test/bin/ffmpeg.exe", b"MZ" + (b"x" * (11 * 1024 * 1024)))
    return buffer.getvalue()


class _Response:
    def __init__(self, payload: bytes):
        self._stream = io.BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self._stream.read(size)


class _FakeService:
    def __init__(self, tools: Path):
        self._tools = tools

    def tools_dir(self) -> Path:
        return self._tools

    def _ffmpeg_candidates(self):
        return [self._tools / "ffmpeg.exe"]

    @staticmethod
    def _ffmpeg_usable(path: Path) -> bool:
        return path.is_file() and path.stat().st_size > 10 * 1024 * 1024


def test_ffmpeg_first_use_progress_and_cache() -> None:
    payload = _fake_ffmpeg_zip()
    original_urlopen = methods.urllib.request.urlopen
    calls = 0

    def fake_urlopen(_request, timeout=0):
        nonlocal calls
        calls += 1
        assert timeout > 0
        return _Response(payload)

    with tempfile.TemporaryDirectory(prefix="lvh_ffmpeg_test_") as td:
        service = _FakeService(Path(td))
        updates: list[tuple[str, str]] = []
        methods.urllib.request.urlopen = fake_urlopen
        try:
            path = methods._download_ffmpeg(service, lambda a, b: updates.append((a, b)))
            assert Path(path).is_file()
            assert calls == 1
            assert any("100%" in detail for _title, detail in updates), updates[-5:]
            assert any("Скачано" in detail for _title, detail in updates)

            updates.clear()
            second = methods._download_ffmpeg(service, lambda a, b: updates.append((a, b)))
            assert second == path
            assert calls == 1, "cached FFmpeg must not be downloaded twice"
        finally:
            methods.urllib.request.urlopen = original_urlopen


def test_release_packaging_contract() -> None:
    spec = (ROOT / "LinkVideo.Helper.spec").read_text(encoding="utf-8")
    build = (ROOT / "scripts" / "build_next_installer.ps1").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "windows-build.yml").read_text(encoding="utf-8")
    app = (ROOT / "linkvideo_vpn_helper" / "app.py").read_text(encoding="utf-8")
    selftest = (ROOT / "installer_next" / "selftest_windows.go").read_text(encoding="utf-8")

    assert "ffmpeg = root /" not in spec
    assert "installer payload correctly excludes ffmpeg.exe" in build
    assert "LinkVideo.Helper_Setup.exe" in build
    assert "LinkVideo.Helper_Setup_Next.exe" not in build
    assert "actions/upload-artifact" not in workflow
    assert "build_setup.bat" not in workflow
    assert "Create or update private RC draft" in workflow
    assert '"rc-$version"' in workflow
    assert "Self-test exact produced Setup payload" in workflow
    assert "--self-test" in workflow
    assert "LinkVideo.Helper_Payload_${version}.zip" in workflow
    assert "LinkVideo.Helper_Payload_${version}.json" in workflow
    assert 'hasArg("--self-test")' in selftest
    assert 'strings.EqualFold(entry.Name(), "ffmpeg.exe")' in selftest
    assert "install_archive_download_methods()" in app
    assert app.index("install_archive_download_methods()") < app.index("install_archive_download_ux()")


def main() -> None:
    test_method_contract()
    test_curl_url_contract()
    test_no_audio_contract()
    test_ffmpeg_first_use_progress_and_cache()
    test_release_packaging_contract()
    print("CORE TESTS 3.0.11 ARCHIVE METHODS OK")


if __name__ == "__main__":
    main()
