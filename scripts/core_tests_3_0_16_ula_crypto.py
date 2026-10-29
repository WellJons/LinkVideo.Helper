from __future__ import annotations

"""Offline Oracle Thin dependency regression, no server or credentials."""

from pathlib import Path

import oracledb
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

root = Path(__file__).resolve().parents[1]
requirements = (root / "requirements.txt").read_text(encoding="utf-8")
assert "cryptography>=" in requirements, "DPY-3016: cryptography is a mandatory runtime dependency"
source = (root / "linkvideo_vpn_helper" / "app.py").read_text(encoding="utf-8")
assert "--self-test-ula-driver" in source
assert "from cryptography.hazmat.primitives.ciphers.aead import AESGCM" in source

key = AESGCM.generate_key(bit_length=128)
nonce = bytes(range(12))
cipher = AESGCM(key)
plaintext = b"offline test only"
assert cipher.decrypt(nonce, cipher.encrypt(nonce, plaintext, None), None) == plaintext
derived = PBKDF2HMAC(
    algorithm=hashes.SHA256(), length=16, salt=b"synthetic-only", iterations=1000,
).derive(b"test")
assert len(derived) == 16
assert hasattr(oracledb, "DB_TYPE_CURSOR")
print("CORE TESTS 3.0.16 ULA ORACLE THIN CRYPTOGRAPHY OK")
