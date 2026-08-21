from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_name(name: str) -> str:
    p = PurePosixPath(str(name).replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise ValueError(f"Unsafe archive path: {name}")
    return p.as_posix()


def read_previous(zip_path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = safe_name(info.filename)
            result[name] = sha256_bytes(zf.read(info))
    return result


def read_current(root: Path) -> dict[str, tuple[Path, str]]:
    result: dict[str, tuple[Path, str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        name = safe_name(path.relative_to(root).as_posix())
        result[name] = (path, sha256_file(path))
    return result


def validate_version(value: str) -> str:
    value = str(value or "").strip()
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", value):
        raise ValueError(f"Invalid version: {value}")
    return value


def build_bundle(
    previous_zip: Path,
    current_dir: Path,
    from_version: str,
    to_version: str,
    out_dir: Path,
    *,
    full_runtime: bool = False,
) -> tuple[Path, Path]:
    """Build an authenticated updater payload.

    Differential mode includes only files that differ from ``previous_zip``.
    ``full_runtime`` deliberately includes every file from the target runtime.
    The latter is used when the exact historical binary baseline cannot be
    trusted: ProductVersion still gates the source version, while every target
    runtime file is replaced with the verified 3.0.11 copy. The old baseline is
    used only to identify known obsolete files that should be deleted.
    """
    from_version = validate_version(from_version)
    to_version = validate_version(to_version)
    if from_version == to_version:
        raise ValueError("from_version and to_version must differ")
    previous = read_previous(previous_zip)
    current = read_current(current_dir)

    if full_runtime:
        changed = sorted(current)
    else:
        changed = sorted(name for name, (_path, digest) in current.items() if previous.get(name) != digest)
    deleted = sorted(name for name in previous if name not in current)
    if not changed and not deleted:
        raise ValueError("No file differences found")

    out_dir.mkdir(parents=True, exist_ok=True)
    payload_path = out_dir / "patch_payload.zip"
    manifest_path = out_dir / "patch_manifest.json"

    with zipfile.ZipFile(payload_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name in changed:
            zf.write(current[name][0], name)

    manifest = {
        "format": 1,
        "from_version": from_version,
        "to_version": to_version,
        "mode": "full-runtime" if full_runtime else "differential",
        "changed": {
            name: {
                "sha256": current[name][1],
                "size": current[name][0].stat().st_size,
            }
            for name in changed
        },
        "deleted": deleted,
        "payload_sha256": sha256_file(payload_path),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload_path, manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build LinkVideo.Helper authenticated update bundle")
    parser.add_argument("--from-zip", required=True, type=Path)
    parser.add_argument("--to-dir", required=True, type=Path)
    parser.add_argument("--from-version", required=True)
    parser.add_argument("--to-version", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--full-runtime",
        action="store_true",
        help="Include every target runtime file instead of only binary differences",
    )
    args = parser.parse_args()
    payload, manifest = build_bundle(
        args.from_zip,
        args.to_dir,
        args.from_version,
        args.to_version,
        args.out_dir,
        full_runtime=args.full_runtime,
    )
    print(payload)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
