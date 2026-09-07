#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass

from linkvideo_vpnsync.auth import hash_password
from linkvideo_vpnsync.config import get_settings
from linkvideo_vpnsync.db import VPNDatabase


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create or update a LinkVideo.VPNSync desktop operator. "
            "This is an API account, separate from the Ubuntu/SSH account; "
            "you may intentionally choose the same password if desired."
        )
    )
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", default="operator", choices=("operator", "admin", "viewer"))
    parser.add_argument("--disable", action="store_true")
    args = parser.parse_args()

    username = str(args.username or "").strip()
    if not username:
        raise SystemExit("Username is empty")

    settings = get_settings()
    db = VPNDatabase(settings.database_url, settings.encryption_key)
    db.open()
    try:
        if args.disable:
            with db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE vpnsync_users SET enabled = FALSE, updated_at = now() WHERE lower(username)=lower(%s)",
                    (username,),
                )
                if cur.rowcount < 1:
                    raise SystemExit(f"Operator {username!r} not found")
                conn.commit()
            print(f"[VPNSync] Operator disabled: {username}")
            return

        print(
            "[VPNSync] This password is for the VPNSync API, not SSH/Termius. "
            "It is stored only as a PBKDF2 hash in PostgreSQL."
        )
        password = getpass.getpass("New VPNSync password: ")
        confirm = getpass.getpass("Repeat VPNSync password: ")
        if password != confirm:
            raise SystemExit("Passwords do not match")
        salt, digest, iterations = hash_password(password)
        with db.connection() as conn, conn.cursor() as cur:
            # Use an explicit UPDATE/INSERT sequence instead of relying on
            # expression-index ON CONFLICT inference. This behaves identically
            # on every supported PostgreSQL version and keeps usernames
            # case-insensitively unique.
            cur.execute(
                """
                UPDATE vpnsync_users
                   SET username = %s,
                       role = %s,
                       password_salt = %s,
                       password_hash = %s,
                       password_iterations = %s,
                       enabled = TRUE,
                       updated_at = now()
                 WHERE lower(username) = lower(%s)
                """,
                (username, args.role, salt, digest, iterations, username),
            )
            if cur.rowcount < 1:
                cur.execute(
                    """
                    INSERT INTO vpnsync_users
                        (username, role, password_salt, password_hash, password_iterations, enabled)
                    VALUES (%s, %s, %s, %s, %s, TRUE)
                    """,
                    (username, args.role, salt, digest, iterations),
                )
            conn.commit()
        print(f"[VPNSync] Operator ready: {username} ({args.role})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
