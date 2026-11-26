"""One solid LZMA2-compressed runtime; no second Chromium or Inno payload."""
import sys
import tarfile
from pathlib import Path

root, output = map(Path, sys.argv[1:])
assert (root / "Uninstall.exe").is_file()
with tarfile.open(output, "w:xz", preset=6, format=tarfile.PAX_FORMAT) as tar:
    for file in sorted(root.rglob("*")):
        if file.is_file():
            tar.add(file, arcname=file.relative_to(root).as_posix(), recursive=False)
