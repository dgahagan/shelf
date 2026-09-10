"""What "start from this item" means — the copyable-field subset.

One declaration with two consumers: the copy-template endpoint behind the
manual form's in-form picker (`app/routers/items.py`, issue #19), and the
`?from={id}` prefill on the Scan page's manual entry panel
(`app/routers/pages.py`, issue #120). Declared here rather than in either
router so the two cannot drift, and in `services/` rather than in
`routers/items_common.py` because that module is at its size cap and the
cap's own instruction is to move domain logic out here (G67).

Not to be confused with `app/services/item_copies.py`, which is about
*physical* copies of an item — rows a user owns more than one of. This module
is about copying an item's *fields* onto a new one.
"""

# Deliberately excludes title, isbn/upc, cover, reading status, value and
# notes: those are identity and state, not template.
#
# Keep this set in sync with
# .devdocs/archive/completed/plan-issues-15-18-19-quick-wins.md section B.
COPYABLE_FIELDS = (
    "authors",
    "publisher",
    "publish_year",
    "media_type",
    "platform",
    "series_name",
    "location_id",
)


def copyable_fields(db, item_id):
    """The copyable subset of `item_id` as a dict, or None if no such row.

    The column list is interpolated from COPYABLE_FIELDS, which is a module
    constant of literals — no caller-supplied value reaches the SQL.
    """
    row = db.execute(
        "SELECT " + ", ".join(COPYABLE_FIELDS) + " FROM items WHERE id = ?",
        (item_id,),
    ).fetchone()
    if not row:
        return None
    return {name: row[name] for name in COPYABLE_FIELDS}
