from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

from make_patch_bundle import build_bundle, sha256_file


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="lv-full-runtime-") as raw:
        root = Path(raw)
        old_zip = root / "old.zip"
        current = root / "current"
        out = root / "out"
        current.mkdir()

        # One file is intentionally byte-identical. Differential mode would
        # omit it; full-runtime mode must still carry it so the target runtime
        # is authoritative even if the exact historical 3.0.10 binary baseline
        # is unavailable.
        with zipfile.ZipFile(old_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("same.txt", b"same")
            zf.writestr("changed.txt", b"old")
            zf.writestr("obsolete.txt", b"remove-me")

        (current / "same.txt").write_bytes(b"same")
        (current / "changed.txt").write_bytes(b"new")
        (current / "new.txt").write_bytes(b"brand-new")

        payload_path, manifest_path = build_bundle(
            old_zip,
            current,
            "3.0.10",
            "3.0.11",
            out,
            full_runtime=True,
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        assert manifest["mode"] == "full-runtime", manifest
        assert set(manifest["changed"]) == {"same.txt", "changed.txt", "new.txt"}, manifest
        assert manifest["deleted"] == ["obsolete.txt"], manifest
        assert manifest["payload_sha256"] == sha256_file(payload_path), manifest

        with zipfile.ZipFile(payload_path, "r") as zf:
            assert set(zf.namelist()) == {"same.txt", "changed.txt", "new.txt"}
            assert zf.read("same.txt") == b"same"
            assert zf.read("changed.txt") == b"new"

    print("CORE TESTS 3.0.11 FULL RUNTIME UPDATE OK")


if __name__ == "__main__":
    main()
