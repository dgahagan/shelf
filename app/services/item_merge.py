"""Re-point a merged row's child records before the row is deleted (#86).

``items`` has five child tables declared ``ON DELETE CASCADE``. Deleting the
merged row therefore destroys its loan history, tags, cross-format links and
physical copies unless each one is re-pointed at the kept row first. Only
``scan_log`` and ``reading_log`` ever were, so a merge looked like it worked
and silently took the rest with it.

``item_copies`` became a casualty after the issue was written: first-class
physical copies shipped in 0.36.0, and that table carries acquisition price,
provenance, condition and copy barcode — the least reconstructible data in
Shelf.

Re-pointing is not a plain UPDATE for most of them. Each child table has its
own conflict rule, and getting one wrong raises ``IntegrityError`` mid-merge:

- ``item_tags`` is keyed on (item_id, tag_id), so a tag both rows carry
  collides.
- ``item_links`` is unique on (item_a_id, item_b_id), needs both columns
  re-pointed, and collapses into a self-link when the two rows were linked to
  each other.
- ``item_copies`` is unique on (item_id, copy_number) and has a partial unique
  index allowing one primary copy per item, so copies must be renumbered and
  at most one primary may survive.
- ``checkouts`` has no uniqueness constraint and is the only plain UPDATE.
- ``list_items`` is keyed on (list_id, item_id), so a list both rows are on
  collides — and it carries one rule the others do not, because ownership
  and wanting are mutually exclusive. ``app.services.lists.reparent`` holds
  both; the move lives there because that module is the one write path for
  the table.
"""

from app.services import item_copies, lists

# The fields a merge fills on the kept row from the merged-away one when the
# kept row has none. The merge route applies it; the Duplicates page reads it too.
MERGE_FILLABLE = frozenset(["subtitle", "authors", "publisher", "publish_year", "page_count",
                            "description", "series_name", "narrator", "isbn"])


def active_loan_ids(db, item_ids) -> set[int]:
    """Which of these items are currently checked out.

    Merging two rows that are both on loan would leave the kept row with two
    open checkouts, which no surface in Shelf can represent — ``pages.py``
    resolves ``lent_to`` with a ``LIMIT 1`` subquery, so the second loan would
    exist in the database and nowhere in the UI.
    """
    ids = list(item_ids)
    if not ids:
        return set()
    marks = ",".join("?" for _ in ids)
    rows = db.execute(
        f"SELECT DISTINCT item_id FROM checkouts "
        f"WHERE checked_in IS NULL AND item_id IN ({marks})",
        ids,
    ).fetchall()
    return {row["item_id"] for row in rows}


def _reparent_tags(db, keep_id: int, other_id: int) -> None:
    # OR IGNORE skips a tag the kept row already carries; the leftover row
    # stays on other_id and is cleared explicitly rather than left to the
    # cascade, so this function's effect does not depend on the DELETE.
    db.execute(
        "UPDATE OR IGNORE item_tags SET item_id = ? WHERE item_id = ?",
        (keep_id, other_id),
    )
    db.execute("DELETE FROM item_tags WHERE item_id = ?", (other_id,))


def _reparent_links(db, keep_id: int, other_id: int) -> None:
    # A link between the two rows describes a relationship that stops existing
    # the moment they are one row. Drop it first, in both directions, or the
    # re-point below turns it into a self-link.
    db.execute(
        "DELETE FROM item_links WHERE (item_a_id = ? AND item_b_id = ?) "
        "OR (item_a_id = ? AND item_b_id = ?)",
        (keep_id, other_id, other_id, keep_id),
    )
    db.execute(
        "UPDATE OR IGNORE item_links SET item_a_id = ? WHERE item_a_id = ?",
        (keep_id, other_id),
    )
    db.execute(
        "UPDATE OR IGNORE item_links SET item_b_id = ? WHERE item_b_id = ?",
        (keep_id, other_id),
    )
    db.execute(
        "DELETE FROM item_links WHERE item_a_id = ? OR item_b_id = ?",
        (other_id, other_id),
    )


def _reparent_copies(db, keep_id: int, other_id: int) -> None:
    keep_has_primary = db.execute(
        "SELECT 1 FROM copies_live WHERE item_id = ? AND is_primary = 1", (keep_id,)
    ).fetchone() is not None
    highest = db.execute(
        "SELECT COALESCE(MAX(copy_number), 0) AS n FROM item_copies WHERE item_id = ?",
        (keep_id,),
    ).fetchone()["n"]
    # Physical, deliberately, and NOT the predict-a-UNIQUE-violation class
    # most of this module's exemptions are: reading copies_live here would
    # make a trashed copy of the losing item invisible, so it would stay
    # parented to `other_id` and be destroyed by the caller's cascading
    # DELETE two lines later — a restorable row lost for good. `deleted_at`
    # and `is_primary` are carried through untouched below; this read only
    # changes which rows are found, not what is done with them.
    rows = db.execute(
        "SELECT id FROM item_copies WHERE item_id = ? ORDER BY copy_number, id",
        (other_id,),
    ).fetchall()

    # Numbering continues above the kept row's highest, so no copy_number
    # collides and the kept row's own copies keep the numbers the user knows.
    # The merged row has at most one primary (its own partial unique index
    # guarantees that), so demoting every moved copy when the kept row already
    # has one leaves exactly one primary either way. A trashed copy always
    # carries is_primary = 0 by trash_copy's own demote contract (it demotes
    # in the same statement that stamps deleted_at), so moving one along with
    # the rest can never mint a second primary.
    for offset, row in enumerate(rows, start=1):
        fields = {"item_id": keep_id, "copy_number": highest + offset}
        if keep_has_primary:
            fields["is_primary"] = 0
        item_copies.update_copy(db, row["id"], fields)


def reparent_children(db, keep_id: int, other_id: int) -> None:
    """Move every child record of ``other_id`` onto ``keep_id``.

    Must run before the caller deletes the ``other_id`` row. Safe to call
    when the merged row has no children.

    **What is deliberately not moved, and why.** ``romm_records``,
    ``komga_records``, ``periodical_issues`` and the ``music_*`` tables each
    key a single item by design — a row there describes *this* item's
    external record or its track list, not a fact about the work that should
    survive onto another row. They are left to the cascade. So is
    ``item_authors``: it is an index derived from each row's own ``authors``
    string, and the kept row's fill re-derives it through ``item_write``.
    ``price_history`` is left to the cascade as well: prices are re-fetched
    for the kept record's identifier, and the merged-away record's series
    belongs to another barcode. ``duplicate_dismissals`` is left to the
    cascade too: a dismissal names a pair of records, and the merged-away
    record's rows go with it (ON DELETE CASCADE).
    Everything whose loss the user would notice, and could not reconstruct, is
    moved here. That includes ``audit_entries``: an audit report keeps the
    merged-away record's *found* and *moved here* rows. It also includes a
    trashed copy, which is restorable, so losing it is exactly what this
    rule forbids; ``_reparent_copies`` selects from the physical table for
    that reason.
    """
    db.execute("UPDATE scan_log SET item_id = ? WHERE item_id = ?", (keep_id, other_id))
    db.execute("UPDATE audit_entries SET item_id = ? WHERE item_id = ?", (keep_id, other_id))
    db.execute("UPDATE reading_log SET item_id = ? WHERE item_id = ?", (keep_id, other_id))
    db.execute("UPDATE checkouts SET item_id = ? WHERE item_id = ?", (keep_id, other_id))
    _reparent_tags(db, keep_id, other_id)
    _reparent_links(db, keep_id, other_id)
    _reparent_copies(db, keep_id, other_id)
    lists.reparent(db, keep_id, other_id)
