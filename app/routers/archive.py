"""Portable library archive export/import — moving a Shelf collection
between instances (or apps that can read the format) without credentials
or instance-specific data. See app/services/archive.py for the format
contract."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app import config
from app.auth import require_role
from app.database import get_db
from app.services.archive import (
    ArchiveError,
    FORMAT_NAME,
    MAX_IMPORT_UPLOAD_SIZE,
    build_archive,
    import_tmp_path,
    merge_archive,
    read_archive,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

def _refusal(message: str) -> dict:
    """A zeroed report carrying a user-facing reason. Built fresh each call —
    a module-level template would share one `errors` list across responses.
    The settings card keys off `error` before anything else, so a refusal
    renders as its message, not as an import that did nothing."""
    return {
        "error": message,
        "imported": 0, "updated": 0, "skipped": 0, "errors": [],
        "covers_installed": 0, "format": FORMAT_NAME,
    }


@router.get("/export/archive")
async def export_archive(_=Depends(require_role("admin"))):
    """Download a portable library archive (zip) of the collection."""
    with get_db() as db:
        archive_path = build_archive(db)
    filename = f"shelf_archive_{datetime.now():%Y%m%d}.zip"
    return FileResponse(
        str(archive_path),
        filename=filename,
        media_type="application/zip",
        background=BackgroundTask(archive_path.unlink, missing_ok=True),
    )


@router.post("/import/archive")
async def import_archive(request: Request, _=Depends(require_role("admin"))):
    """Import a portable library archive (zip), merging it into this
    instance. Merge logic lives in app.services.archive.merge_archive; this
    stays a thin upload/dispatch layer."""
    form = await request.form()
    mode = form.get("mode", "skip")
    if mode not in ("skip", "update"):
        mode = "skip"

    upload = form.get("file")
    if not upload or not hasattr(upload, "read"):
        return _refusal("No file uploaded")

    content = await upload.read()
    if len(content) > MAX_IMPORT_UPLOAD_SIZE:
        return _refusal(
            f"Archive is too large (max {MAX_IMPORT_UPLOAD_SIZE // (1024 * 1024)} MB)."
        )

    # Resolved at call time (config.DATA_DIR), not a frozen module-level
    # import — see import_tmp_path()'s docstring for why.
    tmp_path = import_tmp_path()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path.write_bytes(content)
    try:
        with read_archive(tmp_path) as reader:
            with get_db() as db:
                return merge_archive(db, reader, mode)
    except ArchiveError as e:
        # These messages are written to be shown to the user verbatim.
        return _refusal(e.args[0])
    finally:
        tmp_path.unlink(missing_ok=True)
