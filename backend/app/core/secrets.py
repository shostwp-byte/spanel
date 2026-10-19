"""
Encryption helpers for at-rest secrets stored in the spanel SQLite DB.

Used for: per-website MariaDB user passwords (DatabaseAccount.db_password),
SFTP backup target credentials, and TOTP secrets.

Key derivation: Fernet uses a 32-byte key. We derive it via SHA-256 over
settings.secret_key. Rotating SECRET_KEY in production therefore invalidates
any previously stored ciphertexts; do this only as a deliberate rekey
operation. SECRET_KEY is itself loaded from /opt/spanel/backend/.env which is
not world-readable.
"""

import base64
import hashlib
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


logger = logging.getLogger("spanel.secrets")

_ENCRYPTED_PREFIX = "fernet:"


def _derive_key() -> bytes:
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


_fernet = Fernet(_derive_key())


def encrypt(plaintext: str) -> str:
    if plaintext is None:
        return plaintext
    token = _fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return _ENCRYPTED_PREFIX + token


def decrypt(stored: Optional[str]) -> str:
    """Decrypt a stored value.

    Behaviour:
    - Empty/None → returns "".
    - Values prefixed with ``fernet:`` → decrypted normally; bad ciphertext
      raises ``RuntimeError`` (typically caused by SECRET_KEY rotation).
    - Values without the prefix are legacy plaintext from before encryption
      was introduced. We refuse to read them by default so any forgotten row
      surfaces immediately. Set ``STRICT_DECRYPT=false`` in the environment
      to temporarily allow passthrough during a migration window.
    """
    if not stored:
        return stored or ""
    if not stored.startswith(_ENCRYPTED_PREFIX):
        if not getattr(settings, "strict_decrypt", True):
            logger.warning(
                "secrets.decrypt(): legacy plaintext value detected (length=%d). "
                "Re-save the secret to encrypt it at rest.",
                len(stored),
            )
            return stored
        raise RuntimeError(
            "secrets.decrypt(): refusing to read legacy plaintext value. "
            "Re-save the affected record to encrypt it, or temporarily set "
            "STRICT_DECRYPT=false to migrate."
        )
    payload = stored[len(_ENCRYPTED_PREFIX):]
    try:
        return _fernet.decrypt(payload.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Cannot decrypt stored secret; SECRET_KEY may have been rotated") from exc


def is_encrypted(stored: Optional[str]) -> bool:
    """Return True if the stored value is a Fernet ciphertext written by us."""
    return bool(stored) and stored.startswith(_ENCRYPTED_PREFIX)

