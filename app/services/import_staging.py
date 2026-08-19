"""Staged-upload lifecycle for the import preview flow (plan/apply).

Holds an uploaded portable-archive zip plus its computed plan between a
``POST /api/import/archive/plan`` request and a later ``/apply`` request,
so the apply step can re-read the exact bytes that were previewed and
detect drift against the plan the user actually reviewed.

Layout: ``DATA_DIR / "import_staging" / "<upload_id>.zip"`` and
``<upload_id>.plan.json``, where ``upload_id`` is a
``secrets.token_urlsafe(32)`` value minted by :func:`stage`.

**Single slot.** Only one upload may be staged at a time — :func:`stage`
unconditionally deletes every file already in the staging dir before
writing the new pair. There is no multi-tenant/multi-session staging area;
concurrent admins previewing archives will race and the later stage wins.

**TTL.** Staged files expire 30 minutes after they were written (mtime
based). There is no background sweeper — callers (the plan/apply routers)
are expected to call :func:`sweep_expired` opportunistically on every
request. Independent of that, every read in this module
(:func:`staged_path`, :func:`load_plan`) also checks the TTL itself and
treats an expired file as absent, so a caller that forgets to sweep still
never gets served stale data — sweeping only affects when the bytes are
actually reclaimed from disk, not whether stale data can leak out.

**Untrusted ``upload_id``.** The client supplies ``upload_id`` on
``/apply``, so every function that takes one validates it against
``^[A-Za-z0-9_-]+$`` before deriving any path — the id is never joined onto
a path unchecked, and a malformed id can never resolve outside the staging
dir (the regex forbids ``/`` and ``.`` entirely, so traversal segments like
``..`` can't appear even incidentally). **Convention: every function that
takes an ``upload_id`` treats a malformed one as a clean "not there" result
rather than raising** — :func:`staged_path` and :func:`load_plan` return
``None``; :func:`save_plan` and :func:`consume` silently do nothing. This
is deliberate and uniform across the module so callers never need to catch
an exception just to handle a bad or forged id the same way they'd handle
an expired one.
"""
import json
import re
import secrets
import time
from pathlib import Path

from app import config

TTL_SECONDS = 30 * 60

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _staging_dir() -> Path:
    """DATA_DIR / "import_staging", resolved at call time (not import
    time) so tests that monkeypatch app.config.DATA_DIR see it — a frozen
    module-level constant would bind to whatever DATA_DIR was at first
    import and stay stale for the rest of the process (see
    archive_tmp_path()'s docstring in app/services/archive.py for the same
    trap and fix)."""
    return config.DATA_DIR / "import_staging"


def _valid_token(upload_id: str) -> bool:
    """True if upload_id is a non-empty string matching the token charset
    used by secrets.token_urlsafe — no '/', '.', or other path-meaningful
    characters, so it can never traverse outside the staging dir."""
    return isinstance(upload_id, str) and bool(_TOKEN_RE.fullmatch(upload_id))


def _zip_path(upload_id: str) -> Path:
    return _staging_dir() / f"{upload_id}.zip"


def _plan_path(upload_id: str) -> Path:
    return _staging_dir() / f"{upload_id}.plan.json"


def _is_expired(path: Path) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return True
    return age > TTL_SECONDS


def stage(content: bytes) -> str:
    """Stage a new upload's raw zip bytes and return its upload_id.

    Evicts every file currently in the staging dir first (single slot —
    at most one upload is ever staged), unconditionally, regardless of
    age. Does not itself call sweep_expired(); callers that want opportunistic
    TTL reclamation of *other* stray files should call it separately (in
    practice a no-op here since eviction already clears the dir)."""
    dir_ = _staging_dir()
    dir_.mkdir(parents=True, exist_ok=True)
    for existing in dir_.iterdir():
        if existing.is_file():
            existing.unlink(missing_ok=True)

    upload_id = secrets.token_urlsafe(32)
    _zip_path(upload_id).write_bytes(content)
    return upload_id


def save_plan(upload_id: str, plan: dict) -> None:
    """Persist plan as JSON alongside the staged zip for upload_id.

    Silently does nothing if upload_id is malformed (see module docstring
    convention) — in normal operation this is only ever called with an id
    just returned by stage(), so a malformed id here signals a caller bug,
    not a recoverable client condition."""
    if not _valid_token(upload_id):
        return
    dir_ = _staging_dir()
    dir_.mkdir(parents=True, exist_ok=True)
    _plan_path(upload_id).write_text(json.dumps(plan))


def load_plan(upload_id: str) -> dict | None:
    """Return the persisted plan dict for upload_id, or None if the id is
    malformed, nothing is staged under it, the plan file is missing, or
    the staged pair has exceeded the 30-minute TTL (checked here directly,
    independent of whether sweep_expired() has run)."""
    if not _valid_token(upload_id):
        return None
    path = _plan_path(upload_id)
    if not path.is_file() or _is_expired(path):
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def staged_path(upload_id: str) -> Path | None:
    """Return the Path to the staged zip for upload_id, or None if the id
    is malformed, nothing is staged under it, or it has exceeded the
    30-minute TTL. Never builds or returns a path outside the staging dir
    — a malformed id is rejected by regex before any path is derived."""
    if not _valid_token(upload_id):
        return None
    path = _zip_path(upload_id)
    if not path.is_file() or _is_expired(path):
        return None
    return path


def sweep_expired() -> None:
    """Delete any staged file (zip or plan json) whose mtime is older than
    the 30-minute TTL. No-op if the staging dir doesn't exist. There is no
    background task — callers (the plan/apply routers) run this
    opportunistically on every request. This is the actual disk reclaim;
    staged_path()/load_plan() already refuse to serve expired data
    regardless of whether this has been called."""
    dir_ = _staging_dir()
    if not dir_.is_dir():
        return
    for f in dir_.iterdir():
        if f.is_file() and _is_expired(f):
            f.unlink(missing_ok=True)


def consume(upload_id: str) -> None:
    """Delete both the staged zip and plan json for upload_id, if present.
    Silently does nothing if upload_id is malformed. Unlike the read
    functions, this does not gate on TTL — a caller with a valid
    (non-expired-per-load_plan) upload_id in hand should always be able to
    clean it up."""
    if not _valid_token(upload_id):
        return
    _zip_path(upload_id).unlink(missing_ok=True)
    _plan_path(upload_id).unlink(missing_ok=True)
