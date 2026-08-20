# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPEC).resolve().parent if 'SPEC' in globals() else Path.cwd()
datas = [(str(root / "icon.ico"), ".")]
# FFmpeg is intentionally not bundled. Archive downloads fetch and validate it
# on first FFmpeg-based use, then reuse the per-user LocalAppData cache.

excludes = [
    "PySide6.QtWebEngine", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtUiTools",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
]

a = Analysis(
    ["linkvideo_vpn_helper\\app.py"],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LinkVideo.Helper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="icon.ico",
    version=str(root / "build_version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name="LinkVideo.Helper")
