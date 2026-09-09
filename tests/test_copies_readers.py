"""Issue #116's acceptance pin: one merge, then every reader.

Merging two owned items filed in different locations leaves the kept item
with copies in two rooms — `_reparent_copies` does that correctly, and always
did. What was wrong is that `items.location_id` still named only the first,
and four surfaces read only that field. This is the whole defect and the
whole fix in one test, and it is the prose form of the probe at
`.devdocs/backlog/plan-item-copies-surface-probes/merge_divergence.py`,
which measured this on `main` at 88875e9:

    COPIES: [(1, 1, 'Office'), (2, 0, 'Loft')]
    items.location_id -> Office
    LOFT AUDIT SAYS DUNE MISSING: False     <- the copy at Loft is invisible
    item page shows Office: True | shows Loft: False
"""
import json
import zipfile

from app.services.archive import build_archive, merge_archive, read_archive
from app.services.item_write import update_item_fields
from tests.conftest import _insert_item, _insert_location
from tests.test_archive import _wipe_library

KEEP_ISBN = "9780441013593"


def test_a_merged_item_is_visible_in_both_rooms_to_every_reader(admin_client, db):
    office = _insert_location(db, "Office")
    loft = _insert_location(db, "Loft")
    keep = _insert_item(db, title="Dune", isbn=KEEP_ISBN, owned=1)
    other = _insert_item(db, title="Dune (dup)", isbn="9780553293357", owned=1)
    update_item_fields(db, keep, {"location_id": office})
    update_item_fields(db, other, {"location_id": loft})
    db.commit()

    assert admin_client.post(
        "/api/items/merge", json={"keep_id": keep, "merge_ids": [other]}
    ).status_code == 200

    # The half that already worked on `main`: the merge itself.
    assert [tuple(r) for r in db.execute(
        "SELECT c.copy_number, c.is_primary, l.name FROM item_copies c "
        "LEFT JOIN locations l ON l.id = c.location_id "
        "WHERE c.item_id = ? ORDER BY c.copy_number", (keep,),
    ).fetchall()] == [(1, 1, "Office"), (2, 0, "Loft")]
    assert db.execute(
        "SELECT location_id FROM items WHERE id = ?", (keep,)
    ).fetchone()["location_id"] == office

    # T4 — the item page names both rooms, not just the seam's.
    page = admin_client.get(f"/item/{keep}").text
    assert "Office" in page and "Loft" in page
    assert ">Copies<" in page

    # T5 — the audit of the SECOND room expects the item there.
    assert b"Dune" in admin_client.post("/api/inventory/missing", data={
        "location_id": str(loft), "scanned_ids": "",
    }).content

    # T6 — a scan at a THIRD location writes nothing and names both rooms.
    hall = _insert_location(db, "Hall")
    db.commit()
    before = [tuple(r) for r in db.execute(
        "SELECT copy_number, location_id FROM item_copies WHERE item_id = ? "
        "ORDER BY copy_number", (keep,),
    ).fetchall()]

    scan = admin_client.post("/api/scan", data={
        "isbn": KEEP_ISBN, "mode": "inventory", "location_id": str(hall),
    })
    assert b"elsewhere" in scan.content
    assert b"Copies at Office and Loft; none here." in scan.content

    assert [tuple(r) for r in db.execute(
        "SELECT copy_number, location_id FROM item_copies WHERE item_id = ? "
        "ORDER BY copy_number", (keep,),
    ).fetchall()] == before
    assert db.execute(
        "SELECT location_id FROM items WHERE id = ?", (keep,)
    ).fetchone()["location_id"] == office

    # T7/T8 — an archive round trip preserves both copies.
    db.commit()
    path = build_archive(db)
    with zipfile.ZipFile(path) as zf:
        exported = json.loads(zf.read("library.json"))
    assert [
        (c["copy_number"], c["location"])
        for item in exported["items"] if item["title"] == "Dune"
        for c in item["copies"]
    ] == [(1, "Office"), (2, "Loft")]

    # `scan_log` first: it is out of the archive format's scope, so
    # `_wipe_library` does not clear it — and the archive suite never needs
    # to, because those tests make no scan. This one does, and the rows it
    # left reference `items(id)`.
    db.execute("DELETE FROM scan_log")
    # `_wipe_library` is the archive suite's own fresh-instance simulation,
    # reused rather than re-derived so this pin cannot drift from what the
    # round-trip tests mean by "a fresh instance". `item_copies` is not in
    # its list because ON DELETE CASCADE from `items` takes them.
    _wipe_library(db)

    with read_archive(path) as reader:
        report = merge_archive(db, reader, mode="skip")
    db.commit()
    assert report["errors"] == []

    assert [tuple(r) for r in db.execute(
        "SELECT c.copy_number, c.is_primary, l.name FROM item_copies c "
        "JOIN items i ON i.id = c.item_id "
        "LEFT JOIN locations l ON l.id = c.location_id "
        "WHERE i.title = 'Dune' ORDER BY c.copy_number"
    ).fetchall()] == [(1, 1, "Office"), (2, 0, "Loft")]
