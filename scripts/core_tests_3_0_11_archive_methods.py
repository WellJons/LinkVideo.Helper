from __future__ import annotations

import hashlib
import inspect
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
    assert "-nostdin" in args
    assert args[args.index("-rw_timeout") + 1] == "30000000"

    audio = methods.ffmpeg_audio_args(
        r"C:\Tools\ffmpeg.exe",
        "https://dvr/main/camera/playlist.m3u8",
        Path(r"C:\Temp\archive.mp4"),
    )
    assert "-c" in audio and "copy" in audio
    assert "-an" not in audio
    assert "-nostdin" in audio
    assert audio[audio.index("-rw_timeout") + 1] == "30000000"


def _fake_ffmpeg_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("ffmpeg-test/bin/ffmpeg.exe", b"MZ" + (b"x" * (11 * 1024 * 1024)))
    return buffer.getvalue()


class _Response:
    def __init__(self, payload: bytes, url: str):
        self._stream = io.BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload))}
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self._stream.read(size)

    def geturl(self):
        return self._url


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
    digest = hashlib.sha256(payload).hexdigest().encode("ascii") + b"  ffmpeg-test.zip\n"
    resolved_url = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-test.zip"
    original_urlopen = methods.urllib.request.urlopen
    calls = 0

    def fake_urlopen(request, timeout=0):
        nonlocal calls
        calls += 1
        assert timeout > 0
        url = request.full_url
        if url.endswith(".sha256"):
            assert url == resolved_url + ".sha256"
            return _Response(digest, url)
        return _Response(payload, resolved_url)

    with tempfile.TemporaryDirectory(prefix="lvh_ffmpeg_test_") as td:
        service = _FakeService(Path(td))
        updates: list[tuple[str, str]] = []
        methods.urllib.request.urlopen = fake_urlopen
        try:
            path = methods._download_ffmpeg(service, lambda a, b: updates.append((a, b)))
            assert Path(path).is_file()
            assert calls == 2, "first use must fetch both the archive and its SHA-256 sidecar"
            assert any("100%" in detail for _title, detail in updates), updates[-5:]
            assert any("Скачано" in detail for _title, detail in updates)
            assert any("SHA-256" in detail for _title, detail in updates)

            updates.clear()
            second = methods._download_ffmpeg(service, lambda a, b: updates.append((a, b)))
            assert second == path
            assert calls == 2, "cached FFmpeg must not be downloaded twice"
        finally:
            methods.urllib.request.urlopen = original_urlopen


def test_ffmpeg_rejects_checksum_mismatch() -> None:
    payload = _fake_ffmpeg_zip()
    resolved_url = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-test.zip"
    original_urlopen = methods.urllib.request.urlopen

    def fake_urlopen(request, timeout=0):
        assert timeout > 0
        url = request.full_url
        if url.endswith(".sha256"):
            return _Response(("0" * 64 + "  ffmpeg-test.zip\n").encode("ascii"), url)
        return _Response(payload, resolved_url)

    with tempfile.TemporaryDirectory(prefix="lvh_ffmpeg_hash_test_") as td:
        service = _FakeService(Path(td))
        methods.urllib.request.urlopen = fake_urlopen
        try:
            try:
                methods._download_ffmpeg(service)
            except RuntimeError as exc:
                assert "SHA-256" in str(exc)
            else:
                raise AssertionError("FFmpeg archive with a mismatched SHA-256 was accepted")
            assert not (Path(td) / "ffmpeg.exe").exists()
        finally:
            methods.urllib.request.urlopen = original_urlopen


def test_atomic_mp4_validation() -> None:
    with tempfile.TemporaryDirectory(prefix="lvh_mp4_validation_") as td:
        root = Path(td)
        output = root / "archive.mp4"
        output.write_bytes(b"old-valid-file")

        invalid = methods._create_staged_output(output)
        invalid.write_bytes(b'{"error":"archive unavailable"}')
        try:
            methods._commit_staged_output(invalid, output)
        except RuntimeError as exc:
            assert "MP4" in str(exc)
        else:
            raise AssertionError("textual DVR response was accepted as MP4")
        assert output.read_bytes() == b"old-valid-file", "invalid download overwrote the previous archive"
        invalid.unlink(missing_ok=True)

        valid = methods._create_staged_output(output)
        # 24-byte ftyp box followed by one harmless free box.
        valid.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00\x00\x00\x08free")
        assert methods._is_probable_mp4(valid)
        methods._commit_staged_output(valid, output)
        assert output.read_bytes()[4:8] == b"ftyp"


def test_destination_staging_starts_after_transport_preflight() -> None:
    """A preparation failure must not litter the user's selected folder."""
    audio = inspect.getsource(methods._download_with_ffmpeg_audio)
    no_audio = inspect.getsource(methods._download_no_audio)
    curl = inspect.getsource(methods._download_curl)

    marker = "staged_output = _create_staged_output(output)"
    assert audio.index("self.ensure_ffmpeg(") < audio.index(marker)
    assert no_audio.index("self.ensure_ffmpeg(") < no_audio.index(marker)
    assert curl.index('raise RuntimeError("Нет подтверждённых DVR-участков для Curl")') < curl.index(marker)
    assert curl.index("curl = _curl_executable()") < curl.index(marker)
    assert "except Exception:\n        shutil.rmtree(tmp, ignore_errors=True)\n        raise" in curl


def test_release_packaging_contract() -> None:
    spec = (ROOT / "LinkVideo.Helper.spec").read_text(encoding="utf-8")
    build = (ROOT / "scripts" / "build_next_installer.ps1").read_text(encoding="utf-8")
    verifier = (ROOT / "scripts" / "verify_release.ps1").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "windows-build.yml").read_text(encoding="utf-8")
    app = (ROOT / "linkvideo_vpn_helper" / "app.py").read_text(encoding="utf-8")
    selftest = (ROOT / "installer_next" / "selftest_windows.go").read_text(encoding="utf-8")

    assert "ffmpeg = root /" not in spec
    assert "installer payload correctly excludes ffmpeg.exe" in build
    assert "LinkVideo.Helper_Setup.exe" in build
    assert "LinkVideo.Helper_Setup_Next.exe" not in build
    assert "actions/upload-artifact" not in workflow
    assert "build_setup.bat" not in workflow
    assert "scripts/verify_release.ps1" in workflow
    assert "Create or update private RC draft" in workflow
    assert '"rc-$version"' in workflow
    assert "Self-test exact produced Setup payload" in verifier
    assert "--self-test" in verifier
    assert "LinkVideo.Helper_Payload_${version}.zip" in workflow
    assert "LinkVideo.Helper_Payload_${version}.json" in workflow
    assert 'hasArg("--self-test")' in selftest
    assert 'strings.EqualFold(entry.Name(), "ffmpeg.exe")' in selftest
    assert "install_archive_download_methods()" in app
    assert app.index("install_archive_download_methods()") < app.index("install_archive_download_ux()")

    methods_source = (ROOT / "linkvideo_vpn_helper" / "services" / "archive_download_methods.py").read_text(
        encoding="utf-8"
    )
    core_source = (ROOT / "linkvideo_vpn_helper" / "services" / "archive_service_core.py").read_text(
        encoding="utf-8"
    )
    assert "return _download_with_ffmpeg_audio(self, discovery, output, progress, cancel_event)" in core_source
    assert "return original_download(self, discovery, output, progress, cancel_event)" not in methods_source
    assert "ArchiveService.ensure_ffmpeg =" not in methods_source
    assert "ArchiveService.download =" not in methods_source
    assert "return _download_ffmpeg(self, progress, cancel_event)" in core_source
    assert methods_source.count("staged_output = _create_staged_output(output)") == 3
    assert methods_source.count("_commit_staged_output(staged_output, output)") == 5
    assert "staged_output.unlink(missing_ok=True)" in methods_source
    assert "_is_probable_mp4" in methods_source


def main() -> None:
    test_method_contract()
    test_curl_url_contract()
    test_no_audio_contract()
    test_ffmpeg_first_use_progress_and_cache()
    test_ffmpeg_rejects_checksum_mismatch()
    test_atomic_mp4_validation()
    test_destination_staging_starts_after_transport_preflight()
    test_release_packaging_contract()
    print("CORE TESTS 3.0.11 ARCHIVE METHODS OK")


if __name__ == "__main__":
    main()
