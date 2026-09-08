"""First-class periodical catalogue and 977 scan confirmation routes."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import require_role
from app.config import HTTP_TIMEOUT
from app.database import get_db
from app.services import periodical_records, periodical_scan, periodicals
from app.services.item_write import ItemValueError, insert_item

router = APIRouter()


def _issue_title(
    publication: str,
    *,
    issue_number: str | None = None,
    issue_date: str | None = None,
    cover_date_label: str | None = None,
) -> str:
    publication = publication.strip()
    if issue_number and issue_number.strip():
        return f"{publication} — No. {issue_number.strip()}"
    if cover_date_label and cover_date_label.strip():
        return f"{publication} — {cover_date_label.strip()}"
    if issue_date and issue_date.strip():
        return f"{publication} — {issue_date.strip()}"
    return publication


def _issue_year(issue_date: str | None) -> int | None:
    value = (issue_date or "").strip()
    return int(value[:4]) if len(value) >= 4 and value[:4].isdigit() else None


def find_periodical_item(raw: str) -> dict | None:
    """Resolve a stored issue only from a full 977 + supplement identity.

    A 977 carrier identifies the publication, not one concrete issue. A
    carrier-only scan must therefore never resolve to an existing issue even
    when only one issue currently uses that carrier: doing so would make the
    next issue a false duplicate. The add-on is required at this identity
    boundary.
    """
    serial = periodicals.parse_barcode(raw)
    if serial is None or not serial.supplement:
        return None

    with get_db() as db:
        periodical_records.ensure_schema(db)
        rows = db.execute(
            """SELECT i.*, l.name AS location_name
               FROM periodical_issues pi
               JOIN items i ON i.id = pi.item_id
               LEFT JOIN locations l ON l.id = i.location_id
               WHERE pi.barcode_ean = ? AND pi.barcode_supplement = ?
               ORDER BY i.id LIMIT 2""",
            (serial.ean13, serial.supplement),
        ).fetchall()
    return dict(rows[0]) if len(rows) == 1 else None


async def render_scan_candidate(
    request: Request,
    templates,
    raw: str,
    location_id: int | None,
    mode: str,
):
    """Render publication metadata plus issue-confirmation fields for a 977 scan."""
    serial = periodicals.parse_barcode(raw)
    if serial is None:
        return HTMLResponse("Not a valid periodical barcode", status_code=400)

    existing = find_periodical_item(raw)
    if existing:
        return templates.TemplateResponse(
            request,
            "fragments/scan_result.html",
            {
                "status": "duplicate",
                "isbn": serial.full_code,
                "title": existing["title"],
                "item_id": existing["id"],
                "message": "This periodical issue is already in the collection",
            },
        )

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        result = await periodical_scan.resolve_barcode(raw, client)
    candidate = result.payload if isinstance(result.payload, dict) else {}
    notice = None
    if not result.found:
        notice = {
            "rate_limited": "The ISSN metadata source is rate-limiting requests. The barcode was decoded; confirm the publication below.",
            "transport_failed": "The ISSN metadata source could not be reached. The barcode was decoded; confirm the publication below.",
            "rejected": "The ISSN metadata source rejected the lookup. The barcode was decoded; confirm the publication below.",
            "no_match": "No publication metadata was returned for this ISSN. Enter the publication title below.",
        }.get(result.outcome, "Publication metadata was unavailable; confirm the publication below.")

    return templates.TemplateResponse(
        request,
        "fragments/periodical_scan_result.html",
        {
            "candidate": candidate,
            "raw_barcode": serial.full_code,
            "location_id": location_id,
            "mode": mode if mode in {"add", "wishlist"} else "add",
            "notice": notice,
            "csrf_token": request.cookies.get("csrf_token", ""),
        },
    )


@router.post("/api/periodicals/confirm")
async def confirm_periodical_issue(
    raw_barcode: str = Form(...),
    publication_title: str = Form(...),
    publisher: str = Form(""),
    language: str = Form(""),
    volume: str = Form(""),
    issue_number: str = Form(""),
    issue_date: str = Form(""),
    cover_date_label: str = Form(""),
    location_id: int | None = Form(None),
    mode: str = Form("add"),
    _=Depends(require_role("editor")),
):
    """Create one concrete issue after the user confirms its issue identity."""
    serial = periodicals.parse_barcode(raw_barcode)
    publication_title = publication_title.strip()
    if serial is None or not publication_title:
        return HTMLResponse("A valid periodical barcode and publication title are required", status_code=400)
    if mode not in {"add", "wishlist"}:
        mode = "add"

    try:
        with get_db() as db:
            publication_id = periodical_records.upsert_publication(
                db,
                title=publication_title,
                issn=serial.issn,
                publisher=publisher.strip() or None,
                language=language.strip() or None,
            )
            existing_id = periodical_records.find_duplicate_issue(
                db,
                publication_id,
                volume=volume,
                issue_number=issue_number,
                issue_date=issue_date,
                barcode_ean=serial.ean13,
                barcode_supplement=serial.supplement,
            )
            if existing_id:
                return RedirectResponse(f"/item/{existing_id}", status_code=303)

            item_id = insert_item(
                db,
                title=_issue_title(
                    publication_title,
                    issue_number=issue_number,
                    issue_date=issue_date,
                    cover_date_label=cover_date_label,
                ),
                media_type="magazine",
                publisher=publisher.strip() or None,
                publish_year=_issue_year(issue_date),
                location_id=location_id,
                owned=0 if mode == "wishlist" else 1,
                source="issn",
            )
            periodical_records.link_issue(
                db,
                item_id=item_id,
                publication_id=publication_id,
                volume=volume,
                issue_number=issue_number,
                issue_date=issue_date,
                barcode_ean=serial.ean13,
                barcode_supplement=serial.supplement,
                cover_date_label=cover_date_label,
            )
    except ItemValueError as exc:
        return HTMLResponse(str(exc), status_code=400)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=400)

    return RedirectResponse(f"/periodicals/{publication_id}", status_code=303)


@router.get("/periodicals")
async def periodicals_page(request: Request, _=Depends(require_role("viewer"))):
    with get_db() as db:
        periodical_records.ensure_schema(db)
        publications = db.execute(
            """SELECT p.*, COUNT(pi.item_id) AS issue_count
               FROM periodical_publications p
               LEFT JOIN periodical_issues pi ON pi.publication_id = p.id
               GROUP BY p.id
               ORDER BY p.title COLLATE NOCASE"""
        ).fetchall()
    return request.app.state.templates.TemplateResponse(
        request, "periodicals.html", {"publications": publications}
    )


@router.get("/periodicals/{publication_id}")
async def publication_page(
    request: Request,
    publication_id: int,
    _=Depends(require_role("viewer")),
):
    with get_db() as db:
        periodical_records.ensure_schema(db)
        publication = db.execute(
            "SELECT * FROM periodical_publications WHERE id = ?", (publication_id,)
        ).fetchone()
        if not publication:
            return RedirectResponse("/periodicals", status_code=303)
        issues = periodical_records.issues_for_publication(db, publication_id)
    return request.app.state.templates.TemplateResponse(
        request,
        "periodical_publication.html",
        {"publication": publication, "issues": issues},
    )
