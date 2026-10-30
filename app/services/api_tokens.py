"""API bearer tokens: mint, list, revoke, and resolve to a viewer-capped user.

The raw token exists only in the return value of ``create``. The table holds
its sha256 hash and a short display prefix. No module-level state (G13).
"""

import hashlib
import secrets
import sqlite3

from app.database import get_db

TOKEN_PREFIX = "shelf_"
PREFIX_CHARS = 12
MAX_PER_USER = 25
TOUCH_SECONDS = 60
MAX_NAME = 64
MAX_TOKEN_LEN = 128


def generate() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def parse_bearer(header: str | None) -> str | None:
    """Return the credential of an ``Authorization: Bearer`` header, else None.

    Scheme match is case-insensitive and the credential is stripped, so the
    auth middleware and the rate limiter canonicalise identically. Never raises.
    """
    if not header:
        return None
    scheme, _, rest = header.partition(" ")
    if scheme.casefold() != "bearer":
        return None
    credential = rest.strip()
    return credential or None


def create(db, user_id: int, name: str) -> dict | None:
    """Mint a token for ``user_id``. Returns None when the user is at the cap.

    One INSERT ... SELECT ... WHERE (count) < MAX statement, so the cap has no
    read-then-write gap (G18). The raw token is in the returned dict only.
    """
    raw = generate()
    cur = db.execute(
        "INSERT INTO api_tokens (user_id, name, token_hash, prefix) "
        "SELECT ?, ?, ?, ? "
        "WHERE (SELECT COUNT(*) FROM api_tokens WHERE user_id = ?) < ?",
        (user_id, name, hash_token(raw), raw[:PREFIX_CHARS], user_id, MAX_PER_USER),
    )
    if cur.rowcount == 0:
        return None
    row = db.execute(
        "SELECT id, name, prefix, created_at FROM api_tokens WHERE id = ?",
        (cur.lastrowid,),
    ).fetchone()
    return {
        "id": row["id"],
        "name": row["name"],
        "prefix": row["prefix"],
        "created_at": row["created_at"],
        "token": raw,
    }


def list_for_user(db, user_id: int) -> list[dict]:
    rows = db.execute(
        "SELECT id, name, prefix, last_used_at, created_at FROM api_tokens "
        "WHERE user_id = ? ORDER BY id",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_all(db) -> list[dict]:
    rows = db.execute(
        "SELECT t.id, t.user_id, t.name, t.prefix, t.last_used_at, t.created_at, "
        "u.username, u.display_name "
        "FROM api_tokens t JOIN users u ON u.id = t.user_id "
        "ORDER BY u.username, t.id"
    ).fetchall()
    return [dict(r) for r in rows]


def revoke(db, token_id: int, *, user_id: int | None = None) -> bool:
    if user_id is None:
        cur = db.execute("DELETE FROM api_tokens WHERE id = ?", (token_id,))
    else:
        cur = db.execute(
            "DELETE FROM api_tokens WHERE id = ? AND user_id = ?", (token_id, user_id)
        )
    return cur.rowcount > 0


def resolve(raw: str) -> dict | None:
    """Resolve a raw token to a viewer-capped user dict, or None.

    Failure contract, per exit (G66):
    - empty, over-length, or not ``shelf_``-prefixed input: None, before hashing
      and without touching the database;
    - unknown (or revoked) token: None;
    - database error: raises (the caller answers 500, not 401).

    The returned role is always "viewer": the cap lives here, in the one place
    a token becomes a user. ``last_used_at`` is touched at most once per
    TOUCH_SECONDS, and the UPDATE is only issued when due, because even a
    zero-row UPDATE takes SQLite's writer lock.
    """
    if not raw or len(raw) > MAX_TOKEN_LEN or not raw.startswith(TOKEN_PREFIX):
        return None
    window = f"-{TOUCH_SECONDS} seconds"
    with get_db() as db:
        row = db.execute(
            "SELECT t.id AS token_id, t.user_id, u.username, u.role, "
            "u.display_name, u.token_version, "
            "(t.last_used_at IS NULL OR t.last_used_at < datetime('now', ?)) AS touch_due "
            "FROM api_tokens t JOIN users u ON u.id = t.user_id "
            "WHERE t.token_hash = ?",
            (window, hash_token(raw)),
        ).fetchone()
        if row is None:
            return None
        if row["touch_due"]:
            # The touch is advisory: the read already succeeded, so a writer
            # holding the lock past the busy timeout (an import, a restore)
            # skips this touch rather than failing the request (G153).
            try:
                db.execute(
                    "UPDATE api_tokens SET last_used_at = datetime('now') "
                    "WHERE id = ? AND (last_used_at IS NULL OR last_used_at < datetime('now', ?))",
                    (row["token_id"], window),
                )
            except sqlite3.OperationalError:
                db.rollback()
    return {
        "id": row["user_id"],
        "username": row["username"],
        "role": "viewer",
        "display_name": row["display_name"] or row["username"],
        "token_version": row["token_version"],
        "api_token_id": row["token_id"],
    }
