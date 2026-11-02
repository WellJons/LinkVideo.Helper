from __future__ import annotations

"""Simple invitation-only Telegram access with persistent SQLite membership.

Bot usernames are public. This gate uses random one-use deep-link secrets to
enroll a Telegram account automatically and records cryptographically
random invitation hashes (SHA-256) and Telegram IDs.
No B2O passwords, camera tokens or bot tokens are stored in this database.
"""

import hashlib
from contextlib import contextmanager
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

_INVITE = re.compile(r"[A-Za-z0-9_-]{24,64}\Z")


class InviteStore:
    def __init__(self, filename: str | os.PathLike[str]):
        self.path = Path(filename).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS invitations ("
                "secret_hash TEXT PRIMARY KEY,"
                "expires INTEGER NOT NULL,"
                "remaining INTEGER NOT NULL CHECK(remaining >= 0))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS members ("
                "telegram_id INTEGER PRIMARY KEY,"
                "invited_at INTEGER NOT NULL)"
            )

    @contextmanager
    def _connect(self):
        # sqlite3.Connection.__exit__ commits but does NOT close the file.
        # Explicit close is mandatory, especially on Windows service hosts.
        conn = sqlite3.connect(self.path, timeout=8, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=8000")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _hash(secret: str) -> str:
        return hashlib.sha256(secret.encode("ascii")).hexdigest()

    def create(self, *, hours: int = 24, uses: int = 1) -> str:
        if not 1 <= hours <= 720 or not 1 <= uses <= 100:
            raise ValueError("Срок приглашения: 1–720 часов, число активаций: 1–100.")
        secret = secrets.token_urlsafe(24)
        now = int(time.time())
        with self._connect() as db:
            db.execute(
                "INSERT INTO invitations (secret_hash, expires, remaining)"
                " VALUES (?, ?, ?)",
                (self._hash(secret), now + hours * 3600, uses),
            )
        return secret

    def is_member(self, telegram_id: int) -> bool:
        if type(telegram_id) is not int or telegram_id <= 0:
            return False
        with self._connect() as db:
            return db.execute(
                "SELECT 1 FROM members WHERE telegram_id = ?", (telegram_id,)
            ).fetchone() is not None

    def accept(self, telegram_id: int, secret: str) -> bool:
        if (type(telegram_id) is not int or telegram_id <= 0
                or not isinstance(secret, str) or not _INVITE.fullmatch(secret)):
            return False
        now = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM members WHERE telegram_id = ?", (telegram_id,)
            ).fetchone():
                return True
            row = db.execute(
                "SELECT expires, remaining FROM invitations WHERE secret_hash = ?",
                (self._hash(secret),),
            ).fetchone()
            if row is None or row[0] <= now or row[1] <= 0:
                return False
            db.execute(
                "UPDATE invitations SET remaining = remaining - 1 "
                "WHERE secret_hash = ?", (self._hash(secret),)
            )
            db.execute(
                "INSERT INTO members (telegram_id, invited_at) VALUES (?, ?)",
                (telegram_id, now),
            )
            return True

    def grant(self, telegram_id: int) -> None:
        """Explicit manual employee enrollment by numeric Telegram user ID."""
        if type(telegram_id) is not int or telegram_id <= 0:
            raise ValueError("Нужен положительный Telegram ID.")
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO members (telegram_id, invited_at) VALUES (?, ?)",
                (telegram_id, int(time.time())),
            )

    @staticmethod
    def list_members(filename: str | os.PathLike[str]) -> tuple[int, ...]:
        """Read-only view for desktop users without write permission."""
        from urllib.parse import quote
        path = Path(filename)
        if not path.is_file():
            return ()
        uri = "file:" + quote(path.resolve().as_posix(), safe="/:") + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2) as db:
            rows = db.execute(
                "SELECT telegram_id FROM members ORDER BY invited_at, telegram_id"
            ).fetchall()
        return tuple(int(row[0]) for row in rows)

    def members(self) -> tuple[int, ...]:
        return self.list_members(self.path)

    def revoke(self, telegram_id: int) -> None:
        if type(telegram_id) is not int or telegram_id <= 0:
            raise ValueError("Нужен положительный Telegram ID.")
        with self._connect() as db:
            db.execute("DELETE FROM members WHERE telegram_id = ?", (telegram_id,))


def state_database_path() -> Path:
    override = os.environ.get("LINKVIDEO_TELEGRAM_STATE_DB", "").strip()
    if override:
        return Path(override)
    # Dedicated bot state must not be stored in a Windows user's roaming
    # profile, since Windows sessions on the host change throughout the day.
    base = (os.environ.get("PROGRAMDATA") if os.name == "nt"
            else os.environ.get("XDG_STATE_HOME"))
    if base:
        return Path(base) / "LinkVideo" / "Bot" / "invites.sqlite3"
    return Path.home() / ".local" / "state" / "linkvideo-bot" / "invites.sqlite3"


def invitation_url(username: str, secret: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        raise ValueError("Укажите публичный username Telegram-бота без @.")
    if not _INVITE.fullmatch(secret):
        raise ValueError("Неверный код приглашения.")
    return f"https://t.me/{username}?start={secret}"
