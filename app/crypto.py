"""Symmetric encryption helpers for sensitive settings stored in the DB.

Encryption key is derived from the JWT secret key via SHA-256, so:
- If SECRET_KEY env var is set, the DB is useless without it.
- If SECRET_KEY is not set and the key lives in the DB, an attacker who
  reads the DB can derive the encryption key too.  Setting SECRET_KEY via
  env var (docker-compose / .env) is strongly recommended for deployments
  where API key exposure matters.

Values are stored as Fernet tokens (base64url, self-describing, with HMAC
authentication).  Plaintext values (pre-migration) are detected by the
absence of the Fernet token prefix and transparently re-encrypted on read.
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# Settings keys that contain third-party credentials — encrypted at rest
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "abs_token",
        "hardcover_token",
        "isbndb_api_key",
        "tmdb_api_key",
        "igdb_client_id",
        "igdb_client_secret",
    }
)


def _fernet(secret_key: str) -> Fernet:
    """Return a Fernet instance keyed from the app secret."""
    raw = hashlib.sha256(secret_key.encode()).digest()  # 32 bytes
    key = base64.urlsafe_b64encode(raw)
    return Fernet(key)


def encrypt_value(plaintext: str, secret_key: str) -> str:
    """Encrypt *plaintext* and return a Fernet token string."""
    if not plaintext:
        return plaintext
    return _fernet(secret_key).encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str, secret_key: str) -> str:
    """Decrypt a Fernet token.  Returns the original plaintext on success.

    If *ciphertext* is not a valid Fernet token (e.g. a legacy plaintext
    value stored before encryption was introduced), returns it unchanged so
    callers still get a usable value.
    """
    if not ciphertext:
        return ciphertext
    try:
        return _fernet(secret_key).decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception):
        # Legacy plaintext value — callers should re-encrypt on next write
        logger.debug("decrypt_value: not a Fernet token, treating as plaintext")
        return ciphertext
