"""Routes that create, edit and remove a physical copy (`routers/item_copies.py`).

The data outcomes these pin are the design plan's contracts. The promotion and
the seam re-point are deliberately silent — nothing is announced to the user —
so every assertion here reads the rows rather than the copy on screen.

Seeding commits before each request: `get_db()` commits on context-manager
exit and the `db` fixture yields from inside its own block, so an uncommitted
row is invisible to the request's own connection and the assertion passes
vacuously against an empty library (G48).
"""

import json
import re
import time

from app.services import item_copies
from tests.conftest import _insert_item, _insert_location


def _copy(db, copy_id):
    return db.execute("SELECT * FROM item_copies WHERE id = ?", (copy_id,)).fetchone()


def _seam(db, item_id):
    return db.execute(
        "SELECT location_id FROM items WHERE id = ?", (item_id,)
    ).fetchone()["location_id"]


def _seed_two_copies(db):
    """An item with a primary at Living Room and a secondary in the Loft."""
    living = _insert_location(db, "Living Room")
    loft = _insert_location(db, "Loft")
    item_id = _insert_item(db, location_id=living)
    primary = item_copies.insert_copy(db, {
        "item_id": item_id, "copy_number": 1, "location_id": living,
        "is_primary": 1,
    })
    secondary = item_copies.insert_copy(db, {
        "item_id": item_id, "copy_number": 2, "location_id": loft,
    })
    db.execute("COMMIT")
    return {"item": item_id, "living": living, "loft": loft,
            "primary": primary, "secondary": secondary}


class TestAddRoute:
    def test_add_creates_a_secondary_and_leaves_the_seam_alone(self, admin_client, db):
        seed = _seed_two_copies(db)

        resp = admin_client.post(f"/api/items/{seed['item']}/copies",
                                 data={"location_id": str(seed["loft"])})

        assert resp.status_code == 200
        copies = item_copies.copies_for_item(db, seed["item"])
        assert [c["copy_number"] for c in copies] == [1, 2, 3]
        assert copies[2]["is_primary"] == 0
        assert _copy(db, seed["primary"])["is_primary"] == 1
        assert _seam(db, seed["item"]) == seed["living"]

    def test_add_to_a_zero_copy_item_creates_the_primary(self, admin_client, db):
        living = _insert_location(db, "Living Room")
        item_id = _insert_item(db)
        db.execute("COMMIT")

        resp = admin_client.post(f"/api/items/{item_id}/copies",
                                 data={"location_id": str(living)})

        assert resp.status_code == 200
        copies = item_copies.copies_for_item(db, item_id)
        assert len(copies) == 1
        assert copies[0]["is_primary"] == 1
        assert _seam(db, item_id) == living

    def test_an_empty_location_select_means_no_location(self, admin_client, db):
        """The picker's first option submits `""`. It must bind to None, not
        422 — the shape `routers/music.py` already ships."""
        item_id = _insert_item(db)
        db.execute("COMMIT")

        resp = admin_client.post(f"/api/items/{item_id}/copies",
                                 data={"location_id": "", "condition": "Good"})

        assert resp.status_code == 200
        copies = item_copies.copies_for_item(db, item_id)
        assert copies[0]["location_id"] is None
        assert copies[0]["condition"] == "Good"

    def test_add_refuses_an_unknown_item(self, admin_client, db):
        resp = admin_client.post("/api/items/9999/copies", data={"location_id": ""})
        assert resp.status_code == 404

    def test_a_duplicate_barcode_names_the_other_item(self, admin_client, db):
        living = _insert_location(db, "Living Room")
        other = _insert_item(db, title="Dune", isbn="9780000000019",
                             location_id=living)
        item_copies.insert_copy(db, {"item_id": other, "copy_number": 1,
                                     "location_id": living, "is_primary": 1,
                                     "copy_barcode": "SHARED-1"})
        mine = _insert_item(db, title="Mine", isbn="9780000000026")
        db.execute("COMMIT")

        resp = admin_client.post(f"/api/items/{mine}/copies",
                                 data={"location_id": "", "copy_barcode": "SHARED-1"})

        assert resp.status_code == 400
        assert "Dune" in resp.text
        assert item_copies.copies_for_item(db, mine) == []

    def test_a_viewer_cannot_add(self, viewer_client, db):
        seed = _seed_two_copies(db)
        resp = viewer_client.post(f"/api/items/{seed['item']}/copies",
                                  data={"location_id": ""})
        assert resp.status_code == 403
        assert len(item_copies.copies_for_item(db, seed["item"])) == 2


class TestUpdateRoute:
    def test_editing_the_primary_location_re_points_the_seam(self, admin_client, db):
        seed = _seed_two_copies(db)

        resp = admin_client.post(
            f"/api/items/{seed['item']}/copies/{seed['primary']}",
            data={"location_id": str(seed["loft"])},
        )

        assert resp.status_code == 200
        assert _copy(db, seed["primary"])["location_id"] == seed["loft"]
        assert _seam(db, seed["item"]) == seed["loft"]

    def test_editing_a_secondary_location_leaves_the_seam_alone(self, admin_client, db):
        """Moved to a **third** location, not to the one the seam already
        holds: a secondary sent to the seam's own location leaves the seam
        looking correct whether the route re-points it or not, so that version
        of this test passes against a route that re-points every copy."""
        seed = _seed_two_copies(db)
        attic = _insert_location(db, "Attic")
        db.execute("COMMIT")

        resp = admin_client.post(
            f"/api/items/{seed['item']}/copies/{seed['secondary']}",
            data={"location_id": str(attic)},
        )

        assert resp.status_code == 200
        assert _copy(db, seed["secondary"])["location_id"] == attic
        assert _seam(db, seed["item"]) == seed["living"]
        assert _copy(db, seed["primary"])["location_id"] == seed["living"]

    def test_every_panel_field_round_trips(self, admin_client, db):
        seed = _seed_two_copies(db)

        resp = admin_client.post(
            f"/api/items/{seed['item']}/copies/{seed['secondary']}",
            data={"location_id": str(seed["loft"]), "condition": "Fine",
                  "acquired_date": "2019-04-02", "acquisition_source": "Powell's",
                  "acquisition_price": "18.50", "provenance": "gift from R",
                  "copy_barcode": "COPY-42"},
        )

        assert resp.status_code == 200
        row = _copy(db, seed["secondary"])
        assert row["condition"] == "Fine"
        assert row["acquired_date"] == "2019-04-02"
        assert row["acquisition_source"] == "Powell's"
        assert row["acquisition_price"] == 18.5
        assert row["provenance"] == "gift from R"
        assert row["copy_barcode"] == "COPY-42"

    def test_a_move_clears_the_stale_shelf_position(self, admin_client, db):
        seed = _seed_two_copies(db)
        item_copies.update_copy(db, seed["secondary"], {"position_order": 3})
        db.execute("COMMIT")

        admin_client.post(
            f"/api/items/{seed['item']}/copies/{seed['secondary']}",
            data={"location_id": str(seed["living"])},
        )

        assert _copy(db, seed["secondary"])["position_order"] is None

    def test_an_empty_price_clears_rather_than_erroring(self, admin_client, db):
        seed = _seed_two_copies(db)
        item_copies.update_copy(db, seed["secondary"], {"acquisition_price": 9.0})
        db.execute("COMMIT")

        resp = admin_client.post(
            f"/api/items/{seed['item']}/copies/{seed['secondary']}",
            data={"location_id": str(seed["loft"]), "acquisition_price": ""},
        )

        assert resp.status_code == 200
        assert _copy(db, seed["secondary"])["acquisition_price"] is None

    def test_a_duplicate_barcode_on_edit_names_the_other_item(self, admin_client, db):
        seed = _seed_two_copies(db)
        other = _insert_item(db, title="Dune", isbn="9780000000019")
        item_copies.insert_copy(db, {"item_id": other, "copy_number": 1,
                                     "is_primary": 1, "copy_barcode": "SHARED-1"})
        db.execute("COMMIT")

        resp = admin_client.post(
            f"/api/items/{seed['item']}/copies/{seed['secondary']}",
            data={"location_id": str(seed["loft"]), "copy_barcode": "SHARED-1"},
        )

        assert resp.status_code == 400
        assert "Dune" in resp.text
        assert _copy(db, seed["secondary"])["copy_barcode"] is None

    def test_a_copy_keeps_its_own_barcode_on_a_second_save(self, admin_client, db):
        seed = _seed_two_copies(db)
        item_copies.update_copy(db, seed["secondary"], {"copy_barcode": "MINE-1"})
        db.execute("COMMIT")

        resp = admin_client.post(
            f"/api/items/{seed['item']}/copies/{seed['secondary']}",
            data={"location_id": str(seed["loft"]), "copy_barcode": "MINE-1",
                  "condition": "Fair"},
        )

        assert resp.status_code == 200
        row = _copy(db, seed["secondary"])
        assert row["copy_barcode"] == "MINE-1"
        assert row["condition"] == "Fair"

    def test_a_copy_of_another_item_is_not_reachable(self, admin_client, db):
        seed = _seed_two_copies(db)
        other = _insert_item(db, title="Dune", isbn="9780000000019")
        db.execute("COMMIT")

        resp = admin_client.post(
            f"/api/items/{other}/copies/{seed['secondary']}",
            data={"location_id": str(seed["loft"])},
        )

        assert resp.status_code == 404
        assert _copy(db, seed["secondary"])["location_id"] == seed["loft"]

    def test_a_viewer_cannot_edit(self, viewer_client, db):
        seed = _seed_two_copies(db)
        resp = viewer_client.post(
            f"/api/items/{seed['item']}/copies/{seed['secondary']}",
            data={"location_id": str(seed["living"])},
        )
        assert resp.status_code == 403
        assert _copy(db, seed["secondary"])["location_id"] == seed["loft"]


class TestRemoveRoute:
    def test_removing_the_primary_promotes_and_re_points(self, admin_client, db):
        seed = _seed_two_copies(db)

        resp = admin_client.delete(
            f"/api/items/{seed['item']}/copies/{seed['primary']}"
        )

        assert resp.status_code == 200
        assert _copy(db, seed["primary"]) is None
        assert _copy(db, seed["secondary"])["is_primary"] == 1
        assert _seam(db, seed["item"]) == seed["loft"]

    def test_removing_a_secondary_leaves_the_primary_alone(self, admin_client, db):
        seed = _seed_two_copies(db)

        resp = admin_client.delete(
            f"/api/items/{seed['item']}/copies/{seed['secondary']}"
        )

        assert resp.status_code == 200
        assert _copy(db, seed["primary"])["is_primary"] == 1
        assert _seam(db, seed["item"]) == seed["living"]

    def test_removing_the_last_copy_nulls_the_seam_and_keeps_the_item(
            self, admin_client, db):
        living = _insert_location(db, "Living Room")
        item_id = _insert_item(db, location_id=living)
        only = item_copies.insert_copy(db, {
            "item_id": item_id, "copy_number": 1, "location_id": living,
            "is_primary": 1,
        })
        db.execute("COMMIT")

        resp = admin_client.delete(f"/api/items/{item_id}/copies/{only}")

        assert resp.status_code == 200
        assert item_copies.copies_for_item(db, item_id) == []
        row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row is not None, "removing a copy must never delete the item"
        assert row["location_id"] is None

    def test_a_copy_of_another_item_is_not_removable(self, admin_client, db):
        seed = _seed_two_copies(db)
        other = _insert_item(db, title="Dune", isbn="9780000000019")
        db.execute("COMMIT")

        resp = admin_client.delete(f"/api/items/{other}/copies/{seed['secondary']}")

        assert resp.status_code == 404
        assert _copy(db, seed["secondary"]) is not None

    def test_a_viewer_cannot_remove(self, viewer_client, db):
        seed = _seed_two_copies(db)
        resp = viewer_client.delete(
            f"/api/items/{seed['item']}/copies/{seed['secondary']}"
        )
        assert resp.status_code == 403
        assert _copy(db, seed["secondary"]) is not None


class TestFragmentContext:
    """What `_render_block` must hand the template.

    The collapsed block's zero-copy `legacy` arm cannot be reached from the
    three mutations here — an add gives the item a copy, and removing the last
    one nulls the seam — so this pins the helper that supplies it. The rendered
    legacy arm itself is pinned in `tests/test_item_detail.py` (T4) and through
    the collapsed GET route (T5).
    """

    def test_the_item_row_carries_the_joined_location_name(self, db):
        """gemini-R1: the fragment's `legacy` arm renders
        `item.location_name`, which only the LEFT JOIN supplies. A plain
        `SELECT *` leaves the key missing, Jinja swallows it as `Undefined`,
        and the seam location silently stops rendering — nothing raises, so no
        other assertion in this file would notice."""
        from app.routers.item_copies import _item_with_location

        living = _insert_location(db, "Reading Nook")
        item_id = _insert_item(db, location_id=living)

        assert _item_with_location(db, item_id)["location_name"] == "Reading Nook"

    def test_an_unlocated_item_has_a_null_location_name(self, db):
        from app.routers.item_copies import _item_with_location

        item_id = _insert_item(db)

        assert _item_with_location(db, item_id)["location_name"] is None

    def test_the_block_is_offered_every_location_in_sort_order(self, db):
        from app.routers.item_copies import _all_locations

        _insert_location(db, "Zebra Room")
        _insert_location(db, "Attic")

        names = [r["name"] for r in _all_locations(db)]
        assert names == ["Attic", "Zebra Room"]


class TestEditPanel:
    """The two GETs: the expanded panel, and the collapsed block Cancel asks
    for. Both are `editor` — a viewer's page renders no control that reaches
    them, and the routes refuse one anyway."""

    def test_the_panel_renders_every_field_for_the_named_copy(self, admin_client, db):
        seed = _seed_two_copies(db)
        item_copies.update_copy(db, seed["secondary"], {
            "condition": "Fair", "acquired_date": "2019-04-02",
            "acquisition_source": "Powell's", "acquisition_price": 18.5,
            "provenance": "gift from R", "copy_barcode": "COPY-42",
        })
        db.execute("COMMIT")

        html = admin_client.get(
            f"/api/items/{seed['item']}/copies/{seed['secondary']}/edit"
        ).text

        assert 'name="location_id"' in html
        assert 'value="Fair"' in html
        assert 'value="2019-04-02"' in html
        assert "Powell&#39;s" in html or "Powell's" in html
        assert 'value="18.5"' in html
        assert "gift from R" in html
        assert 'value="COPY-42"' in html

    def test_the_panel_posts_back_to_that_copy(self, admin_client, db):
        seed = _seed_two_copies(db)

        html = admin_client.get(
            f"/api/items/{seed['item']}/copies/{seed['secondary']}/edit"
        ).text

        assert f'hx-post="/api/items/{seed["item"]}/copies/{seed["secondary"]}"' in html
        assert f'hx-delete="/api/items/{seed["item"]}/copies/{seed["secondary"]}"' in html

    def test_the_price_input_pairs_step_with_min(self, admin_client, db):
        """G35 — a bare `step` takes the element's own `value` as its step
        base, so editing an existing price off that grid makes the browser
        refuse the whole form, silently, with no server-side signal."""
        seed = _seed_two_copies(db)

        html = admin_client.get(
            f"/api/items/{seed['item']}/copies/{seed['secondary']}/edit"
        ).text

        price_tag = [t for t in re.findall(r"<input[^>]*>", html)
                     if 'name="acquisition_price"' in t]
        assert price_tag, "the panel should carry a price input"
        for tag in price_tag:
            assert 'step="any"' in tag or 'min=' in tag, tag

    def test_the_remove_confirm_names_what_is_lost(self, admin_client, db):
        """Removal is permanent — there is no soft delete anywhere in this
        schema — so the confirm has to say what goes with the row."""
        seed = _seed_two_copies(db)

        html = admin_client.get(
            f"/api/items/{seed['item']}/copies/{seed['secondary']}/edit"
        ).text

        confirm = re.search(r'hx-confirm="([^"]*)"', html)
        assert confirm, "Remove copy must be guarded by hx-confirm"
        message = confirm.group(1)
        for word in ("condition", "acquisition", "provenance", "permanently"):
            assert word in message, f"the confirm does not name {word}: {message}"

    def test_the_condition_input_suggests_without_constraining(self, admin_client, db):
        seed = _seed_two_copies(db)

        html = admin_client.get(
            f"/api/items/{seed['item']}/copies/{seed['secondary']}/edit"
        ).text

        assert '<datalist' in html
        for grade in ("New", "Fine", "Good", "Fair", "Poor", "Ex-library"):
            assert f'value="{grade}"' in html
        condition = [t for t in re.findall(r"<input[^>]*>", html)
                     if 'name="condition"' in t]
        assert condition and 'type="text"' in condition[0], condition

    def test_a_copy_of_another_item_has_no_panel(self, admin_client, db):
        seed = _seed_two_copies(db)
        other = _insert_item(db, title="Dune", isbn="9780000000019")
        db.execute("COMMIT")

        resp = admin_client.get(
            f"/api/items/{other}/copies/{seed['secondary']}/edit"
        )

        assert resp.status_code == 404

    def test_a_viewer_gets_no_panel(self, viewer_client, db):
        seed = _seed_two_copies(db)
        resp = viewer_client.get(
            f"/api/items/{seed['item']}/copies/{seed['secondary']}/edit"
        )
        assert resp.status_code == 403


class TestCollapsedBlockRoute:
    def test_cancel_restores_the_block(self, admin_client, db):
        seed = _seed_two_copies(db)

        html = admin_client.get(f"/api/items/{seed['item']}/copies").text

        assert 'id="item-copies"' in html
        assert ">Copies<" in html
        assert "Living Room" in html and "Loft" in html
        assert 'name="acquisition_price"' not in html, "that is the panel, not the block"

    def test_the_block_renders_the_zero_copy_seam(self, admin_client, db):
        """The fragment's `legacy` arm reads `item.location_name`, which only
        the LEFT JOIN in `_item_with_location` supplies. A plain `SELECT *`
        leaves the key missing, Jinja swallows it as `Undefined`, and the seam
        location silently stops rendering — nothing raises. This is the one
        route that can reach that arm, so the pin lives here."""
        nook = _insert_location(db, "Reading Nook")
        item_id = _insert_item(db, location_id=nook)
        db.execute("DELETE FROM item_copies WHERE item_id = ?", (item_id,))
        db.execute("COMMIT")

        html = admin_client.get(f"/api/items/{item_id}/copies").text

        assert "Reading Nook" in html
        assert f"/browse?location_filter={nook}" in html

    def test_an_unknown_item_has_no_block(self, admin_client, db):
        assert admin_client.get("/api/items/9999/copies").status_code == 404

    def test_a_viewer_cannot_ask_for_the_block(self, viewer_client, db):
        seed = _seed_two_copies(db)
        resp = viewer_client.get(f"/api/items/{seed['item']}/copies")
        assert resp.status_code == 403


class TestRefusalsDoNotLogUnderTheWriteLock:
    """G3 — `SQLiteHandler` opens its own connection to write `log_entries`,
    so a `logger.*` call made while the request still holds its
    `BEGIN IMMEDIATE` lock blocks on itself until SQLite's 5s busy timeout.
    The handler then swallows the timeout and drops the record: the request
    answers correctly, five seconds late, with no log line. Nothing goes red,
    which is why this survived into the branch and was caught in diff review.

    Both halves are asserted. The **landed record** is the deterministic one —
    under the trap it is dropped, so its presence cannot be satisfied by
    coincidence. The **stopwatch** is the one that names the user-visible cost;
    its threshold is far below the 5s timeout and far above the ~0.01s the
    fixed path takes, so it is not a flaky margin. Measured on the unfixed
    code: 5.01s and zero log rows; fixed: 0.01s and one.

    **What actually releases the lock is `db.rollback()`, not leaving the
    `with` block.** The mutation run showed a log line emitted *after* the
    rollback but still inside the block is already fast and still lands — so
    the rule these tests enforce is rollback-before-log. Emitting from outside
    the block as well is belt-and-braces, and it is what keeps the rule legible
    to the next reader.
    """

    def _refused_add(self, admin_client, db):
        """An unknown `location_id` trips the foreign key inside the funnel —
        the reachable route to the IntegrityError arm."""
        item_id = _insert_item(db)
        db.execute("COMMIT")
        start = time.monotonic()
        resp = admin_client.post(f"/api/items/{item_id}/copies",
                                 data={"location_id": "99999"})
        return resp, time.monotonic() - start, item_id

    def test_a_refused_add_still_writes_its_log_line(self, admin_client, db):
        resp, _, item_id = self._refused_add(admin_client, db)

        assert resp.status_code == 400
        logged = db.execute(
            "SELECT message FROM log_entries WHERE message LIKE 'Copy add refused%'"
        ).fetchall()
        assert len(logged) == 1, (
            "the refusal's log record was dropped — the logger is being called "
            "while the request still holds the write lock (G3)"
        )
        assert str(item_id) in logged[0]["message"]

    def test_a_refused_add_does_not_wait_out_the_busy_timeout(self, admin_client, db):
        _, elapsed, _ = self._refused_add(admin_client, db)

        assert elapsed < 2.0, (
            f"the refused add took {elapsed:.2f}s — SQLite's busy timeout is 5s, "
            "so this is the logger blocking on the request's own write lock (G3)"
        )

    def test_a_refused_add_leaves_no_partial_row(self, admin_client, db):
        """`get_db()` commits on clean exit, so a caught exception that returns
        normally commits whatever landed before it. The catch rolls back first.

        **This assertion cannot currently fail, and that is recorded rather
        than hidden.** On this path the statement that raises is the only write
        the request makes, so there is nothing partial to commit either way —
        removing the `db.rollback()` leaves it green, verified by mutation. It
        is kept as a regression tripwire for the day the funnel writes
        something before the statement that fails, which is exactly the shape
        diff review flagged as unreachable-for-now. Read it as documentation of
        the hazard, not as proof the rollback works; what proves the rollback
        works is the stopwatch above, because the rollback is what releases the
        write lock before the log line is emitted."""
        resp, _, item_id = self._refused_add(admin_client, db)

        assert resp.status_code == 400
        assert item_copies.copies_for_item(db, item_id) == []

    def test_a_refused_update_keeps_the_copy_as_it_was(self, admin_client, db):
        seed = _seed_two_copies(db)
        item_copies.update_copy(db, seed["secondary"], {"condition": "Good"})
        db.execute("COMMIT")

        start = time.monotonic()
        resp = admin_client.post(
            f"/api/items/{seed['item']}/copies/{seed['secondary']}",
            data={"location_id": "99999", "condition": "Ruined"},
        )
        elapsed = time.monotonic() - start

        assert resp.status_code == 400
        assert elapsed < 2.0, f"took {elapsed:.2f}s — the logger is under the lock (G3)"
        # Same caveat as the add-side row assertion above: the failing
        # statement is the only write, so this is a tripwire for a future
        # partial write rather than a pin the current rollback can break.
        row = _copy(db, seed["secondary"])
        assert row["condition"] == "Good", "a refused update must not half-apply"
        assert row["location_id"] == seed["loft"]
        logged = db.execute(
            "SELECT message FROM log_entries WHERE message LIKE 'Copy update refused%'"
        ).fetchall()
        assert len(logged) == 1


class TestARefusalReachesTheUser:
    """Every refusal must carry an `HX-Trigger` toast, not just a status code.

    htmx's default `responseHandling` matches `[45]..` with `swap:false`, so
    the response body of a refusal is never swapped into `hx-target` and the
    message reaches the browser console and nowhere else — the panel sits
    unchanged and a failed save looks exactly like a click that did nothing.
    The header is what puts it on screen, and asserting the status code alone
    cannot tell the two apart, which is how the gate stayed green over a
    surface that told the user nothing (test drive, 2026-09-11).

    Each assertion reads the message out of the header rather than checking
    the header merely exists: a toast that fires with the wrong text, or
    styled as a success, is its own defect.
    """

    def _toast(self, resp):
        assert "HX-Trigger" in resp.headers, "a refusal with no toast is invisible"
        return json.loads(resp.headers["HX-Trigger"])["showToast"]

    def test_a_duplicate_barcode_on_add_toasts_the_other_item(self, admin_client, db):
        other = _insert_item(db, title="Dune", isbn="9780000000019")
        item_copies.insert_copy(db, {"item_id": other, "copy_number": 1,
                                     "is_primary": 1, "copy_barcode": "SHARED-1"})
        mine = _insert_item(db, title="Mine", isbn="9780000000026")
        db.execute("COMMIT")

        resp = admin_client.post(f"/api/items/{mine}/copies",
                                 data={"location_id": "", "copy_barcode": "SHARED-1"})

        toast = self._toast(resp)
        assert "Dune" in toast["message"], "the toast must name the other item"
        assert toast["type"] == "error", "a refusal styled as success misleads"

    def test_a_duplicate_barcode_on_edit_toasts_the_other_item(self, admin_client, db):
        seed = _seed_two_copies(db)
        other = _insert_item(db, title="Dune", isbn="9780000000019")
        item_copies.insert_copy(db, {"item_id": other, "copy_number": 1,
                                     "is_primary": 1, "copy_barcode": "SHARED-1"})
        db.execute("COMMIT")

        resp = admin_client.post(
            f"/api/items/{seed['item']}/copies/{seed['secondary']}",
            data={"location_id": "", "copy_barcode": "SHARED-1"},
        )

        toast = self._toast(resp)
        assert "Dune" in toast["message"]
        assert toast["type"] == "error"

    def test_an_integrity_failure_on_add_toasts_what_the_body_says(self, admin_client, db):
        """The `_refuse` path — the one that also emits the deferred log line.

        Pinned separately from the barcode clashes because it returns through
        `_refuse()` rather than directly, so a header added only to the direct
        returns would leave this one silent.
        """
        item_id = _insert_item(db)
        db.execute("COMMIT")

        resp = admin_client.post(f"/api/items/{item_id}/copies",
                                 data={"location_id": "99999"})

        assert resp.status_code == 400
        toast = self._toast(resp)
        assert toast["message"] == resp.text, "the toast and the body must agree"
        assert toast["type"] == "error"

    def test_an_integrity_failure_on_update_toasts_what_the_body_says(self, admin_client, db):
        seed = _seed_two_copies(db)

        resp = admin_client.post(
            f"/api/items/{seed['item']}/copies/{seed['secondary']}",
            data={"location_id": "99999"},
        )

        assert resp.status_code == 400
        toast = self._toast(resp)
        assert toast["message"] == resp.text
        assert toast["type"] == "error"

    def test_a_missing_copy_toasts_rather_than_failing_silently(self, admin_client, db):
        item_id = _insert_item(db)
        db.execute("COMMIT")

        for resp in (
            admin_client.get(f"/api/items/{item_id}/copies/9999/edit"),
            admin_client.post(f"/api/items/{item_id}/copies/9999",
                              data={"location_id": ""}),
            admin_client.delete(f"/api/items/{item_id}/copies/9999"),
        ):
            assert resp.status_code == 404
            assert self._toast(resp)["message"] == "Copy not found"

    def test_a_missing_item_toasts_rather_than_failing_silently(self, admin_client, db):
        for resp in (
            admin_client.get("/api/items/9999/copies"),
            admin_client.post("/api/items/9999/copies", data={"location_id": ""}),
        ):
            assert resp.status_code == 404
            assert self._toast(resp)["message"] == "Item not found"

    def test_a_successful_mutation_carries_no_error_toast(self, admin_client, db):
        """The other half of the rule: success is announced by the swap itself.

        Without this, attaching the header unconditionally — to every response
        the router returns — would satisfy every assertion above while
        toasting "Copy not found" over a save that worked.
        """
        item_id = _insert_item(db)
        db.execute("COMMIT")

        resp = admin_client.post(f"/api/items/{item_id}/copies",
                                 data={"location_id": ""})

        assert resp.status_code == 200
        assert "HX-Trigger" not in resp.headers
