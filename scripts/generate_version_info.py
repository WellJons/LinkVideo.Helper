from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "linkvideo_vpn_helper" / "version.py"
OUT = ROOT / "build_version_info.txt"

text = VERSION_FILE.read_text(encoding="utf-8")
m = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.M)
if not m:
    raise SystemExit("APP_VERSION not found")
version = m.group(1).strip()
nums = [int(x) for x in version.split("-")[0].split(".")]
while len(nums) < 4:
    nums.append(0)
nums = nums[:4]
quad = ", ".join(str(x) for x in nums)

content = f'''VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({quad}),
    prodvers=({quad}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'LinkVideo'),
          StringStruct('FileDescription', 'LinkVideo.Helper'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', 'LinkVideo.Helper'),
          StringStruct('OriginalFilename', 'LinkVideo.Helper.exe'),
          StringStruct('ProductName', 'LinkVideo.Helper'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
'''
OUT.write_text(content, encoding="utf-8")
print(f"Windows EXE version resource generated: {version}")
