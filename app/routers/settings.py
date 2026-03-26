from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, FileResponse

from app.auth import require_role
from app.config import DATABASE_PATH, DATA_DIR
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
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                (key, value, value),
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

    # Validate: try opening as SQLite and check for items table
    try:
        conn = sqlite3.connect(str(tmp_path))
        conn.execute("SELECT COUNT(*) FROM items")
        conn.close()
    except Exception:
        tmp_path.unlink(missing_ok=True)
        return {"ok": False, "message": "Invalid database file — must be a valid Shelf SQLite database"}

    # Replace current database
    import shutil
    shutil.copy2(str(tmp_path), str(DATABASE_PATH))
    tmp_path.unlink(missing_ok=True)

    return {"ok": True, "message": "Database restored. Restart the container to apply."}
