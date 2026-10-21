"""Shelf audits: the Audits pages and the Scan page's audit strip.

Two surfaces, one service (`app/services/audits.py`):

- **The strip** — `POST /api/audits`, `GET /api/audits/summary`,
  `POST /api/audits/{audit_id}/finish` — lives on the Scan page in Inventory
  mode. Inventory mode is core, so these three carry **no** feature gate (the
  `collector` registry entry lists them under `ungated`): with Collector tools
  off a user still starts, scans into and finishes an audit; only the report
  pages refuse.
- **The pages** — `/audits` and `/audits/{audit_id}` — are Collector tools.

Every route declares `require_role("editor")` **then** `require_feature`, the
order the registry lint checks. `/audits` is declared above
`/audits/{audit_id}` and `/summary` above `/{audit_id}/…` (G119).

htmx never swaps a 4xx body, so a refusal the user can correct answers **200**
with the fragment carrying an inline error, and a not-found answers 404 with an
`HX-Trigger` toast (`_not_found`) whose body nothing renders. Every write takes
`BEGIN IMMEDIATE` first (G18) and nothing logs under it (G3).
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import Response

from app.auth import require_role
from app.database import get_db
from app.features import feature_enabled, require_feature
from app.routers import items_common
from app.services import audits, item_copies
from app.services.write_targets import ItemValueError

page_router = APIRouter()
router = APIRouter(prefix="/api/audits")


def _parse_id(raw: str) -> int | None:
    """A positive integer form/query value, or None for anything else."""
    raw = (raw or "").strip()
    if raw.isascii() and raw.isdigit() and int(raw) > 0:
        return int(raw)
    return None


def _not_found(message: str) -> Response:
    """A 404 the user can see: htmx shows the toast and swaps nothing."""
    resp = Response(status_code=404)
    resp.headers["HX-Trigger"] = items_common._toast_header(message, "error")
    return resp


def _strip(request: Request, db, session: dict | None, *, error: str | None = None,
           finished: dict | None = None):
    """The strip fragment: an open session's summary, or the start-or-join view."""
    context: dict = {"session": None, "error": error, "finished": finished}
    if session is not None and session["is_open"]:
        report = audits.build_report(db, session)
        context.update(session=session, counts=audits.summary_counts(report))
    else:
        context["open_sessions"] = audits.open_sessions(db)
    return request.app.state.templates.TemplateResponse(
        request, "fragments/audit_strip.html", context)


# ── The strip (ungated) ─────────────────────────────────────────────────────

@router.get("/summary")
async def audit_summary(
    request: Request,
    audit_id: str = Query(""),
    _=Depends(require_role("editor")),
):
    """One route for both states: an open session's summary, or the start view."""
    with get_db() as db:
        return _strip(request, db, audits.get_session(db, _parse_id(audit_id)))


@router.post("")
async def start_audit(
    request: Request,
    scope: str = Form(""),
    location_id: str = Form(""),
    _=Depends(require_role("editor")),
):
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            audit_id = audits.create_session(
                db, scope=scope, location_id=_parse_id(location_id))
        except audits.AuditError as e:
            db.rollback()
            return _strip(request, db, None, error=str(e))
        db.commit()
        return _strip(request, db, audits.get_session(db, audit_id))


@router.post("/{audit_id}/finish")
async def finish_audit(
    request: Request,
    audit_id: int,
    _=Depends(require_role("editor")),
):
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        session = audits.get_session(db, audit_id)
        if session is None:
            db.rollback()
            return _not_found("That audit was deleted")
        audits.finish_session(db, audit_id)
        db.commit()
        if feature_enabled("collector"):
            # An integer read back from the database, never a request string.
            return Response(status_code=200,
                            headers={"HX-Redirect": f"/audits/{session['id']}"})
        return _strip(request, db, None, finished=audits.get_session(db, audit_id))


# ── Report actions (Collector tools) ────────────────────────────────────────

NOTES_MAX = 2000


def _report_response(request: Request, db, session: dict, error: str | None = None):
    """The report fragment, recomputed from the database as it now stands."""
    return request.app.state.templates.TemplateResponse(
        request, "fragments/audit_report.html",
        {"session": session, "report": audits.build_report(db, session), "error": error})


@router.post("/{audit_id}/resolve")
async def resolve_elsewhere(
    request: Request,
    audit_id: int,
    entry_id: str = Form(""),
    copy_id: str = Form(""),
    _=Depends(require_role("editor")),
    __=Depends(require_feature("collector")),
):
    """Decision 9: the scan was of this copy — move it to where it was seen."""
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        session = audits.get_session(db, audit_id)
        if session is None:
            db.rollback()
            return _not_found("That audit was deleted")
        eid, cid = _parse_id(entry_id), _parse_id(copy_id)
        entry = db.execute(
            "SELECT * FROM audit_entries WHERE id = ? AND session_id = ? AND result = 'elsewhere'",
            (eid, audit_id)).fetchone() if eid else None
        if entry is None:
            db.rollback()
            return _not_found("That scan is no longer in this audit")
        # The entry stays as history once resolved, so a replay (a retry, a
        # stale tab, a second device) would move a second copy. A later
        # `relocated` entry for the same item and location is the resolution
        # — the same rule `build_report` uses to hide the line.
        resolved = db.execute(
            "SELECT 1 FROM audit_entries WHERE session_id = ? AND result = 'relocated' "
            "AND id > ? AND item_id IS ? AND location_id IS ?",
            (audit_id, entry["id"], entry["item_id"], entry["location_id"])).fetchone()
        if resolved is not None:
            db.rollback()
            return _report_response(request, db, session, "That scan was already resolved")
        copy = db.execute(
            "SELECT id, item_id, is_primary FROM copies_live WHERE id = ?",
            (cid,)).fetchone() if cid else None
        if copy is None:
            db.rollback()
            return _not_found("That copy no longer exists")
        error = None
        if copy["item_id"] != entry["item_id"]:
            error = "That copy belongs to a different item"
        elif entry["location_id"] is None:
            error = "That location was deleted"
        if error is None:
            try:
                item_copies.relocate_copy(db, copy, entry["location_id"])
                item_copies.clear_missing(db, copy["id"])
                audits.add_entry(
                    db, audit_id, item_id=copy["item_id"], copy_id=copy["id"],
                    location_id=entry["location_id"], raw_code=entry["raw_code"],
                    result="relocated")
            except ItemValueError:
                db.rollback()
                error = "That location was deleted"
        if error is not None:
            db.rollback()
            return _report_response(request, db, session, error)
        db.commit()
        return _report_response(request, db, session)


@router.post("/{audit_id}/missing")
async def mark_missing(
    request: Request,
    audit_id: int,
    copy_id: list[int] = Form(...),
    _=Depends(require_role("editor")),
    __=Depends(require_feature("collector")),
):
    """Decision 13: mark only copies the report itself lists as missing."""
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        session = audits.get_session(db, audit_id)
        if session is None:
            db.rollback()
            return _not_found("That audit was deleted")
        report = audits.build_report(db, session)
        for cid in sorted(set(copy_id) & report.missing_copy_ids()):
            item_copies.mark_missing(db, cid)
        db.commit()
        return _report_response(request, db, session)


def _notes_response(request: Request, session: dict, value: str, error: str | None = None):
    return request.app.state.templates.TemplateResponse(
        request, "fragments/audit_notes.html",
        {"session": session, "notes_value": value, "error": error})


@router.post("/{audit_id}/notes")
async def save_notes(
    request: Request,
    audit_id: int,
    notes: str = Form(""),
    _=Depends(require_role("editor")),
    __=Depends(require_feature("collector")),
):
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        session = audits.get_session(db, audit_id)
        if session is None:
            db.rollback()
            return _not_found("That audit was deleted")
        if len(notes) > NOTES_MAX:
            db.rollback()
            return _notes_response(
                request, session, notes,
                f"Notes are limited to {NOTES_MAX:,} characters; yours has {len(notes):,}.")
        audits.set_notes(db, audit_id, notes.strip())
        db.commit()
        session = audits.get_session(db, audit_id)
        return _notes_response(request, session, session["notes"] or "")


@router.delete("/{audit_id}")
async def delete_audit(
    audit_id: int,
    _=Depends(require_role("admin")),
    __=Depends(require_feature("collector")),
):
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        if not audits.delete_session(db, audit_id):
            db.rollback()
            return _not_found("That audit was already deleted")
        db.commit()
    return Response(status_code=200, headers={"HX-Redirect": "/audits"})


# ── The pages (Collector tools) ─────────────────────────────────────────────

@page_router.get("/audits")
async def audits_page(
    request: Request,
    _=Depends(require_role("editor")),
    __=Depends(require_feature("collector")),
):
    with get_db() as db:
        sessions = audits.list_sessions(db)
    return request.app.state.templates.TemplateResponse(
        request, "audits.html", {"sessions": sessions})


@page_router.get("/audits/{audit_id}")
async def audit_report_page(
    request: Request,
    audit_id: int,
    _=Depends(require_role("editor")),
    __=Depends(require_feature("collector")),
):
    with get_db() as db:
        session = audits.get_session(db, audit_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Audit not found")
        report = audits.build_report(db, session)
    return request.app.state.templates.TemplateResponse(
        request, "audit.html", {"session": session, "report": report})
