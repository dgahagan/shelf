"""The Labels page: assign copy barcodes in bulk and print them on label sheets.

Two halves, one feature (`collector`):

- **Assign** gives every barcode-less copy in a scope the next generated code
  (`copy_labels.assign_codes`), in one write transaction whatever the count.
- **Print** renders a sheet of Code 128 labels for an item, a code range or a
  scope, laid out in millimetres for one of the Avery presets, at most
  `SHEET_CAP` labels per render with Previous/Next links for the rest.

Every route declares `require_role("editor")` **then** `require_feature`, the
order the registry lint checks. The prefix is read with `copy_labels.prefix`
**before** any write lock, because it may log (G3).
"""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request

from app.auth import require_role
from app.database import get_db
from app.features import require_feature
from app.routers import item_copies as copies_router
from app.services import code128, copy_labels, item_copies
from app.services import locations as location_svc

page_router = APIRouter()
router = APIRouter(prefix="/api/labels")
item_router = APIRouter(prefix="/api")


def _optional_int(raw: str, name: str, *, minimum: int = 0) -> int | None:
    """Parse an optional integer query/form value; 400 on anything else."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if not (raw.isascii() and raw.isdigit()) or int(raw) < minimum:
        raise HTTPException(status_code=400, detail=f"{name} must be a whole number")
    return int(raw)


def _scope_args(scope: str, location_id: str) -> tuple[str, int | None]:
    if scope not in copy_labels.SCOPES:
        raise HTTPException(status_code=400, detail="Unknown scope")
    loc = _optional_int(location_id, "location_id", minimum=1)
    return scope, loc


@page_router.get("/labels")
async def labels_page(
    request: Request,
    _=Depends(require_role("editor")),
    __=Depends(require_feature("collector")),
):
    with get_db() as db:
        locations = location_svc.location_tree(db)
        code_prefix = copy_labels.prefix(db)
    return request.app.state.templates.TemplateResponse(
        request, "labels.html",
        {"locations": locations, "code_prefix": code_prefix,
         "presets": copy_labels.PRESETS.values(),
         "default_preset": copy_labels.DEFAULT_PRESET},
    )


@router.get("/count")
async def labels_count(
    request: Request,
    scope: str = "all",
    location_id: str = "",
    _=Depends(require_role("editor")),
    __=Depends(require_feature("collector")),
):
    scope, loc = _scope_args(scope, location_id)
    needs_location = scope == "location" and loc is None
    count = 0
    if not needs_location:
        with get_db() as db:
            count = copy_labels.count_scope(db, scope, loc)
    return request.app.state.templates.TemplateResponse(
        request, "fragments/labels_count.html",
        {"count": count, "needs_location": needs_location},
    )


@router.post("/assign")
async def labels_assign(
    request: Request,
    scope: str = Form("all"),
    location_id: str = Form(""),
    _=Depends(require_role("editor")),
    __=Depends(require_feature("collector")),
):
    scope, loc = _scope_args(scope, location_id)
    if scope == "location" and loc is None:
        raise HTTPException(status_code=400, detail="Choose a location")
    with get_db() as db:
        # Before assign_codes' BEGIN IMMEDIATE: an invalid stored prefix logs,
        # and a log line under the write lock is five seconds and dropped (G3).
        code_prefix = copy_labels.prefix(db)
        count, first, last = copy_labels.assign_codes(db, code_prefix, scope, loc)
    # The count line above the button re-reads on this event; without it the
    # page says "N copies would get a code" beside "N codes assigned".
    return request.app.state.templates.TemplateResponse(
        request, "fragments/labels_assigned.html",
        {"count": count, "first": first, "last": last,
         "sheet_url": "/labels/sheet?" + urlencode({"from": first, "to": last}) if count else None},
        headers={"HX-Trigger": "labels-assigned"},
    )


def _label_cell(code: str, title: str, copy_number: int, preset: copy_labels.Preset) -> dict:
    """One printable label: the symbol as inline SVG, the code, a short title.

    A hand-typed code with a character Code 128 Set B cannot carry, or one too
    long to fit the label at a scannable module width, renders its text alone,
    marked, rather than failing the whole sheet or printing bars that do not
    decode.
    """
    short = title if len(title) <= copy_labels.TITLE_MAX else title[:copy_labels.TITLE_MAX - 1] + "…"
    symbol, note = None, None
    try:
        n = code128.modules(code)
    except ValueError:
        note = "No barcode for this code"
    else:
        if preset.fits(n):
            symbol = code128.svg(code, height_mm=copy_labels.SYMBOL_HEIGHT_MM,
                                 module_mm=preset.module_mm(n), with_text=False)
        else:
            note = "Code too long to print as a barcode"
    return {"code": code, "title": short, "copy_number": copy_number, "svg": symbol, "note": note}


@page_router.get("/labels/sheet")
async def labels_sheet(
    request: Request,
    scope: str = "",
    location_id: str = "",
    item_id: str = "",
    code_from: str = Query("", alias="from"),
    code_to: str = Query("", alias="to"),
    preset: str = "",
    start: str = "",
    offset: str = "",
    _=Depends(require_role("editor")),
    __=Depends(require_feature("collector")),
):
    stock = copy_labels.PRESETS.get(preset) or copy_labels.PRESETS[copy_labels.DEFAULT_PRESET]
    item = _optional_int(item_id, "item_id", minimum=1)
    first = _optional_int(start, "start") or 1
    skip = _optional_int(offset, "offset") or 0
    first = min(max(first, 1), stock.per_sheet)
    if scope:
        scope, loc = _scope_args(scope, location_id)
    else:
        loc = _optional_int(location_id, "location_id", minimum=1)

    # The active selector, carried unchanged into the Previous/Next links.
    if item is not None:
        selector = {"item_id": item}
    elif code_from or code_to:
        selector = {k: v for k, v in (("from", code_from), ("to", code_to)) if v}
    else:
        selector = {"scope": scope or "all"}
        if loc is not None:
            selector["location_id"] = loc

    with get_db() as db:
        code_prefix = copy_labels.prefix(db)
        rows = copy_labels.sheet_copies(
            db, code_prefix=code_prefix, scope=scope or "all", location_id=loc,
            item_id=item, code_from=code_from or None, code_to=code_to or None,
            offset=skip, limit=copy_labels.SHEET_CAP + 1,
        )
    has_next = len(rows) > copy_labels.SHEET_CAP
    rows = rows[:copy_labels.SHEET_CAP]

    # The start position applies to the first sheet of the first chunk only:
    # later chunks begin on a fresh sheet.
    blanks = first - 1 if skip == 0 else 0
    cells = [None] * blanks + [
        _label_cell(r["copy_barcode"], r["title"], r["copy_number"], stock) for r in rows
    ]
    sheets = [cells[i:i + stock.per_sheet] for i in range(0, len(cells), stock.per_sheet)] if rows else []

    def _page_url(new_offset: int) -> str:
        query = {**selector, "preset": stock.key, "start": first}
        if new_offset:
            query["offset"] = new_offset
        return "/labels/sheet?" + urlencode(query)

    return request.app.state.templates.TemplateResponse(
        request, "labels_sheet.html",
        {"stock": stock, "sheets": sheets, "count": len(rows), "offset": skip,
         "prev_url": _page_url(max(skip - copy_labels.SHEET_CAP, 0)) if skip > 0 else None,
         "next_url": _page_url(skip + copy_labels.SHEET_CAP) if has_next else None,
         "presets": copy_labels.PRESETS.values(), "selector": selector, "start": first,
         "padding": copy_labels.LABEL_PADDING_MM},
    )


@item_router.post("/items/{item_id}/copies/{copy_id}/assign-code")
async def assign_copy_code(
    request: Request,
    item_id: int,
    copy_id: int,
    _=Depends(require_role("editor")),
    __=Depends(require_feature("collector")),
):
    """Give one barcode-less copy the next generated code; re-render its panel.

    The button sits inside the panel's form, so HTMX posts the panel's fields
    with it. They are not saved here — only the code is — but they are carried
    back into the re-rendered panel so the user's unsaved typing survives.
    """
    form = await request.form()
    unsaved: dict = {k: form[k] for k in copies_router._EDITABLE if k in form and k != "copy_barcode"}
    if "location_id" in form:
        raw_loc = form["location_id"]
        unsaved["location_id"] = int(raw_loc) if raw_loc.isdigit() else None
    failure: tuple[str, int] | None = None
    with get_db() as db:
        p = copy_labels.prefix(db)  # may log, so before the lock (G3)
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT copy_barcode FROM copies_live WHERE id = ? AND item_id = ?",
            (copy_id, item_id),
        ).fetchone()
        if row is None:
            failure = ("Copy not found", 404)
        elif row["copy_barcode"]:
            failure = ("This copy already has a code", 409)
        else:
            n = copy_labels.next_number(db, p)
            item_copies.update_copy(db, copy_id, {"copy_barcode": copy_labels.format_code(p, n)})
        if failure:
            db.rollback()
        else:
            db.commit()
            return copies_router._render_edit_panel(request, db, item_id, copy_id, unsaved)
    return copies_router._refusal(*failure)
