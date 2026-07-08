from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, FileResponse

from app.auth import require_role
from app.config import DATABASE_PATH, DATA_DIR
from app.crypto import SENSITIVE_KEYS, encrypt_value, get_encryption_key
from app.database import get_db

router = APIRouter(prefix="/api/settings", dependencies=[Depends(require_role("admin"))])

_INTEGRATION_KEYS = (
    "abs_url",
    "abs_token",
    "isbndb_api_key",
    "tmdb_api_key",
    "hardcover_token",
    "igdb_client_id",
    "igdb_client_secret",
)


def _upsert_setting(db, key: str, value: str, cleared: bool = False):
    """Write one setting. Sensitive keys are write-only: the page renders
    masked fields that post empty, so a blank submission keeps the stored
    value unless the matching clear checkbox was ticked."""
    if key in SENSITIVE_KEYS:
        if cleared:
            value = ""
        elif not value:
            return
        else:
            value = encrypt_value(value, get_encryption_key())
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
        (key, value, value),
    )


@router.post("")
async def update_settings(request: Request):
    """Save integration settings.

    Only keys present in the submitted form are written — each integration
    section is its own form, and sections must not blank each other's values
    (previously handled by echoing every credential as a hidden input).
    """
    form = await request.form()
    with get_db() as db:
        for key in _INTEGRATION_KEYS:
            if key not in form:
                continue
            value = (form.get(key) or "").strip()
            if key == "abs_url":
                value = value.rstrip("/")
            _upsert_setting(db, key, value, cleared=form.get(f"clear_{key}") == "on")
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/vision")
async def update_vision_settings(
    vision_provider: str = Form(""),
    anthropic_api_key: str = Form(""),
    anthropic_vision_model: str = Form(""),
    openai_base_url: str = Form(""),
    openai_api_key: str = Form(""),
    openai_vision_model: str = Form(""),
    openai_ingest_long_edge: str = Form(""),
    ollama_url: str = Form(""),
    ollama_model: str = Form(""),
    ollama_ingest_long_edge: str = Form(""),
    clear_anthropic_api_key: str = Form(""),
    clear_openai_api_key: str = Form(""),
):
    """Save photo-intake vision settings.

    Separate handler (like /lending) so this partial form doesn't blank
    the other integration credentials.
    """
    if vision_provider not in ("", "anthropic", "openai", "ollama"):
        return {"ok": False, "message": "Unknown vision provider"}
    ollama_long_edge = ollama_ingest_long_edge.strip()
    if ollama_long_edge and not ollama_long_edge.isdigit():
        return {"ok": False, "message": "Ollama image size must be a whole number of pixels"}
    openai_long_edge = openai_ingest_long_edge.strip()
    if openai_long_edge and not openai_long_edge.isdigit():
        return {"ok": False, "message": "OpenAI image size must be a whole number of pixels"}
    # Each API key clears independently via its own checkbox.
    clears = {"anthropic_api_key": clear_anthropic_api_key == "on",
              "openai_api_key": clear_openai_api_key == "on"}
    with get_db() as db:
        for key, value in [
            ("vision_provider", vision_provider),
            ("anthropic_api_key", anthropic_api_key.strip()),
            ("anthropic_vision_model", anthropic_vision_model.strip()),
            ("openai_base_url", openai_base_url.strip().rstrip("/")),
            ("openai_api_key", openai_api_key.strip()),
            ("openai_vision_model", openai_vision_model.strip()),
            ("openai_ingest_long_edge", openai_long_edge),
            ("ollama_url", ollama_url.strip().rstrip("/")),
            ("ollama_model", ollama_model.strip()),
            ("ollama_ingest_long_edge", ollama_long_edge),
        ]:
            _upsert_setting(db, key, value, cleared=clears.get(key, False))
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/lending")
async def update_lending_settings(
    lending_overdue_days: str = Form("28"),
    notify_url: str = Form(""),
    notify_format: str = Form("ntfy"),
    clear_notify_url: str = Form(""),
):
    """Save lending/reminder settings.

    Separate from POST /api/settings on purpose: that handler writes its
    entire fixed key list from the form, so posting a partial form there
    would blank the integration credentials.
    """
    from app.services.notify import FORMATS

    days = lending_overdue_days.strip() or "28"
    if not days.isdigit():
        return {"ok": False, "message": "Overdue days must be a whole number"}
    fmt = notify_format if notify_format in FORMATS else "ntfy"

    with get_db() as db:
        for key, value in [
            ("lending_overdue_days", days),
            ("notify_url", notify_url.strip()),
            ("notify_format", fmt),
        ]:
            _upsert_setting(db, key, value, cleared=clear_notify_url == "on" and key == "notify_url")
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/notify-test")
async def notify_test(request: Request):
    """Send a test notification to the URL provided in the request body."""
    from app.services.notify import send_notification

    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "message": "Invalid request"}
    url = (body.get("url") or "").strip()
    fmt = body.get("format") or "ntfy"
    if not url:
        # Masked field posts empty — test the stored URL instead
        from app.database import get_setting
        with get_db() as db:
            url = get_setting(db, "notify_url")
    if not url:
        return {"ok": False, "message": "No URL provided"}

    ok = await send_notification(url, "Shelf test notification", "Loan reminders are working!", fmt)
    return {"ok": ok, "message": "Test notification sent" if ok else "Send failed — check the URL and server logs"}


def _vacuum_backup():
    backup_path = DATA_DIR / "shelf_backup.db"
    backup_path.unlink(missing_ok=True)
    with get_db() as db:
        db.execute("VACUUM INTO ?", (str(backup_path),))
    return backup_path


@router.get("/backup")
async def download_backup():
    """Download a plain backup of the SQLite database."""
    backup_path = _vacuum_backup()
    filename = f"shelf_backup_{datetime.now():%Y%m%d_%H%M}.db"
    return FileResponse(str(backup_path), filename=filename, media_type="application/octet-stream")


@router.post("/backup")
async def download_backup_encrypted(passphrase: str = Form("")):
    """Download a backup, AES-GCM-encrypted when a passphrase is provided —
    an off-site copy then exposes neither user hashes nor item data."""
    from app.crypto import encrypt_backup

    backup_path = _vacuum_backup()
    if not passphrase:
        filename = f"shelf_backup_{datetime.now():%Y%m%d_%H%M}.db"
        return FileResponse(str(backup_path), filename=filename, media_type="application/octet-stream")

    encrypted = encrypt_backup(backup_path.read_bytes(), passphrase)
    enc_path = DATA_DIR / "shelf_backup.db.enc"
    enc_path.write_bytes(encrypted)
    backup_path.unlink(missing_ok=True)  # don't leave the plaintext copy behind
    filename = f"shelf_backup_{datetime.now():%Y%m%d_%H%M}.db.enc"
    return FileResponse(str(enc_path), filename=filename, media_type="application/octet-stream")


@router.post("/restore")
async def restore_backup(request: Request):
    """Restore database from uploaded .db file. Requires container restart."""
    import sqlite3
    form = await request.form()
    db_file = form.get("file")
    if not db_file or not hasattr(db_file, "read"):
        return {"ok": False, "message": "No file uploaded"}

    content = await db_file.read()
    max_db_size = 500 * 1024 * 1024  # 500 MB
    if len(content) > max_db_size:
        return {"ok": False, "message": "File too large (max 500 MB)"}

    from app.crypto import is_encrypted_backup, decrypt_backup
    if is_encrypted_backup(content):
        passphrase = (form.get("passphrase") or "").strip()
        if not passphrase:
            return {"ok": False, "message": "This backup is encrypted — enter its passphrase"}
        try:
            content = decrypt_backup(content, passphrase)
        except ValueError as e:
            return {"ok": False, "message": str(e)}

    tmp_path = DATA_DIR / "shelf_restore_tmp.db"
    tmp_path.write_bytes(content)

    # Validate: must be a valid Shelf SQLite database with the expected schema
    _REQUIRED_TABLES = {"items", "users", "settings"}
    _REQUIRED_COLUMNS = {
        "items": {"id", "title", "media_type"},
        "users": {"id", "username", "password", "role"},
        "settings": {"key", "value"},
    }

    try:
        conn = sqlite3.connect(str(tmp_path))

        # Reject databases with triggers (could execute arbitrary SQL on queries)
        triggers = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall()
        if triggers:
            conn.close()
            tmp_path.unlink(missing_ok=True)
            return {"ok": False, "message": "Database contains triggers — not allowed"}

        # Reject databases with views (could embed malicious SQL)
        views = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'view'"
        ).fetchall()
        if views:
            conn.close()
            tmp_path.unlink(missing_ok=True)
            return {"ok": False, "message": "Database contains views — not allowed"}

        # Reject virtual tables (their module code runs on access)
        vtables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND sql LIKE 'CREATE VIRTUAL TABLE%'"
        ).fetchall()
        if vtables:
            conn.close()
            tmp_path.unlink(missing_ok=True)
            return {"ok": False, "message": "Database contains virtual tables — not allowed"}

        # Reject databases with attached databases
        dbs = conn.execute("PRAGMA database_list").fetchall()
        if len(dbs) > 1:
            conn.close()
            tmp_path.unlink(missing_ok=True)
            return {"ok": False, "message": "Database has attached databases — not allowed"}

        # Verify all required tables exist
        existing_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing_tables = _REQUIRED_TABLES - existing_tables
        if missing_tables:
            conn.close()
            tmp_path.unlink(missing_ok=True)
            return {
                "ok": False,
                "message": f"Not a valid Shelf database (missing tables: {', '.join(sorted(missing_tables))})",
            }

        # Verify required columns on each critical table
        for table, required_cols in _REQUIRED_COLUMNS.items():
            existing_cols = {
                row[1]
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            missing_cols = required_cols - existing_cols
            if missing_cols:
                conn.close()
                tmp_path.unlink(missing_ok=True)
                return {
                    "ok": False,
                    "message": f"Incompatible schema: '{table}' table is missing columns: {', '.join(sorted(missing_cols))}",
                }

        conn.close()
    except Exception:
        tmp_path.unlink(missing_ok=True)
        return {"ok": False, "message": "Invalid database file — must be a valid Shelf SQLite database"}

    # Replace current database
    import shutil
    shutil.copy2(str(tmp_path), str(DATABASE_PATH))
    tmp_path.unlink(missing_ok=True)

    # Invalidate all existing sessions by bumping every user's token_version.
    # Encrypted settings in the restored DB stay readable because the
    # encryption key lives outside the DB (env var or DATA_DIR/encryption.key)
    # and is unaffected by the restore. A backup from a *different* install
    # was encrypted with that install's key — startup migration logs a warning
    # and those credentials must be re-entered. Backups still encrypted under
    # the legacy JWT-derived key are re-encrypted on the required restart.
    from app.database import init_db
    init_db()  # bring an older restored DB up to the current schema first
    with get_db() as db:
        db.execute("UPDATE users SET token_version = token_version + 1")

    return {"ok": True, "message": "Database restored. All sessions invalidated. Restart the container to apply."}
