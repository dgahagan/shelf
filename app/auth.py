import logging
import os
import secrets
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


def get_secret_key() -> str:
    """Get the JWT secret key. Priority: env var > DB settings > generate & store."""
    global _cached_secret_key
    if _cached_secret_key:
        return _cached_secret_key

    if SECRET_KEY:
        _cached_secret_key = SECRET_KEY
        return _cached_secret_key

    with get_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key = 'secret_key'").fetchone()
        if row and row["value"]:
            _cached_secret_key = row["value"]
            return _cached_secret_key

        key = secrets.token_hex(32)
        db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('secret_key', ?)",
            (key,),
        )
        # Read back what was actually stored — another process may have beaten us
        row = db.execute("SELECT value FROM settings WHERE key = 'secret_key'").fetchone()
        _cached_secret_key = row["value"]
        return _cached_secret_key


def _rotate_secret_key() -> None:
    """Generate a new secret key, invalidating all existing JWTs."""
    global _cached_secret_key
    key = secrets.token_hex(32)
    with get_db() as db:
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('secret_key', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = ?",
            (key, key),
        )
    _cached_secret_key = key


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
