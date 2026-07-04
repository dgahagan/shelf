from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, FileResponse

from app.auth import require_role, get_secret_key
from app.config import DATABASE_PATH, DATA_DIR
from app.crypto import SENSITIVE_KEYS, encrypt_value, decrypt_value
from app.database import get_db

router = APIRouter(prefix="/api/settings", dependencies=[Depends(require_role("admin"))])


@router.post("")
async def update_settings(
    abs_url: str = Form(""),
    abs_token: str = Form(""),
    isbndb_api_key: str = Form(""),
    tmdb_api_key: str = Form(""),
    hardcover_token: str = Form(""),
    igdb_client_id: str = Form(""),
    igdb_client_secret: str = Form(""),
):
    secret = get_secret_key()
    with get_db() as db:
        for key, value in [
            ("abs_url", abs_url.strip().rstrip("/")),
            ("abs_token", abs_token.strip()),
            ("isbndb_api_key", isbndb_api_key.strip()),
            ("tmdb_api_key", tmdb_api_key.strip()),
            ("hardcover_token", hardcover_token.strip()),
            ("igdb_client_id", igdb_client_id.strip()),
            ("igdb_client_secret", igdb_client_secret.strip()),
        ]:
            stored = encrypt_value(value, secret) if key in SENSITIVE_KEYS and value else value
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                (key, stored, stored),
            )
    return RedirectResponse(url="/settings", status_code=303)


@router.get("/backup")
async def download_backup():
    """Download a backup of the SQLite database."""
    backup_path = DATA_DIR / "shelf_backup.db"
    backup_path.unlink(missing_ok=True)
    with get_db() as db:
        db.execute("VACUUM INTO ?", (str(backup_path),))
    filename = f"shelf_backup_{datetime.now():%Y%m%d_%H%M}.db"
    return FileResponse(str(backup_path), filename=filename, media_type="application/octet-stream")


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
    # We deliberately do NOT rotate the secret key here: the restored DB's
    # encrypted settings (API tokens) are Fernet-encrypted with the key stored
    # inside that same DB, and rotating it would make them undecryptable.
    from app.database import init_db
    init_db()  # bring an older restored DB up to the current schema first
    with get_db() as db:
        db.execute("UPDATE users SET token_version = token_version + 1")

    return {"ok": True, "message": "Database restored. All sessions invalidated. Restart the container to apply."}
