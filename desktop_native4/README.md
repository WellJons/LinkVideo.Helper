# LinkVideo.Helper 4 — native direction

Source of truth: released 3.0.27 PySide6 desktop (login, MainWindow, native widgets, themes, animations, original workflows).

This branch starts from the original v3.0.27 release, **not** from the Electron rewrite. Entry: desktop_native4/native_entry.py.

Native preview settings use LinkVideo/LinkVideo.Helper.Native4, not stable 3.x. Credentials are not silently migrated. The old update channel and 3.x background task are disabled until independent v4 implementations are reviewed.

The Electron experiment remains isolated on v4/electron. This new native baseline does not claim every experimental Electron-only feature was integrated.
