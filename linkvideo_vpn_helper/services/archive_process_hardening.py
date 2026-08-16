from __future__ import annotations

import os
import subprocess
from pathlib import Path


_INSTALLED = False


def install_archive_process_hardening() -> None:
    """Prevent an FFmpeg HLS socket from holding archive UI indefinitely.

    Some archive progress loops read ``ffmpeg -progress pipe:1`` line by line.
    If an HLS TCP connection stays established but stops delivering bytes,
    ``readline()`` cannot observe Esc until FFmpeg itself unwinds. Add FFmpeg's
    AVIO read/write timeout to HTTP(S) inputs centrally so every current and
    future archive invocation gets the same stall limit without changing normal
    local concat/remux commands.
    """
    global _INSTALLED
    if _INSTALLED or getattr(subprocess.Popen, "_lv_archive_stall_guard", False):
        _INSTALLED = True
        return

    original = subprocess.Popen

    class ArchiveSafePopen(original):
        _lv_archive_stall_guard = True

        def __init__(self, *args, **kwargs):
            if args:
                command = args[0]
            else:
                command = kwargs.get("args")

            if isinstance(command, (list, tuple)) and command:
                items = [str(x) for x in command]
                exe = Path(items[0]).name.lower()
                if exe in {"ffmpeg", "ffmpeg.exe"}:
                    try:
                        input_index = items.index("-i")
                    except ValueError:
                        input_index = -1
                    input_url = items[input_index + 1] if 0 <= input_index < len(items) - 1 else ""
                    if input_url.lower().startswith(("http://", "https://")):
                        # 30 seconds without network I/O is a failed archive
                        # source, not a reason to freeze the operator UI.
                        if "-rw_timeout" not in items[: max(0, input_index)]:
                            items[input_index:input_index] = ["-rw_timeout", "30000000"]
                        if "-nostdin" not in items:
                            items.insert(1, "-nostdin")
                        if args:
                            args = (items, *args[1:])
                        else:
                            kwargs["args"] = items
            super().__init__(*args, **kwargs)

    subprocess.Popen = ArchiveSafePopen
    _INSTALLED = True
