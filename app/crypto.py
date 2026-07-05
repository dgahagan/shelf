"""Symmetric encryption helpers for sensitive settings stored in the DB.

The encryption key is independent of the JWT secret, and is never stored in
the database — so a stolen DB backup contains ciphertext only.  Resolution
order:

1. ``SHELF_ENCRYPTION_KEY`` env var, if set (recommended for deployments —
   the DB *and* the data dir are then useless without it).
2. A key file at ``DATA_DIR/encryption.key``, auto-generated on first use
   with 0600 permissions.  The backup download is ``VACUUM INTO`` (DB only),
   so the key never rides along in a backup.

Historically the key was derived from the JWT secret (which itself could
live in the DB); ``migrate_sensitive_settings()`` re-encrypts such legacy
values on startup.

Values are stored as Fernet tokens (base64url, self-describing, with HMAC
authentication).  Plaintext values (pre-migration) are detected by the
absence of the Fernet token prefix and transparently re-encrypted on read.
"""

import base64
import hashlib
import logging
import os
import secrets

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# All Fernet tokens are base64url(0x80 || timestamp || ...), so they share
# this prefix; used to tell ciphertext from legacy plaintext values.
_FERNET_PREFIX = "gAAAAA"

# Settings keys that contain third-party credentials — encrypted at rest
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "abs_token",
        "anthropic_api_key",
        "hardcover_token",
        "isbndb_api_key",
        "tmdb_api_key",
        "igdb_client_id",
        "igdb_client_secret",
        # An ntfy topic URL is effectively a credential — anyone holding it
        # can post to (and often read) the topic
        "notify_url",
    }
)


_cached_encryption_key: str | None = None


def _read_or_create_keyfile() -> str:
    """Read the key file, creating it atomically (0600) if missing."""
    from app import config  # attribute lookup at call time — tests patch DATA_DIR
    key_file = config.DATA_DIR / "encryption.key"
    try:
        fd = os.open(str(key_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        key = key_file.read_text().strip()
        if key:
            return key
        # Truncated/empty file from an interrupted first write — regenerate
        key = secrets.token_hex(32)
        key_file.write_text(key)
        key_file.chmod(0o600)
        return key
    try:
        key = secrets.token_hex(32)
        os.write(fd, key.encode())
    finally:
        os.close(fd)
    return key


def get_encryption_key() -> str:
    """Encryption key for sensitive settings — env var, else local key file."""
    global _cached_encryption_key
    if _cached_encryption_key:
        return _cached_encryption_key
    key = os.environ.get("SHELF_ENCRYPTION_KEY", "").strip() or _read_or_create_keyfile()
    _cached_encryption_key = key
    return key


def is_fernet_token(value: str) -> bool:
    """True if *value* looks like Fernet ciphertext rather than plaintext."""
    return value.startswith(_FERNET_PREFIX)


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


def migrate_sensitive_settings() -> int:
    """Move sensitive settings onto the dedicated encryption key.

    Re-encrypts values still encrypted under the legacy JWT-derived key, and
    encrypts any legacy plaintext values.  Idempotent — runs on every
    startup, which also covers backups restored in place (restore requires a
    restart).  Values that decrypt with neither key (e.g. a backup from a
    different install) are left untouched and logged.

    Returns the number of rows rewritten.
    """
    from app.auth import get_secret_key
    from app.database import get_db

    new_key = get_encryption_key()
    legacy_key = get_secret_key()
    migrated = 0
    with get_db() as db:
        rows = db.execute("SELECT key, value FROM settings").fetchall()
        for row in rows:
            key, value = row["key"], row["value"]
            if key not in SENSITIVE_KEYS or not value:
                continue
            if is_fernet_token(value):
                try:
                    _fernet(new_key).decrypt(value.encode())
                    continue  # already on the new key
                except InvalidToken:
                    pass
                try:
                    plaintext = _fernet(legacy_key).decrypt(value.encode()).decode()
                except InvalidToken:
                    logger.warning(
                        "settings[%s]: undecryptable with current or legacy key; "
                        "left as-is — re-enter the credential in Settings", key
                    )
                    continue
            else:
                plaintext = value  # pre-encryption plaintext value
            db.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                (encrypt_value(plaintext, new_key), key),
            )
            migrated += 1
    if migrated:
        logger.info("Re-encrypted %d sensitive setting(s) with the dedicated encryption key", migrated)
    return migrated
