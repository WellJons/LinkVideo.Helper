from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create private LinkVideo.Helper payload baseline for future differential patches")
    parser.add_argument("--source", type=Path, default=Path("dist/LinkVideo.Helper"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("release_payload"))
    args = parser.parse_args()

    version = str(args.version).strip()
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", version):
        raise SystemExit(f"Invalid version: {version}")
    source = args.source.resolve()
    if not (source / "LinkVideo.Helper.exe").exists():
        raise SystemExit(f"LinkVideo.Helper.exe is missing in {source}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = args.out_dir / f"LinkVideo.Helper_Payload_{version}.zip"
    manifest_path = args.out_dir / f"LinkVideo.Helper_Payload_{version}.json"
    files: dict[str, dict[str, object]] = {}

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            name = path.relative_to(source).as_posix()
            zf.write(path, name)
            files[name] = {"sha256": sha256_file(path), "size": path.stat().st_size}

    data = {
        "format": 1,
        "version": version,
        "payload": zip_path.name,
        "payload_sha256": sha256_file(zip_path),
        "files": files,
    }
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(zip_path)
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
