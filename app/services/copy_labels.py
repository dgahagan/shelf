"""Copy labels — generated barcodes for physical copies, and the sheets they print on.

A generated code reads `PREFIX-NNNNNN`: the prefix is one setting (default
`SHF`, 1–8 letters or digits, stored upper-cased), the number is one past the
highest number any copy has ever held under that prefix. Hand-typed copy
barcodes stay free text; this module never rewrites one, and the next-code
rule ignores them.

**The prefix may log.** An invalid stored value (only a raw write can put one
there — the Settings route validates) falls back to the default with a
warning. `SQLiteHandler` writes `log_entries` on its own connection, so a
warning emitted while the caller holds `BEGIN IMMEDIATE` waits out the 5 s
busy timeout and is dropped (G3). Every caller therefore reads `prefix(db)`
**before** taking the write lock and passes the value in; `assign_codes` and
`next_number` take it as an argument and never log.

Scopes (which copies a bulk Assign or a sheet covers) are the three the design
names: `all`, `no_identifier` (copies of items with no ISBN and no UPC), and
`location` (that node only, not its descendants).
"""

import logging
import re
from dataclasses import dataclass

from app.database import get_setting
from app.services.item_copies import update_copy

logger = logging.getLogger(__name__)

PREFIX_KEY = "copy_barcode_prefix"
DEFAULT_PREFIX = "SHF"
PREFIX_RE = re.compile(r"^[A-Z0-9]{1,8}$")
SCOPES = ("all", "no_identifier", "location")
#: One sheet render holds at most this many labels — ten US Letter sheets.
SHEET_CAP = 300


def prefix(db) -> str:
    """The prefix generated codes use: the setting, or `DEFAULT_PREFIX`.

    Logs a warning on each call that finds an invalid stored value — so call
    it before any `BEGIN IMMEDIATE` (module docstring, G3).
    """
    stored = (get_setting(db, PREFIX_KEY) or "").strip()
    if not stored:
        return DEFAULT_PREFIX
    if not PREFIX_RE.match(stored):
        logger.warning(
            "Copy label prefix %r is not 1-8 letters or digits; using %s",
            stored, DEFAULT_PREFIX,
        )
        return DEFAULT_PREFIX
    return stored


def normalise_prefix(raw: str) -> str | None:
    """Validate a prefix typed into Settings; upper-cased, or `None` if invalid."""
    value = (raw or "").strip().upper()
    return value if PREFIX_RE.match(value) else None


def format_code(code_prefix: str, n: int) -> str:
    return f"{code_prefix}-{n:06d}"


def parse_code(code_prefix: str, code: str | None) -> int | None:
    """The number in a generated code under `code_prefix`, else `None`.

    Tolerant of numbers wider than six digits, so the millionth copy still
    parses.
    """
    m = re.fullmatch(rf"{re.escape(code_prefix)}-(\d+)", code or "")
    return int(m.group(1)) if m else None


def next_number(db, code_prefix: str) -> int:
    """One past the highest number any copy holds under `code_prefix`.

    Called only inside the caller's `BEGIN IMMEDIATE`, so two assigners cannot
    both read the same maximum.
    """
    # The physical copies table, not the live view: a trashed copy still
    # holds its barcode's UNIQUE slot, so its number is taken (G107).
    rows = db.execute(
        "SELECT copy_barcode FROM item_copies WHERE copy_barcode LIKE ?",
        (f"{code_prefix}-%",),
    ).fetchall()
    numbers = [parse_code(code_prefix, r["copy_barcode"]) for r in rows]
    return max((n for n in numbers if n is not None), default=0) + 1


def _scope_clause(scope: str, location_id: int | None) -> tuple[str, list]:
    if scope == "all":
        return "", []
    if scope == "no_identifier":
        return (" AND (i.isbn IS NULL OR i.isbn = '') AND (i.upc IS NULL OR i.upc = '')", [])
    if scope == "location":
        return " AND c.location_id = ?", [location_id]
    raise ValueError(f"unknown scope {scope!r}")


def scope_copies(db, scope: str, location_id: int | None = None) -> list:
    """Live copies in `scope` that have no barcode yet — what Assign would label."""
    where, params = _scope_clause(scope, location_id)
    return db.execute(
        "SELECT c.id, c.item_id, c.copy_number FROM copies_live c "
        "JOIN items_live i ON i.id = c.item_id "
        f"WHERE c.copy_barcode IS NULL{where} ORDER BY i.title COLLATE NOCASE, c.copy_number",
        params,
    ).fetchall()


def count_scope(db, scope: str, location_id: int | None = None) -> int:
    return len(scope_copies(db, scope, location_id))


def assign_codes(db, code_prefix: str, scope: str,
                 location_id: int | None = None) -> tuple[int, str | None, str | None]:
    """Give every barcode-less copy in `scope` the next generated code.

    `code_prefix` is the caller's `prefix(db)`, read before this call. Opens
    `BEGIN IMMEDIATE` as its first statement and re-reads the scope under the
    lock (G18); never logs while it holds it. Never overwrites a code — the
    scope excludes copies that have one. A failure on any row raises, and the
    caller's `with get_db()` rolls the whole batch back.

    Returns `(count, first_code, last_code)`; the codes are `None` for zero.
    """
    db.execute("BEGIN IMMEDIATE")
    rows = scope_copies(db, scope, location_id)
    if not rows:
        return 0, None, None
    n = next_number(db, code_prefix)
    first = format_code(code_prefix, n)
    for row in rows:
        update_copy(db, row["id"], {"copy_barcode": format_code(code_prefix, n)})
        n += 1
    return len(rows), first, format_code(code_prefix, n - 1)


def sheet_copies(db, *, code_prefix: str, scope: str | None = None,
                 location_id: int | None = None, item_id: int | None = None,
                 code_from: str | None = None, code_to: str | None = None,
                 offset: int = 0, limit: int = SHEET_CAP) -> list[dict]:
    """Live copies **with** a barcode, chosen by one selector, in print order.

    Selectors: `item_id` (one item's copies), a code range `code_from`..
    `code_to` (generated codes under `code_prefix`, compared by number, so a
    hand-typed code never falls inside a range), or a `scope`. Ordered by
    barcode — by number for a range. Each row carries `copy_barcode`, `title`,
    `copy_number` and `item_id`.
    """
    base = (
        "SELECT c.copy_barcode, c.copy_number, c.item_id, i.title FROM copies_live c "
        "JOIN items_live i ON i.id = c.item_id WHERE c.copy_barcode IS NOT NULL"
    )
    if item_id is not None:
        rows = db.execute(
            base + " AND c.item_id = ? ORDER BY c.copy_number LIMIT ? OFFSET ?",
            (item_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]
    if code_from is not None or code_to is not None:
        lo = parse_code(code_prefix, code_from) if code_from else 1
        hi = parse_code(code_prefix, code_to) if code_to else None
        if lo is None or (code_to and hi is None):
            return []
        rows = db.execute(base + " AND c.copy_barcode LIKE ?", (f"{code_prefix}-%",)).fetchall()
        numbered = [
            (parse_code(code_prefix, r["copy_barcode"]), dict(r)) for r in rows
        ]
        picked = sorted(
            ((n, r) for n, r in numbered
             if n is not None and n >= lo and (hi is None or n <= hi)),
            key=lambda pair: pair[0],
        )
        return [r for _n, r in picked[offset:offset + limit]]
    where, params = _scope_clause(scope or "all", location_id)
    rows = db.execute(
        base + where + " ORDER BY c.copy_barcode LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return [dict(r) for r in rows]


@dataclass(frozen=True)
class Preset:
    """One label stock: page, grid and label geometry, all in millimetres."""

    key: str
    label: str
    page: str          # CSS @page size keyword
    cols: int
    rows: int
    label_w: float
    label_h: float
    margin_top: float
    margin_left: float
    pitch_x: float     # left edge to next label's left edge
    pitch_y: float

    @property
    def per_sheet(self) -> int:
        return self.cols * self.rows

    def module_mm(self, symbol_modules: int) -> float:
        """Bar module width that fits `symbol_modules` plus the Code 128 quiet
        zones (10 modules each side) inside the label, less 1 mm padding each
        side; floored at 0.19 mm, below which phone cameras stop decoding."""
        return max(MIN_MODULE_MM, (self.label_w - 2 * LABEL_PADDING_MM) / (symbol_modules + 20))

    def fits(self, symbol_modules: int) -> bool:
        """True when the symbol fits the label at or above the minimum module.

        Below it the floored module makes the symbol wider than the label, the
        sheet's CSS squeezes it to fit, and it stops decoding — so a code that
        does not fit is printed as text only (a long hand-typed code; every
        generated code fits both presets).
        """
        return (self.label_w - 2 * LABEL_PADDING_MM) / (symbol_modules + 20) >= MIN_MODULE_MM


MIN_MODULE_MM = 0.19


LABEL_PADDING_MM = 1.0
SYMBOL_HEIGHT_MM = 10.0
TITLE_MAX = 40

#: Published dimensions: Avery 5160 (US Letter, 30 per sheet) and Avery L7160
#: (A4, 21 per sheet).
PRESETS: dict[str, Preset] = {
    "avery5160": Preset(
        key="avery5160", label="Avery 5160 — US Letter, 30 per sheet", page="letter",
        cols=3, rows=10, label_w=66.675, label_h=25.4,
        margin_top=12.7, margin_left=4.7625, pitch_x=69.85, pitch_y=25.4,
    ),
    "averyl7160": Preset(
        key="averyl7160", label="Avery L7160 — A4, 21 per sheet", page="A4",
        cols=3, rows=7, label_w=63.5, label_h=38.1,
        margin_top=15.15, margin_left=7.25, pitch_x=66.04, pitch_y=38.1,
    ),
}
DEFAULT_PRESET = "avery5160"
