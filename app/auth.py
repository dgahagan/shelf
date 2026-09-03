import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt
from fastapi import Request, Response
from fastapi.responses import RedirectResponse, HTMLResponse

from app.config import SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRY_SECONDS
from app.database import get_db

logger = logging.getLogger(__name__)

ROLE_LEVELS = {"viewer": 1, "editor": 2, "admin": 3}

_cached_secret_key: str | None = None


SIGNING_KEY_FILE = "signing.key"


def _prune_legacy_secret_key_row() -> None:
    """Delete a pre-0.30 ``secret_key`` settings row, if one is there.

    Runs on every cold resolve rather than once: a restored old backup brings
    the row back, and restore already invalidates every session and asks for a
    restart, so this catches it with no new hook.  ``SELECT`` first so the
    common case does no write at all and the write happens exactly once, on
    the first start after upgrading.

    Failure is warned about and swallowed.  This is hardening with no bearing
    on the key already resolved, and an install that sets ``SECRET_KEY`` must
    not gain a startup failure mode it did not have before.
    """
    removed = False
    try:
        with get_db() as db:
            if db.execute("SELECT 1 FROM settings WHERE key = 'secret_key'").fetchone():
                db.execute("DELETE FROM settings WHERE key = 'secret_key'")
                removed = True
    except sqlite3.Error as e:
        # Logged outside the block above (G3) — SQLiteHandler opens a second
        # connection and would block on our write until SQLite's busy timeout.
        logger.warning("Could not remove the legacy signing-key settings row: %s", e)
        return
    if removed:
        logger.info(
            "Legacy signing-key settings row removed; the key file is authoritative"
        )


def get_secret_key() -> str:
    """JWT signing key — env var, else DATA_DIR/signing.key, else generated.

    Process-cached, so token minting and verification gain no filesystem read
    per request.  Resolution order on the cold path:

    1. ``SECRET_KEY`` env var → use it.  The key file is never read or
       written.  A leftover ``secret_key`` row is pruned anyway, so the "no
       key material in the database" invariant holds on every install.
    2. ``DATA_DIR/signing.key`` present and non-empty → use it, and prune a
       ``secret_key`` row if one is also present (a restored pre-0.30 backup).
    3. A ``secret_key`` row and no usable file → **relocate the value, never
       change it**: write it to the file, read it back, and delete the row
       only once they match.  On an OSError, or a mismatch, keep the row and
       keep using its value exactly as before, warn, and remove a mismatching
       file.  A crash between the write and the delete leaves both, and the
       next start takes path 2 and deletes the row — idempotent.
    4. Neither → generate into the file.  If *that* write fails, degrade to
       the pre-0.30 behaviour (insert the row) and warn.

    Preserving the value is what keeps sessions alive across the upgrade, and
    it is also what keeps ``crypto.migrate_sensitive_settings`` correct: it
    opens pre-July legacy ciphertext with ``get_secret_key()``, so changing
    the value would make those settings permanently unreadable.  ``lifespan``
    calls this *before* that migration — preserve the order, do not assume it.

    Nothing here raises out of ``lifespan`` on a file error.  A raise there is
    a boot loop whose only symptom is that nobody can log in, and the
    relocation is an improvement, not a precondition: a failed write degrades
    to exactly the behaviour the operator had before upgrading.
    """
    global _cached_secret_key
    if _cached_secret_key:
        return _cached_secret_key

    from app import crypto

    if SECRET_KEY:
        _cached_secret_key = SECRET_KEY
        _prune_legacy_secret_key_row()
        return _cached_secret_key

    file_key = crypto._read_keyfile(SIGNING_KEY_FILE)
    if file_key:
        _cached_secret_key = file_key
        _prune_legacy_secret_key_row()
        return _cached_secret_key

    # No usable key file. One connection covers the row read, the relocation's
    # delete and the fresh-install fallback insert; the outcome is gathered
    # here and every log line emitted after the block closes (G3).
    outcome = ""
    detail = ""
    with get_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key = 'secret_key'").fetchone()
        row_key = row["value"] if row and row["value"] else None

        if row_key:
            key = row_key  # the fallback in every arm below: never re-key
            try:
                crypto._write_keyfile(SIGNING_KEY_FILE, row_key)
            except OSError as e:
                outcome = "kept"
                detail = f"could not write {SIGNING_KEY_FILE}: {e.strerror or e}"
            else:
                if crypto._read_keyfile(SIGNING_KEY_FILE) == row_key:
                    db.execute("DELETE FROM settings WHERE key = 'secret_key'")
                    outcome = "relocated"
                else:
                    crypto._unlink_keyfile(SIGNING_KEY_FILE)
                    outcome = "kept"
                    detail = f"{SIGNING_KEY_FILE} did not read back identical"
        else:
            key = secrets.token_hex(32)
            try:
                crypto._write_keyfile(SIGNING_KEY_FILE, key)
            except OSError as e:
                db.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES ('secret_key', ?)",
                    (key,),
                )
                # Read back what was stored — another process may have beaten us
                stored = db.execute(
                    "SELECT value FROM settings WHERE key = 'secret_key'"
                ).fetchone()
                key = stored["value"]
                outcome = "kept"
                detail = f"could not write {SIGNING_KEY_FILE}: {e.strerror or e}"

    _cached_secret_key = key
    if outcome == "relocated":
        logger.info(
            "Signing key moved to %s; settings row removed", SIGNING_KEY_FILE
        )
    elif outcome == "kept":
        logger.warning(
            "Signing-key hardening skipped — the key stays in the database (%s). "
            "Make the data directory writable and restart to complete the move.",
            detail,
        )
    return _cached_secret_key


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(user_id: int, username: str, role: str, display_name: str | None = None, token_version: int = 1) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "display_name": display_name or username,
        "tv": token_version,
        "iat": now,
        "exp": now + timedelta(seconds=JWT_EXPIRY_SECONDS),
    }
    return jwt.encode(payload, get_secret_key(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, get_secret_key(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        logger.debug("Expired JWT token presented")
        return None
    except jwt.InvalidTokenError:
        logger.warning("Invalid JWT token presented")
        return None


def set_auth_cookie(response: Response, token: str, csrf_token: str | None = None) -> None:
    secure = not os.environ.get("SHELF_DEV_INSECURE_COOKIES")
    if os.environ.get("SHELF_DEV_INSECURE_COOKIES"):
        logger.warning(
            "SHELF_DEV_INSECURE_COOKIES is set — auth cookie is NOT secure. "
            "Never use this in production."
        )
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=JWT_EXPIRY_SECONDS,
        path="/",
    )
    # Set a paired CSRF token cookie (readable by JS for double-submit)
    if csrf_token is None:
        csrf_token = secrets.token_hex(32)
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,
        secure=secure,
        samesite="strict",
        max_age=JWT_EXPIRY_SECONDS,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(key="access_token", path="/")
    response.delete_cookie(key="csrf_token", path="/")


def get_current_user(request: Request) -> dict | None:
    """Read user from JWT cookie. Returns dict with id, username, role, display_name or None."""
    token = request.cookies.get("access_token")
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None

    # Check token version against DB to detect invalidated tokens
    token_tv = payload.get("tv", 1)
    user_id = int(payload["sub"])
    with get_db() as db:
        row = db.execute("SELECT token_version FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        if row["token_version"] != token_tv:
            return None

    return {
        "id": user_id,
        "username": payload["username"],
        "role": payload["role"],
        "display_name": payload.get("display_name", payload["username"]),
    }


def should_refresh_token(request: Request) -> str | None:
    """If token is past half-life, return a fresh token. Otherwise None."""
    token = request.cookies.get("access_token")
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    exp = payload.get("exp", 0)
    iat = payload.get("iat", 0)
    now = time.time()
    half_life = (exp - iat) / 2
    if now > iat + half_life:
        return create_token(
            int(payload["sub"]), payload["username"], payload["role"],
            payload.get("display_name"), payload.get("tv", 1),
        )
    return None


def get_user_count() -> int:
    with get_db() as db:
        row = db.execute("SELECT COUNT(*) as cnt FROM users").fetchone()
        return row["cnt"] if row else 0


def require_role(minimum_role: str):
    """FastAPI dependency factory. Returns a dependency that checks the user's role."""
    min_level = ROLE_LEVELS[minimum_role]

    async def _dependency(request: Request):
        user = getattr(request.state, "user", None)
        if not user:
            _raise_auth_required(request)
        if ROLE_LEVELS.get(user["role"], 0) < min_level:
            _raise_insufficient_role(request)
        return user

    return _dependency


def _raise_auth_required(request: Request):
    """Raise appropriate response for unauthenticated requests."""
    if request.headers.get("HX-Request"):
        resp = HTMLResponse(status_code=401)
        resp.headers["HX-Redirect"] = "/login"
        raise _ResponseException(resp)
    if request.url.path.startswith("/api/"):
        raise _ResponseException(HTMLResponse("Unauthorized", status_code=401))
    raise _ResponseException(RedirectResponse(url="/login", status_code=303))


def _raise_insufficient_role(request: Request):
    """Raise appropriate response for insufficient permissions."""
    if request.headers.get("HX-Request"):
        resp = HTMLResponse(status_code=403)
        resp.headers["HX-Redirect"] = "/login"
        raise _ResponseException(resp)
    if request.url.path.startswith("/api/"):
        raise _ResponseException(HTMLResponse("Forbidden", status_code=403))
    raise _ResponseException(RedirectResponse(url="/browse", status_code=303))


class _ResponseException(Exception):
    """Wraps a Response so FastAPI's dependency system can return it."""
    def __init__(self, response: Response):
        self.response = response
