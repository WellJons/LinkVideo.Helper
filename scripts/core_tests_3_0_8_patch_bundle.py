from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.make_patch_bundle import build_bundle

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    old_zip = root / "old.zip"
    with zipfile.ZipFile(old_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("LinkVideo.Helper.exe", b"old-app")
        zf.writestr("_internal/keep.dll", b"same")
        zf.writestr("_internal/delete.dll", b"remove-me")

    new = root / "new"
    (new / "_internal").mkdir(parents=True)
    (new / "LinkVideo.Helper.exe").write_bytes(b"new-app")
    (new / "_internal/keep.dll").write_bytes(b"same")
    (new / "_internal/new.dll").write_bytes(b"new-file")

    payload, manifest_path = build_bundle(old_zip, new, "3.0.8", "3.0.9", root / "out")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["from_version"] == "3.0.8"
    assert manifest["to_version"] == "3.0.9"
    assert set(manifest["changed"]) == {"LinkVideo.Helper.exe", "_internal/new.dll"}
    assert manifest["deleted"] == ["_internal/delete.dll"]
    with zipfile.ZipFile(payload, "r") as zf:
        assert set(zf.namelist()) == {"LinkVideo.Helper.exe", "_internal/new.dll"}
        assert zf.read("LinkVideo.Helper.exe") == b"new-app"

print("CORE TESTS 3.0.8 PATCH BUNDLE PLANNER OK")
