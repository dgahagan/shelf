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
from app.services import import_staging
from app.services.archive import (
    ArchiveError,
    FORMAT_NAME,
    MAX_IMPORT_UPLOAD_SIZE,
    SELECTION_DEFAULTS,
    apply_plan,
    build_archive,
    import_tmp_path,
    merge_archive,
    normalize_selection,
    plan_archive,
    read_archive,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_TRUTHY_STRINGS = {"true", "on", "1", "yes"}

_PREVIEW_EXPIRED = "Preview expired — upload the archive again."


def _truthy(value, default: bool) -> bool:
    """Checkbox-style form truthiness for a single field. A field that is
    absent from the form (`value is None`) falls back to `default` — the
    matching SELECTION_DEFAULTS value, not False, so an old client that
    never sends an unknown toggle doesn't silently turn it off. A field
    that is present is true only for the conventional checkbox/boolean
    spellings, case-insensitive; anything else (including an empty
    string) is false."""
    if value is None:
        return default
    return str(value).strip().lower() in _TRUTHY_STRINGS


def _refusal(message: str) -> dict:
    """A zeroed report carrying a user-facing reason. Built fresh each call —
    a module-level template would share one `errors` list across responses.
    The settings card keys off `error` before anything else, so a refusal
    renders as its message, not as an import that did nothing."""
    return {
        "error": message,
        "imported": 0, "updated": 0, "skipped": 0, "errors": [],
        "covers_installed": 0, "format": FORMAT_NAME,
        "covers_replaced": 0, "drifted": 0,
        "deselected": {
            "creates": 0, "updates": 0, "covers": 0,
            "reading_log": 0, "checkouts": 0, "valuation_history": 0,
        },
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
    instance in one request (no staging) — plan-everything then
    apply-everything under the hood. Merge logic lives in
    app.services.archive.merge_archive; this stays a thin upload/dispatch
    layer. `replace_covers` is the one behavior-change knob added in 0.7.0:
    off by default, same as the /plan+/apply flow."""
    form = await request.form()
    mode = form.get("mode", "skip")
    if mode not in ("skip", "update"):
        mode = "skip"
    replace_covers = _truthy(form.get("replace_covers"), False)

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
                return merge_archive(db, reader, mode, replace_covers=replace_covers)
    except ArchiveError as e:
        # These messages are written to be shown to the user verbatim.
        return _refusal(e.args[0])
    finally:
        tmp_path.unlink(missing_ok=True)


@router.post("/import/archive/plan")
async def plan_archive_import(request: Request, _=Depends(require_role("admin"))):
    """Preview an archive import: validate the upload, compute what a merge
    would do, and stage the bytes + the plan for a later /apply call.
    Writes nothing to the library. Validation runs *before* staging — a
    file that fails read_archive() is never staged (nothing to clean up,
    nothing half-done for /apply to trip over)."""
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

    import_staging.sweep_expired()

    # Same shared tmp path the legacy endpoint uses — validation happens
    # against a real file on disk (read_archive takes a Path), not the raw
    # bytes, so this reuses that rail exactly rather than re-implementing
    # it against an in-memory buffer.
    tmp_path = import_tmp_path()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path.write_bytes(content)
    try:
        with read_archive(tmp_path) as reader:
            with get_db() as db:
                plan = plan_archive(db, reader, mode=mode)
    except ArchiveError as e:
        return _refusal(e.args[0])
    finally:
        tmp_path.unlink(missing_ok=True)

    # Only a validated upload reaches here — stage the raw bytes (not the
    # tmp file, which is already gone) and persist the plan alongside them.
    upload_id = import_staging.stage(content)
    import_staging.save_plan(upload_id, plan)
    return {"upload_id": upload_id, "plan": plan}


@router.post("/import/archive/apply")
async def apply_archive_import(request: Request, _=Depends(require_role("admin"))):
    """Apply a plan previously returned by /plan. The plan's own `mode` is
    authoritative — the submitted `mode` is only compared against it, to
    refuse a stale form (reviewed under one mode, submitted under another)
    rather than silently applying the wrong one. Selection fields are
    checkbox-style truthy strings; an absent field falls back to its
    SELECTION_DEFAULTS value. Staged files are consumed in `finally`,
    whether apply succeeds or errors."""
    form = await request.form()
    upload_id = form.get("upload_id") or ""

    import_staging.sweep_expired()

    zip_path = import_staging.staged_path(upload_id)
    plan = import_staging.load_plan(upload_id)
    if zip_path is None or plan is None:
        return _refusal(_PREVIEW_EXPIRED)

    mode = form.get("mode", "skip")
    if mode not in ("skip", "update"):
        mode = "skip"
    if mode != plan.get("mode"):
        # The staged upload/plan are left in place — this is a stale form,
        # not an invalid or expired preview, so the same upload_id can be
        # retried once the form's mode matches what was actually reviewed.
        return _refusal("This plan was reviewed under a different mode — preview it again.")

    selection = normalize_selection({
        key: _truthy(form.get(key), default)
        for key, default in SELECTION_DEFAULTS.items()
    })

    try:
        with read_archive(zip_path) as reader:
            with get_db() as db:
                return apply_plan(db, reader, plan, selection)
    except ArchiveError as e:
        return _refusal(e.args[0])
    finally:
        import_staging.consume(upload_id)
