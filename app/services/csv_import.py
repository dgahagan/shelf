"""The generic CSV import's row mapping.

Split out of `app/routers/items_csv.py` to keep that route under its line cap
in `tests/test_module_sizes.py`. The route imports this module and calls
through it (`csv_import.resolve_state(...)`) so tests can patch.
"""

from app.services import locations
from app.services.item_write import update_item_fields, validate_item_fields

# LibraryThing columns that land on a new item's copy (normalize_librarything's
# `copy`), and are reported as `existing_item` on any other row.
LT_ACQUISITION_COLUMNS = (
    "acquired", "date_acquired", "from_where", "purchase_price", "condition",
)


def resolve_state(norm: dict, to_read_wishlist: bool,
                  tracker: bool) -> tuple[bool, bool, bool, bool]:
    """Resolve a row's (owned, wishlisted) once, and whether it states each.

    `owned` comes from the normaliser untouched. A generic row with no
    `owned` value is one of two things: a 0.41.1-era export, where
    `wishlisted=1` meant not owned, or any other file, where the historical
    default is owned (applied on insert; an update leaves the row alone).

    `wishlisted`, in order: an owned row is never on the wishlist (the funnel
    would refuse the pair, and an import must not fail a row that only says
    "I have it" — G96); else the file's own `wishlisted` column; else the
    to-read option. A present `wishlisted=0` is honoured, not replaced by the
    option (G87).
    """
    owned = norm["owned"]
    column_wish = norm["wishlisted"]
    if owned is None and column_wish:
        owned = False
    owned_given = owned is not None
    if owned is None:
        owned = True

    if owned:
        wishlisted = False
    elif column_wish is not None:
        wishlisted = column_wish
    else:
        wishlisted = to_read_wishlist and norm["reading_status"] == "want_to_read"

    # A reading-tracker row states both flags. A generic row states
    # `wishlisted` only through its column, or by being owned (rule 1).
    wishlisted_given = tracker or column_wish is not None or (owned_given and owned)
    return owned, wishlisted, owned_given, wishlisted_given


# Audiobook fields an import writes on insert, and in update mode only into
# an empty column (see update_from_row).
FILL_EMPTY = ("subtitle", "narrator", "duration_mins", "description", "asin")


def match_audiobook(db, asin: str, title: str, authors: str | None):
    """Find the audiobook an Audible export row describes: `(hit, rung)`.

    The ASIN rung first, then title and authors against audiobooks that hold
    no ASIN yet (an Audiobookshelf row often has an ISBN and, until its next
    sync, no ASIN). `rung` is "asin" or "title"; `(None, None)` on a miss.

    Both reads use the physical table and order a live row first: the ASIN
    rung must find a trashed twin so the route restores it (G107, "find the
    row the view hides"), and the title rung follows the existing title
    rung's "live wins" rule. Neither has a UNIQUE constraint behind it.
    """
    hit = db.execute(
        "SELECT id, deleted_at FROM items WHERE asin = ? AND media_type = 'audiobook' "
        "ORDER BY deleted_at IS NOT NULL, id LIMIT 1",
        (asin,),
    ).fetchone()
    if hit:
        return hit, "asin"
    hit = db.execute(
        "SELECT id, deleted_at FROM items WHERE "
        "TRIM(title) = TRIM(?) COLLATE NOCASE AND "
        "TRIM(COALESCE(authors, '')) = TRIM(?) COLLATE NOCASE AND "
        "media_type = 'audiobook' AND asin IS NULL "
        "ORDER BY deleted_at IS NOT NULL, id LIMIT 1",
        (title, authors or ""),
    ).fetchone()
    return (hit, "title") if hit else (None, None)


def update_from_row(db, item_id: int, row: dict, *, generic: bool = False,
                    location_id: int | None = None):
    """Update an existing item from CSV row data (non-empty fields only).

    Generic rows also carry the parsed round-trip fields (#145): each applies
    only when the normalizer produced a value, so an empty cell is no opinion.
    `estimated_value` is never applied here — the valuation route owns it.
    The `FILL_EMPTY` audiobook fields apply to every format, and only into a
    column the existing row leaves empty.
    `location_id` reaches `sync_primary_location` through the seam, which
    moves the primary copy (or creates the first one) and leaves the others;
    nothing here removes a copy first, so G96's ordering does not arise.
    """
    field_map = {
        "authors": "authors", "author": "authors",
        "publisher": "publisher",
        "publish_year": "publish_year", "year": "publish_year",
        "page_count": "page_count", "pages": "page_count",
        "series_name": "series_name", "series": "series_name",
    }
    updates = {}
    for csv_key, db_key in field_map.items():
        val = (row.get(csv_key) or "").strip()
        if val and db_key not in updates:
            if db_key in ("publish_year", "page_count"):
                updates[db_key] = int(val) if val.isdigit() else None
            else:
                updates[db_key] = val
    # Fill-only fields (any format): written only where the row holds none,
    # because an Audiobookshelf-synced row's values come from the user's own
    # files and an import must not overwrite them.
    fill = {k: row[k] for k in FILL_EMPTY if row.get(k) not in (None, "")}
    if fill:
        current = db.execute(
            f"SELECT {', '.join(fill)} FROM items_live WHERE id = ?", (item_id,)
        ).fetchone()
        for key, val in fill.items():
            if current is not None and current[key] in (None, ""):
                updates.setdefault(key, val)
    if generic:
        # Already parsed by normalize_generic — never through .strip() above.
        for key in ("language", "series_position", "manual_value", "platform"):
            if row.get(key) is not None:
                updates[key] = row[key]
        if location_id is not None:
            updates["location_id"] = location_id
    if updates:
        update_item_fields(db, item_id, updates)


def precheck_row(db, norm: dict, isbn_val: str | None, media: str) -> None:
    """Raise now what the funnel would raise later, before any write (G85).

    Runs only when the row carries a location or a platform: the location is
    created before the insert, and the update path applies the platform
    after `restore_item`, so either would otherwise be followed by a
    validation failure. Same exceptions and messages as the funnel.
    """
    fields = {}
    if norm.get("platform") is not None:
        fields["platform"] = norm["platform"]
    if norm.get("location"):
        fields["isbn"] = isbn_val
        fields["media_type"] = media
    if fields:
        validate_item_fields(db, fields)


class RowLocations:
    """One import's location lookups: check early, create late (G85).

    `check()` is read-only and runs with the row's other pre-checks;
    `ensure()` may write and is called only where the row is about to be
    written, so a skipped or failed row creates no location. Names are
    cached per request, and `created` lists each new name once, in order.
    """

    def __init__(self, db):
        self.db = db
        self._ids: dict[str, int] = {}
        self.created: list[str] = []

    def check(self, name: str) -> int | None:
        key = name.casefold()
        if key in self._ids:
            return self._ids[key]
        found = locations.find_by_path(self.db, name)
        if found is None and " / " in name:
            raise locations.LocationError(f'location "{name}" not found')
        if found is not None:
            self._ids[key] = found
        return found

    def ensure(self, name: str) -> int:
        key = name.casefold()
        if key not in self._ids:
            loc_id, created = locations.resolve_or_create(self.db, name)
            if created:
                self.created.append(name)
            self._ids[key] = loc_id
        return self._ids[key]
