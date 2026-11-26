"""Qt-free settings for the isolated v4 profile; original v3 storage is untouched."""
from __future__ import annotations

import configparser
import json
import os
import re
import tempfile
from pathlib import Path
from threading import RLock


_EXACT = {"vpn_servers/custom_v2", "vpn_servers/disabled_v2"}
_PREFIXES = ("archive/extra_cluster_enabled/", "archive/custom_servers/", "vpn_create/start_ip/")


def _allowed(key):
    return key in _EXACT or any(key.startswith(p) for p in _PREFIXES)


def _legacy_key(text):
    text = re.sub(r"%U([0-9A-Fa-f]{4})|%([0-9A-Fa-f]{2})",
                  lambda m: chr(int(m.group(1) or m.group(2), 16)), text)
    return text.replace("\\", "/")


def _legacy_value(raw):
    """Decode QSettings INI strings/bools for the supported non-secret keys."""
    raw = raw.strip()
    if raw.startswith('"'):
        if not raw.endswith('"'):
            raise ValueError("Unterminated quoted legacy setting")
        raw = raw[1:-1]
    result = []
    i = 0
    escapes = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f",
               "\\": "\\", '"': '"', "a": "\a", "v": "\v", "0": "\0"}
    while i < len(raw):
        c = raw[i]
        i += 1
        if c != "\\":
            result.append(c)
            continue
        if i >= len(raw):
            raise ValueError("Incomplete legacy escape")
        c = raw[i]
        i += 1
        if c == "x":
            match = re.match(r"[0-9a-fA-F]{1,4}", raw[i:])
            if not match:
                raise ValueError("Invalid legacy hex escape")
            result.append(chr(int(match.group(), 16)))
            i += len(match.group())
        elif c in escapes:
            result.append(escapes[c])
        else:
            raise ValueError("Unsupported legacy escape")
    value = "".join(result)
    if value.startswith("@@"):
        value = value[1:]
    elif value.startswith("@String(") and value.endswith(")"):
        value = value[8:-1]
    elif value.startswith("@"):
        raise ValueError("Unsupported legacy setting type")
    return value


def migrate_legacy(path):
    ini = configparser.ConfigParser(interpolation=None, delimiters=("=",), strict=True)
    ini.optionxform = str
    with Path(path).open("r", encoding="utf-8-sig") as file:
        ini.read_file(file)
    result = {}
    for section in ini.sections():
        for name, raw in ini.items(section):
            key = _legacy_key(name if section == "General" else section + "/" + name)
            if not _allowed(key):
                continue
            value = _legacy_value(raw)
            if key.startswith("archive/extra_cluster_enabled/"):
                if value.lower() not in ("true", "false", "1", "0"):
                    raise ValueError("Invalid legacy boolean")
                value = value.lower() in ("true", "1")
            elif key in _EXACT or key.startswith("archive/custom_servers/"):
                # Validate but preserve the exact serialized value, including Cyrillic.
                if not isinstance(json.loads(value), list):
                    raise ValueError("Invalid legacy list")
            result[key] = value
    return result


class ProfileSettings:
    def __init__(self, path, *, legacy=None):
        self.path = Path(path)
        self.lock = RLock()
        self.data = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Invalid v4 settings")
            self.data = {k: v for k, v in data.items() if _allowed(k)}
        elif legacy is not None and Path(legacy).exists():
            self.data = migrate_legacy(legacy)
            # Original INI stays intact; never silently replace a failed migration.
            self.sync()

    def value(self, key, default=None, type=None):
        with self.lock:
            value = self.data.get(key, default)
            if type is None or value is None:
                return value
            if type is bool and isinstance(value, str):
                return value.lower() not in ("", "false", "0")
            return type(value)

    def setValue(self, key, value):
        if not _allowed(key):
            raise ValueError("Unsupported persistent setting")
        if not isinstance(value, (str, bool, int, float)):
            raise ValueError("Unsupported persistent value")
        with self.lock:
            old = self.data.copy()
            self.data[key] = value
            try:
                self.sync()
            except Exception:
                self.data = old
                raise

    def remove(self, key):
        with self.lock:
            old = self.data.copy()
            self.data = {k: v for k, v in self.data.items()
                         if k != key and not k.startswith(key + "/")}
            if self.data != old:
                try:
                    self.sync()
                except Exception:
                    self.data = old
                    raise

    def sync(self):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(self.data, file, ensure_ascii=False, indent=2)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(tmp, self.path)
            finally:
                Path(tmp).unlink(missing_ok=True)
