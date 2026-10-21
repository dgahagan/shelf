"""The `copies_live` seam, pinned at its real readers — not the view itself.

`tests/test_soft_delete_seam.py` already pins the view's own mechanism: that
it exists on every connection, that it hides a copy whose own `deleted_at` is
set, that the join also hides every copy of a trashed item, and that writing
through it fails loudly. This file is the other half: it hand-trashes a copy
or an item (the Trash routes and the item page's Remove copy do the same
through the funnel) and then walks every real reader — the item page, Arrange, Shelf Fill, the
inventory audit, cover review, the by-id copy routes, `add_copy`'s numbering
split, merge's renumbering, barcode-conflict prediction and the archive
import — to check it agrees with the view.

Two hand-trashed scenarios, seeded once and shared by every test below:

    - **copy trashed**: item A's own secondary copy (`a2`) is marked deleted.
      Its item stays live. This is the ordinary case.
    - **item trashed**: item B itself is marked deleted; its copies' own
      `deleted_at` stay NULL. This is the join's whole justification — a
      copy that is not itself touched still has to disappear.

A third item, C, has a *single* copy that is trashed and hand-demoted to
`is_primary = 0` — the exact state a later plan produces when it starts
trashing copies for real. It pins `add_copy`'s primary/secondary split and
the G86 zero-copy fallback in the same breath.

G48 applies throughout: every seed and every hand `UPDATE ... deleted_at`
is committed before the first request against it, or an absence assertion
would pass on an empty database instead of on the view actually filtering.
"""
import pytest

from app.routers import cover_review, shelf_fill
from app.services import archive, item_copies, item_merge, location_order
from tests.conftest import _insert_item, _insert_location

_TRASHED_AT = "2026-09-18T00:00:00"


@pytest.fixture
def seed(db):
    """Five locations, five items, untrashed. See the module docstring."""
    loc_cedar = _insert_location(db, "Cedar Loft Annex")
    loc_garage = _insert_location(db, "Garage West Rack")
    loc_fern = _insert_location(db, "Fern Hollow Bay")
    loc_sunroom = _insert_location(db, "Sunroom Overflow Case")
    loc_boathouse = _insert_location(db, "Boathouse Crate Row")

    item_a = _insert_item(db, title="Pin Item A Two Copies", isbn="9780000000101", owned=1)
    # a2 is inserted BEFORE a1, so it holds the lower `id` — load-bearing for
    # the cover-review pin, whose query picks `ORDER BY c.id LIMIT 1`. Both
    # live, a2 (Garage) would win; once trashed, location_name must move to
    # a1 (Cedar), which is the only way that pin is non-vacuous.
    a2 = item_copies.insert_copy(db, {
        "item_id": item_a, "copy_number": 2, "location_id": loc_garage,
        "is_primary": 0, "copy_barcode": "PIN-A2-SECONDARY-7421",
    })
    a1 = item_copies.insert_copy(db, {
        "item_id": item_a, "copy_number": 1, "location_id": loc_cedar,
        "is_primary": 1,
    })

    item_b = _insert_item(db, title="Pin Item B Trashed Item", isbn="9780000000201", owned=1)
    b1 = item_copies.insert_copy(db, {
        "item_id": item_b, "copy_number": 1, "location_id": loc_fern,
        "is_primary": 1, "copy_barcode": "PIN-B1-PRIMARY-8532",
    })
    # A second copy at the same location, carrying a shelf position, so the
    # "three readers that never join items" pins have something non-trivial
    # to lose — a bare "goes from 1 to 0" pin is too easily vacuous.
    b2 = item_copies.insert_copy(db, {
        "item_id": item_b, "copy_number": 2, "location_id": loc_fern,
        "is_primary": 0, "position_order": 5,
    })

    item_c = _insert_item(
        db, title="Pin Item C Zero Copy Fallback", isbn="9780000000301",
        owned=1, location_id=loc_sunroom,
    )
    c1 = item_copies.insert_copy(db, {
        "item_id": item_c, "copy_number": 1, "location_id": loc_boathouse,
        "is_primary": 1,
    })

    item_e = _insert_item(db, title="Pin Item E Merge Source", isbn="9780000000401", owned=1)
    e1 = item_copies.insert_copy(db, {"item_id": item_e, "copy_number": 1, "is_primary": 1})

    # A live copy at Fern Hollow Bay belonging to neither A, B nor C — the
    # thing being positioned/ordered in the "three readers" pins, so those
    # pins prove a *partial* filter (one item's copies gone, another's kept)
    # rather than an accidentally-empty location.
    item_f = _insert_item(db, title="Pin Item F Bystander", isbn="9780000000501", owned=1)
    f1 = item_copies.insert_copy(db, {
        "item_id": item_f, "copy_number": 1, "location_id": loc_fern, "is_primary": 1,
    })

    db.commit()
    return {
        "loc_cedar": loc_cedar, "loc_garage": loc_garage, "loc_fern": loc_fern,
        "loc_sunroom": loc_sunroom, "loc_boathouse": loc_boathouse,
        "item_a": item_a, "a1": a1, "a2": a2,
        "item_b": item_b, "b1": b1, "b2": b2,
        "item_c": item_c, "c1": c1,
        "item_e": item_e, "e1": e1,
        "item_f": item_f, "f1": f1,
    }


@pytest.fixture
def trashed(db, seed):
    """Hand-trash the three scenarios and commit — G48."""
    db.execute("UPDATE item_copies SET deleted_at = ? WHERE id = ?", (_TRASHED_AT, seed["a2"]))
    db.execute("UPDATE items SET deleted_at = ? WHERE id = ?", (_TRASHED_AT, seed["item_b"]))
    db.execute(
        "UPDATE item_copies SET deleted_at = ?, is_primary = 0 WHERE id = ?",
        (_TRASHED_AT, seed["c1"]),
    )
    db.commit()
    return seed


# ---------------------------------------------------------------------------
# 1. Absence
# ---------------------------------------------------------------------------

def test_item_page_hides_trashed_copy_but_keeps_the_live_one(admin_client, trashed):
    page = admin_client.get(f"/item/{trashed['item_a']}").text
    assert f'data-testid="edit-copy-{trashed["a1"]}"' in page
    assert f'data-testid="edit-copy-{trashed["a2"]}"' not in page
    # "Garage West Rack" also names an <option> in every "add copy" location
    # picker on the page, so a bare substring check would be vacuous (G69) —
    # assert on the copy row's own browse link instead, which only renders
    # for a copy actually in `copies_for_item`'s result.
    assert f'location_filter={trashed["loc_cedar"]}"' in page
    assert f'location_filter={trashed["loc_garage"]}"' not in page


def test_arrange_page_hides_trashed_copy(admin_client, trashed):
    garage = admin_client.get(f"/locations/{trashed['loc_garage']}/arrange")
    assert f'data-copy-id="{trashed["a2"]}"' not in garage.text
    assert "No physical copies are stored directly at this location yet." in garage.text

    cedar = admin_client.get(f"/locations/{trashed['loc_cedar']}/arrange")
    assert f'data-copy-id="{trashed["a1"]}"' in cedar.text


def test_shelf_fill_summary_excludes_trashed_copy(admin_client, trashed):
    resp = admin_client.get(
        "/api/shelf-fill/summary", params={"location_id": trashed["loc_garage"]}
    )
    assert "Empty shelf" in resp.text


def _audit_missing(client, location_id):
    """The Missing section of a fresh audit of `location_id`, started and
    read through the routes (the strip's start, then the report page)."""
    import re

    start = client.post("/api/audits", data={"scope": "location",
                                              "location_id": str(location_id)})
    audit_id = re.search(r'data-audit-id="(\d+)"', start.text).group(1)
    html = client.get(f"/audits/{audit_id}").text
    return html.split('data-audit-section="missing"', 1)[1].split("</section>", 1)[0]


def test_audit_report_excludes_trashed_copy(admin_client, trashed):
    missing = _audit_missing(admin_client, trashed["loc_garage"])
    assert "Pin Item A Two Copies" not in missing
    assert ' data-audit-count="0"' in missing


def test_cover_review_location_name_follows_the_live_copy_not_the_trashed_one(db, trashed):
    row = db.execute(
        f"SELECT {cover_review._QUEUE_COLUMNS} FROM items_live i WHERE i.id = ?",
        (trashed["item_a"],),
    ).fetchone()
    assert row["location_name"] == "Cedar Loft Annex"


def test_shelf_fill_summary_excludes_a_trashed_items_copies(admin_client, trashed):
    """The join half: item B's two copies (b1, b2) vanish; item F's (f1)
    does not, so the total is 1 rather than 3."""
    resp = admin_client.get(
        "/api/shelf-fill/summary", params={"location_id": trashed["loc_fern"]}
    )
    assert '<strong class="text-shelf-text">1</strong> item' in resp.text
    assert '<strong class="text-shelf-text">3</strong> item' not in resp.text


# ---------------------------------------------------------------------------
# 2. 404s
# ---------------------------------------------------------------------------

def test_edit_panel_404s_for_trashed_copy(admin_client, trashed):
    resp = admin_client.get(f"/api/items/{trashed['item_a']}/copies/{trashed['a2']}/edit")
    assert resp.status_code == 404


def test_update_404s_for_trashed_copy(admin_client, trashed):
    resp = admin_client.post(
        f"/api/items/{trashed['item_a']}/copies/{trashed['a2']}",
        data={"condition": "Good"},
    )
    assert resp.status_code == 404


def test_remove_404s_for_trashed_copy(admin_client, trashed):
    resp = admin_client.delete(f"/api/items/{trashed['item_a']}/copies/{trashed['a2']}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. Numbering
# ---------------------------------------------------------------------------

def test_add_copy_numbers_past_a_trashed_secondary(db, trashed):
    """item A's copy 2 is trashed but still holds the number physically —
    the next copy is 3, and it is a secondary (a1 is still a live primary)."""
    new_id = item_copies.add_copy(db, trashed["item_a"], {})
    row = db.execute(
        "SELECT copy_number, is_primary FROM item_copies WHERE id = ?", (new_id,)
    ).fetchone()
    assert row["copy_number"] == 3
    assert row["is_primary"] == 0


def test_add_copy_on_an_item_whose_only_copy_is_trashed_and_demoted(db, trashed):
    """item C's only copy is trashed AND hand-demoted to secondary — the
    state a later plan produces. The next copy still numbers past the
    trashed physical row (2, not 1) but becomes primary again, because the
    view sees no live copies at all. Both halves of add_copy's split, one
    assertion."""
    new_id = item_copies.add_copy(db, trashed["item_c"], {})
    row = db.execute(
        "SELECT copy_number, is_primary FROM item_copies WHERE id = ?", (new_id,)
    ).fetchone()
    assert (row["copy_number"], row["is_primary"]) == (2, 1)


# ---------------------------------------------------------------------------
# 4. Barcode
# ---------------------------------------------------------------------------

def test_add_copy_refuses_a_trashed_copys_barcode(admin_client, trashed):
    resp = admin_client.post(
        f"/api/items/{trashed['item_a']}/copies",
        data={"copy_barcode": "PIN-A2-SECONDARY-7421"},
    )
    assert resp.status_code == 400
    assert "already used" in resp.text
    assert "Pin Item A Two Copies" in resp.text


def test_add_copy_refuses_a_trashed_items_copy_barcode(admin_client, trashed):
    """The pin that reds if `_barcode_conflict`'s join goes back to
    `items_live` — item B is trashed, but its copy still holds the barcode,
    and predicting the UNIQUE constraint has to see it."""
    resp = admin_client.post(
        f"/api/items/{trashed['item_a']}/copies",
        data={"copy_barcode": "PIN-B1-PRIMARY-8532"},
    )
    assert resp.status_code == 400
    assert "already used" in resp.text
    assert "Pin Item B Trashed Item" in resp.text


def test_archive_import_drops_and_reports_a_trashed_items_copy_barcode(db, trashed):
    target = _insert_item(db, title="Archive Import Target", isbn="9780000000601", owned=1)
    db.commit()
    errors: list[str] = []

    archive._import_copies(
        db, target,
        [{
            "copy_number": 1, "location": None, "is_primary": True,
            "position_order": None, "condition": None, "acquired_date": None,
            "acquisition_source": None, "acquisition_price": None,
            "provenance": None, "notes": None,
            "copy_barcode": "PIN-B1-PRIMARY-8532",
        }],
        get_location_id=lambda name: None,
        errors=errors,
        archive_id=999,
        title="Archive Import Target",
    )

    assert any("PIN-B1-PRIMARY-8532" in e and str(trashed["item_b"]) in e for e in errors), errors
    row = db.execute(
        "SELECT copy_barcode FROM item_copies WHERE item_id = ?", (target,)
    ).fetchone()
    assert row["copy_barcode"] is None


# ---------------------------------------------------------------------------
# 5. Merge
# ---------------------------------------------------------------------------

def test_merge_reparents_past_a_trashed_secondary_copy_number(admin_client, db, trashed):
    resp = admin_client.post(
        "/api/items/merge",
        json={"keep_id": trashed["item_a"], "merge_ids": [trashed["item_e"]]},
    )
    assert resp.status_code == 200

    moved = db.execute(
        "SELECT copy_number, is_primary FROM item_copies WHERE id = ?", (trashed["e1"],)
    ).fetchone()
    assert (moved["copy_number"], moved["is_primary"]) == (3, 0)


# ---------------------------------------------------------------------------
# 6. Zero-copy fallback (G86)
# ---------------------------------------------------------------------------

def test_audit_report_falls_back_to_items_location_when_only_copy_trashed(
    admin_client, trashed
):
    at_the_old_copy_location = _audit_missing(admin_client, trashed["loc_boathouse"])
    assert "Pin Item C Zero Copy Fallback" not in at_the_old_copy_location

    at_the_seam_location = _audit_missing(admin_client, trashed["loc_sunroom"])
    assert "Pin Item C Zero Copy Fallback" in at_the_seam_location


def test_cover_review_falls_back_to_items_location_when_only_copy_trashed(db, trashed):
    row = db.execute(
        f"SELECT {cover_review._QUEUE_COLUMNS} FROM items_live i WHERE i.id = ?",
        (trashed["item_c"],),
    ).fetchone()
    assert row["location_name"] == "Sunroom Overflow Case"


# ---------------------------------------------------------------------------
# The three readers that never join `items` themselves — copies_live's own
# join is their entire defence against a trashed item's copy.
# ---------------------------------------------------------------------------

def test_apply_copy_order_expected_set_excludes_a_trashed_items_copy(db, trashed):
    """item B's b1/b2 are both invisible; item F's f1 is the whole set."""
    location_order.apply_copy_order(db, trashed["loc_fern"], [trashed["f1"]])
    assert db.execute(
        "SELECT position_order FROM item_copies WHERE id = ?", (trashed["f1"],)
    ).fetchone()["position_order"] == 1


def test_append_copy_position_excludes_a_trashed_items_copy(db, trashed):
    """b2 carries position_order=5 but belongs to the trashed item B, so the
    next position for f1 (already at Fern Hollow Bay) is 1, not 6."""
    next_position = shelf_fill._append_copy_position(
        db, trashed["f1"], trashed["loc_fern"]
    )
    assert next_position == 1
