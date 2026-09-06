from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any


PASSWORD_ITERATIONS = 390_000
SESSION_TTL_SECONDS = 12 * 60 * 60


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    raw = text.encode("ascii")
    raw += b"=" * ((4 - len(raw) % 4) % 4)
    return base64.urlsafe_b64decode(raw)


def hash_password(password: str, *, salt: bytes | None = None, iterations: int = PASSWORD_ITERATIONS) -> tuple[bytes, bytes, int]:
    value = str(password or "")
    if len(value) < 8:
        raise ValueError("Password must contain at least 8 characters")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", value.encode("utf-8"), salt, int(iterations), dklen=32)
    return salt, digest, int(iterations)


def verify_password(password: str, salt: bytes, expected_hash: bytes, iterations: int) -> bool:
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        str(password or "").encode("utf-8"),
        bytes(salt),
        int(iterations),
        dklen=len(expected_hash),
    )
    return hmac.compare_digest(actual, bytes(expected_hash))


@dataclass(frozen=True)
class AuthContext:
    username: str
    role: str
    legacy: bool = False


class AuthService:
    def __init__(self, db, signing_secret: str, *, session_ttl: int = SESSION_TTL_SECONDS) -> None:
        self.db = db
        self.signing_secret = str(signing_secret or "").encode("utf-8")
        self.session_ttl = max(300, int(session_ttl))
        if len(self.signing_secret) < 24:
            raise ValueError("Auth signing secret is too short")

    def _current_user(self, username: str) -> AuthContext:
        with self.db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT username, role, enabled
                  FROM vpnsync_users
                 WHERE lower(username)=lower(%s)
                 LIMIT 1
                """,
                (str(username or ""),),
            )
            row = cur.fetchone()
        if not row or not bool(row.get("enabled")):
            raise ValueError("Session user is disabled or missing")
        return AuthContext(
            username=str(row.get("username") or ""),
            role=str(row.get("role") or "operator"),
        )

    def login(self, username: str, password: str) -> tuple[str, AuthContext, int]:
        wanted = str(username or "").strip()
        if not wanted or not password:
            raise ValueError("Invalid username or password")
        with self.db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, role, password_salt, password_hash,
                       password_iterations, enabled
                  FROM vpnsync_users
                 WHERE lower(username) = lower(%s)
                 LIMIT 1
                """,
                (wanted,),
            )
            row = cur.fetchone()
            if not row or not bool(row.get("enabled")):
                raise ValueError("Invalid username or password")
            if not verify_password(
                password,
                bytes(row["password_salt"]),
                bytes(row["password_hash"]),
                int(row["password_iterations"]),
            ):
                raise ValueError("Invalid username or password")
            username_db = str(row["username"])
            role = str(row.get("role") or "operator")
            cur.execute(
                "UPDATE vpnsync_users SET last_login_at = now(), updated_at = now() WHERE id = %s",
                (int(row["id"]),),
            )
            conn.commit()
        ctx = AuthContext(username=username_db, role=role)
        return self.issue_token(ctx), ctx, self.session_ttl

    def issue_token(self, context: AuthContext) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "sub": context.username,
            "role": context.role,
            "iat": now,
            "exp": now + self.session_ttl,
            "nonce": _b64e(secrets.token_bytes(12)),
        }
        body = _b64e(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        signature = _b64e(hmac.new(self.signing_secret, body.encode("ascii"), hashlib.sha256).digest())
        return f"{body}.{signature}"

    def verify_token(self, token: str) -> AuthContext:
        value = str(token or "").strip()
        try:
            body, signature = value.split(".", 1)
        except ValueError as exc:
            raise ValueError("Invalid session token") from exc
        expected = _b64e(hmac.new(self.signing_secret, body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("Invalid session token")
        try:
            payload = json.loads(_b64d(body).decode("utf-8"))
        except Exception as exc:
            raise ValueError("Invalid session token") from exc
        if int(payload.get("exp") or 0) <= int(time.time()):
            raise ValueError("Session expired")
        username = str(payload.get("sub") or "").strip()
        if not username:
            raise ValueError("Invalid session token")

        # Do not trust an old role embedded in a still-valid token. This DB read
        # makes operator disable/role changes effective immediately instead of
        # waiting for the 12-hour token TTL to expire.
        return self._current_user(username)

    def audit(
        self,
        *,
        actor: str,
        role: str = "",
        source: str,
        action: str,
        server_id: int | None = None,
        login: str = "",
        success: bool = True,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vpnsync_audit_log
                    (actor, role, source, action, server_id, login, success, details)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    str(actor or ""),
                    str(role or ""),
                    str(source or "server"),
                    str(action or "unknown"),
                    int(server_id) if server_id is not None else None,
                    str(login or ""),
                    bool(success),
                    json.dumps(details or {}, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            conn.commit()
