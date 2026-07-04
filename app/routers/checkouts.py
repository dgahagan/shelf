from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.auth import require_role
from app.database import get_db, get_setting

router = APIRouter(prefix="/api")

DEFAULT_OVERDUE_DAYS = 28

# A loan is overdue when its explicit due date has passed, or — for loans
# with no due date (e.g. created via the Lend scan mode) — when it has been
# out longer than the configured fallback window. One ? param: fallback days.
OVERDUE_CONDITION = (
    "c.checked_in IS NULL AND ("
    "  (c.due_date IS NOT NULL AND c.due_date < date('now'))"
    "  OR (c.due_date IS NULL AND julianday('now') - julianday(c.checked_out) > ?)"
    ")"
)


def get_overdue_days(db) -> int:
    """Fallback overdue window in days; 0 disables the no-due-date fallback."""
    raw = get_setting(db, "lending_overdue_days")
    try:
        days = int(raw) if raw else DEFAULT_OVERDUE_DAYS
    except ValueError:
        days = DEFAULT_OVERDUE_DAYS
    # 0 = disabled: make the fallback unreachable rather than branching SQL
    return days if days > 0 else 10**9


def get_overdue_loans(db) -> list[dict]:
    """All overdue checkouts with item and borrower context."""
    rows = db.execute(
        "SELECT c.*, i.title, i.cover_path, b.name as borrower_name, "
        "CAST(julianday('now') - julianday(c.checked_out) AS INTEGER) as days_out "
        "FROM checkouts c "
        "JOIN items i ON c.item_id = i.id "
        "JOIN borrowers b ON c.borrower_id = b.id "
        f"WHERE {OVERDUE_CONDITION} "
        "ORDER BY c.checked_out ASC",
        (get_overdue_days(db),),
    ).fetchall()
    return [dict(row) for row in rows]


# --- Borrowers ---

@router.post("/borrowers")
async def create_borrower(name: str = Form(...), _=Depends(require_role("admin"))):
    with get_db() as db:
        db.execute("INSERT OR IGNORE INTO borrowers (name) VALUES (?)", (name.strip(),))
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/borrowers/{borrower_id}/delete")
async def delete_borrower(borrower_id: int, _=Depends(require_role("admin"))):
    with get_db() as db:
        active = db.execute(
            "SELECT COUNT(*) as c FROM checkouts WHERE borrower_id = ? AND checked_in IS NULL",
            (borrower_id,),
        ).fetchone()["c"]
        if active > 0:
            return {"ok": False, "message": "Borrower has active checkouts"}
        db.execute("DELETE FROM borrowers WHERE id = ?", (borrower_id,))
    return RedirectResponse(url="/settings", status_code=303)


# --- Checkouts ---

@router.post("/items/{item_id}/checkout")
async def checkout_item(
    request: Request,
    item_id: int,
    borrower_id: int = Form(...),
    due_days: int = Form(14),
    notes: str = Form(""),
    _=Depends(require_role("editor")),
):
    """Check out an item to a borrower."""
    templates = request.app.state.templates
    due = (date.today() + timedelta(days=due_days)).isoformat() if due_days > 0 else None

    with get_db() as db:
        # Check not already checked out
        active = db.execute(
            "SELECT id FROM checkouts WHERE item_id = ? AND checked_in IS NULL", (item_id,)
        ).fetchone()
        if active:
            return {"ok": False, "message": "Already checked out"}

        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, due_date, notes) VALUES (?, ?, ?, ?)",
            (item_id, borrower_id, due, notes.strip() or None),
        )

    return RedirectResponse(url=f"/item/{item_id}", status_code=303)


@router.post("/checkouts/{checkout_id}/checkin")
async def checkin_item(checkout_id: int, _=Depends(require_role("editor"))):
    """Check in an item (return it)."""
    with get_db() as db:
        checkout = db.execute("SELECT item_id FROM checkouts WHERE id = ?", (checkout_id,)).fetchone()
        if not checkout:
            return {"ok": False, "message": "Checkout not found"}
        db.execute(
            "UPDATE checkouts SET checked_in = datetime('now') WHERE id = ?", (checkout_id,)
        )
    return RedirectResponse(url=f"/item/{checkout['item_id']}", status_code=303)


@router.get("/checkouts/overdue")
async def overdue_items(request: Request, _=Depends(require_role("viewer"))):
    """List all overdue checkouts (explicit due date or fallback window)."""
    with get_db() as db:
        return get_overdue_loans(db)
