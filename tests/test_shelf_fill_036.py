import re
from unittest.mock import AsyncMock, patch

import pytest

from app.routers import shelf_fill
from app.services import provider_result
from app.services import upcitemdb
from app.services import lists
from app.services import locations as location_svc
from app.services import tags as tags_svc
from tests.conftest import _assert_ownership_partition
from tests.test_legacy_book import KRISTY_SUPPLEMENT, KRISTY_UPC
from tests.test_legacy_book_scan import KRISTY_ISBN13, KRISTY_UPC5


def _item(db, *, title="Filed book", owned=1, isbn=None, wishlisted=False):
    cur = db.execute(
        "INSERT INTO items (title, media_type, owned, isbn) VALUES (?, 'book', ?, ?)",
        (title, owned, isbn),
    )
    if wishlisted:
        from app.services import lists

        lists.add(db, lists.WISHLIST, cur.lastrowid)
    return cur.lastrowid


def test_place_item_creates_primary_copy_and_appends_when_ordering_exists(db):
    room = location_svc.create_location(db, "Living Room")
    shelf = location_svc.create_location(db, "Shelf 1", parent_id=room)
    first_item = _item(db, title="First")
    second_item = _item(db, title="Second")

    first = shelf_fill._place_item(db, first_item, shelf)
    result = shelf_fill._place_item(db, second_item, shelf)

    item = db.execute("SELECT owned, location_id FROM items WHERE id = ?", (second_item,)).fetchone()
    copies = db.execute(
        "SELECT item_id, location_id, is_primary, position_order FROM item_copies "
        "WHERE location_id = ? ORDER BY position_order", (shelf,)
    ).fetchall()
    assert item["location_id"] == shelf
    assert [row["item_id"] for row in copies] == [first_item, second_item]
    assert [row["position_order"] for row in copies] == [1, 2]
    assert all(row["is_primary"] == 1 for row in copies)
    assert first["position_order"] == 1
    assert result["position_order"] == 2
    assert result["location_name"] == "Living Room / Shelf 1"


def test_place_item_promotes_wishlist_to_owned(db):
    shelf = location_svc.create_location(db, "Shelf")
    item_id = _item(db, owned=0, wishlisted=True)

    result = shelf_fill._place_item(db, item_id, shelf)

    item = db.execute("SELECT owned FROM items WHERE id = ?", (item_id,)).fetchone()
    assert item["owned"] == 1
    assert result["was_wishlist"] is True
    assert not lists.is_member(db, lists.WISHLIST, item_id)
    _assert_ownership_partition(db)


def test_place_exact_copy_promotes_wishlist_and_removes_membership(db):
    """`_place_exact_copy`'s secondary-copy branch (app/routers/shelf_fill.py)
    has no pin today. The wishlist item here carries only a
    secondary (non-primary) copy — no primary copy row at all — which is the
    G96-shaped danger: if the promotion's `owned = 1` write ever grew a
    `location_id` key, it would re-enter `item_copies.sync_primary_location`,
    which *creates* a primary copy when none exists. Asserting the copy count
    and its `is_primary` flag stay put, not just that `owned` flipped, is what
    would catch that.
    """
    first = location_svc.create_location(db, "Shelf A")
    target = location_svc.create_location(db, "Shelf B")
    item_id = _item(db, owned=0, wishlisted=True)
    secondary_id = db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id, copy_barcode, is_primary) "
        "VALUES (?, 1, ?, 'WISH-COPY-1', 0)", (item_id, first),
    ).lastrowid

    exact = shelf_fill._copy_by_barcode(db, "WISH-COPY-1")
    assert exact["is_primary"] == 0
    result = shelf_fill._place_exact_copy(db, exact, target)

    item = db.execute("SELECT owned FROM items WHERE id = ?", (item_id,)).fetchone()
    assert item["owned"] == 1
    assert result["was_wishlist"] is True
    assert not lists.is_member(db, lists.WISHLIST, item_id)

    copies = db.execute(
        "SELECT id, is_primary, location_id FROM item_copies WHERE item_id = ?",
        (item_id,),
    ).fetchall()
    assert [row["id"] for row in copies] == [secondary_id]
    assert copies[0]["is_primary"] == 0
    assert copies[0]["location_id"] == target
    _assert_ownership_partition(db)


def test_copy_barcode_moves_exact_secondary_without_moving_primary(db):
    first = location_svc.create_location(db, "Shelf A")
    second = location_svc.create_location(db, "Shelf B")
    target = location_svc.create_location(db, "Shelf C")
    item_id = _item(db)
    db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id, copy_barcode, is_primary) "
        "VALUES (?, 1, ?, 'COPY-1', 1)", (item_id, first),
    )
    secondary_id = db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id, copy_barcode, is_primary) "
        "VALUES (?, 2, ?, 'COPY-2', 0)", (item_id, second),
    ).lastrowid
    db.execute("UPDATE items SET location_id = ? WHERE id = ?", (first, item_id))

    exact = shelf_fill._copy_by_barcode(db, "COPY-2")
    result = shelf_fill._place_exact_copy(db, exact, target)

    primary = db.execute(
        "SELECT location_id FROM item_copies WHERE item_id = ? AND is_primary = 1", (item_id,)
    ).fetchone()
    secondary = db.execute(
        "SELECT location_id FROM item_copies WHERE id = ?", (secondary_id,)
    ).fetchone()
    item = db.execute("SELECT location_id FROM items WHERE id = ?", (item_id,)).fetchone()
    assert primary["location_id"] == first
    assert item["location_id"] == first
    assert secondary["location_id"] == target
    assert result["copy_number"] == 2


def test_trashed_copy_label_is_reported_and_moves_nothing(editor_client, db):
    """qa-copy-labels Obs 1 (review B1): a trashed copy's label must not fall
    through to the item path, which would move the live primary instead."""
    from app.services import item_copies

    first = location_svc.create_location(db, "Shelf A")
    target = location_svc.create_location(db, "Shelf C")
    item_id = _item(db)
    db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id, copy_barcode, is_primary) "
        "VALUES (?, 1, ?, 'COPY-1', 1)", (item_id, first),
    )
    secondary_id = db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id, copy_barcode, is_primary) "
        "VALUES (?, 2, ?, 'COPY-2', 0)", (item_id, first),
    ).lastrowid
    db.execute("UPDATE items SET location_id = ? WHERE id = ?", (first, item_id))
    item_copies.trash_copy(db, secondary_id)
    db.commit()

    resp = editor_client.post("/api/shelf-fill/scan",
                              data={"isbn": "COPY-2", "location_id": str(target)})

    assert resp.status_code == 200
    assert 'data-scan-status="in_trash"' in resp.text
    locs = db.execute("SELECT location_id FROM item_copies WHERE item_id = ?", (item_id,)).fetchall()
    assert [r["location_id"] for r in locs] == [first, first]
    assert db.execute("SELECT location_id FROM items WHERE id = ?", (item_id,)).fetchone()[0] == first


def test_moving_a_secondary_copy_clears_its_old_shelf_position(db, monkeypatch):
    """_place_exact_copy's own location-setting write, isolated from the
    append that immediately follows it in the real flow, must clear a moved
    copy's stale position_order rather than carry it to the new shelf.

    In the ordinary flow ``_append_copy_position`` always overwrites
    ``position_order`` with a freshly computed value afterwards, which would
    mask whether the preceding location UPDATE itself cleared the stale one —
    so the append is stubbed out here to pin that statement's own contract.
    """
    first = location_svc.create_location(db, "Shelf A")
    second = location_svc.create_location(db, "Shelf B")
    item_id = _item(db)
    db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id, copy_barcode, is_primary) "
        "VALUES (?, 1, ?, 'PRIMARY-1', 1)", (item_id, first),
    )
    secondary_id = db.execute(
        "INSERT INTO item_copies "
        "(item_id, copy_number, location_id, copy_barcode, is_primary, position_order) "
        "VALUES (?, 2, ?, 'SECONDARY-1', 0, 5)", (item_id, first),
    ).lastrowid
    monkeypatch.setattr(shelf_fill, "_append_copy_position", lambda *a, **k: None)

    exact = shelf_fill._copy_by_barcode(db, "SECONDARY-1")
    shelf_fill._place_exact_copy(db, exact, second)

    moved = db.execute(
        "SELECT location_id, position_order FROM item_copies WHERE id = ?", (secondary_id,)
    ).fetchone()
    assert moved["location_id"] == second
    assert moved["position_order"] is None


def test_append_copy_position_skips_a_copy_that_has_since_moved(db):
    """The `AND location_id = ?` guard inside the append's own UPDATE (G18):
    if the copy is no longer at the location this call believes it placed it
    on, the position write must be skipped rather than clobbering wherever
    the copy actually is now."""
    shelf_a = location_svc.create_location(db, "Shelf A")
    shelf_b = location_svc.create_location(db, "Shelf B")
    item_id = _item(db)
    copy_id = db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id, is_primary) "
        "VALUES (?, 1, ?, 1)", (item_id, shelf_b),
    ).lastrowid
    before = db.execute(
        "SELECT location_id, position_order FROM item_copies WHERE id = ?", (copy_id,)
    ).fetchone()

    # A stale call believing the copy is still on shelf_a.
    result = shelf_fill._append_copy_position(db, copy_id, shelf_a)

    assert result is None
    after = db.execute(
        "SELECT location_id, position_order FROM item_copies WHERE id = ?", (copy_id,)
    ).fetchone()
    assert tuple(after) == tuple(before)


def test_shelf_fill_page_lists_nested_locations(admin_client, db):
    room = location_svc.create_location(db, "Bedroom")
    location_svc.create_location(db, "Bookcase", parent_id=room)
    # The TestClient serves requests through a separate DB connection, so make
    # the setup visible before exercising the page route.
    db.commit()

    response = admin_client.get("/shelf-fill")

    assert response.status_code == 200
    assert "Shelf Fill" in response.text
    assert "Bedroom" in response.text
    assert "Bookcase" in response.text


def test_viewer_cannot_open_shelf_fill(viewer_client):
    response = viewer_client.get("/shelf-fill", follow_redirects=False)
    assert response.status_code in (302, 303, 403)


def test_shelf_fill_page_renders_default_tags_control(admin_client):
    page = admin_client.get("/shelf-fill").text
    tag = re.search(r'<input[^>]*id="default-tags"[^>]*>', page, re.S).group(0)
    assert 'name="tags"' in tag
    assert 'list="tag-suggestions"' in tag
    assert 'form="shelf-fill-form"' in tag
    assert 'data-storage-key="shelf_fill_tags"' in tag
    assert 'data-type-source="#shelf-fill-media-type"' in tag
    # Shelf Fill only adds — no :disabled binding, unlike Scan's.
    assert ":disabled=" not in tag
    assert '<datalist id="tag-suggestions">' in page
    assert '/static/js/tag-suggest.js' in page

# --- The shelf position, made visible (0.37.2) -------------------------------

def test_two_bookcases_number_their_shelves_independently(db):
    """The workflow the feature is for: fill one shelf, move to another.

    Position is scoped to `location_id`, so shelf 2 of a different bookcase
    starts at 1 again rather than continuing the first shelf's count.
    """
    room = location_svc.create_location(db, "Study")
    case_a = location_svc.create_location(db, "Bookcase 1", parent_id=room)
    case_b = location_svc.create_location(db, "Bookcase 2", parent_id=room)
    shelf_a1 = location_svc.create_location(db, "Shelf 1", parent_id=case_a)
    shelf_b2 = location_svc.create_location(db, "Shelf 2", parent_id=case_b)

    a = [shelf_fill._place_item(db, _item(db, title=f"A{i}"), shelf_a1) for i in range(3)]
    b = [shelf_fill._place_item(db, _item(db, title=f"B{i}"), shelf_b2) for i in range(2)]

    assert [r["position_order"] for r in a] == [1, 2, 3]
    assert [r["position_order"] for r in b] == [1, 2], "second bookcase restarted"
    assert a[0]["location_name"] == "Study / Bookcase 1 / Shelf 1"
    assert b[0]["location_name"] == "Study / Bookcase 2 / Shelf 2"


def test_summary_reports_total_next_position_and_unordered_remainder(db):
    shelf = location_svc.create_location(db, "Shelf")
    assert shelf_fill._shelf_summary(db, shelf) == {
        "total": 0, "placed": 0, "next_position": 1,
    }

    shelf_fill._place_item(db, _item(db, title="One"), shelf)
    shelf_fill._place_item(db, _item(db, title="Two"), shelf)
    assert shelf_fill._shelf_summary(db, shelf) == {
        "total": 2, "placed": 2, "next_position": 3,
    }

    # A copy moved here by Scan's Move mode carries no position and sorts last
    # on Arrange; the summary has to say so rather than imply the shelf is
    # fully ordered.
    stray = _item(db, title="Moved by Scan")
    db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id, is_primary) "
        "VALUES (?, 1, ?, 1)", (stray, shelf),
    )
    assert shelf_fill._shelf_summary(db, shelf) == {
        "total": 3, "placed": 2, "next_position": 3,
    }


def test_result_card_shows_the_position(editor_client, db):
    room = location_svc.create_location(db, "Room")
    shelf = location_svc.create_location(db, "Shelf 3", parent_id=room)
    item_id = _item(db, title="Shown Position")
    db.commit()

    resp = editor_client.post(
        "/api/shelf-fill/place", data={"item_id": item_id, "location_id": shelf}
    )

    assert resp.status_code == 200
    assert 'data-testid="shelf-fill-position"' in resp.text
    assert "#1" in resp.text
    # and the picker's summary is refreshed out of band by the same response
    assert 'hx-swap-oob="true"' in resp.text
    assert 'data-testid="shelf-fill-summary-counts"' in resp.text


def test_summary_endpoint_answers_for_a_chosen_shelf(editor_client, db):
    shelf = location_svc.create_location(db, "Target")
    shelf_fill._place_item(db, _item(db, title="Already here"), shelf)
    db.commit()

    resp = editor_client.get(f"/api/shelf-fill/summary?location_id={shelf}")

    assert resp.status_code == 200
    assert ">1</strong> item on this shelf" in resp.text
    assert "next scan goes to position <strong" in resp.text
    assert ">2</strong>" in resp.text
    assert f"/locations/{shelf}/arrange" in resp.text
    # no OOB marker when loaded directly — it replaces the element by target
    assert 'hx-swap-oob' not in resp.text


def test_summary_endpoint_handles_no_selection_and_a_deleted_shelf(editor_client, db):
    empty = editor_client.get("/api/shelf-fill/summary?location_id=0")
    assert empty.status_code == 200
    assert "item on this shelf" not in empty.text

    gone = editor_client.get("/api/shelf-fill/summary?location_id=999999")
    assert gone.status_code == 200
    assert "no longer exists" in gone.text


def test_summary_endpoint_refuses_a_viewer(viewer_client, db):
    shelf = location_svc.create_location(db, "Shelf")
    db.commit()
    resp = viewer_client.get(f"/api/shelf-fill/summary?location_id={shelf}")
    assert resp.status_code in (302, 303, 401, 403)


class TestBareLegacyUpcInsideShelfFill:
    """#90 — the incomplete card must be resolvable without leaving Shelf Fill.

    Shelf Fill calls `items.scan_isbn` as a plain function and assigns
    `position_order` only after *its own* added/duplicate response. A card
    whose form posted to `/api/scan` would create the item outside this route,
    so `_place_item` would never run and the book would land with no shelf
    position under a heading that says it was filed.
    """

    @staticmethod
    def _hidden(html):
        return dict(
            re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', html)
        )

    @staticmethod
    def _action(html):
        return re.search(r'hx-post="([^"]+)"', html).group(1)

    @pytest.fixture
    def shelf_with_a_copy(self, db):
        room = location_svc.create_location(db, "Room")
        shelf = location_svc.create_location(db, "Shelf 9", parent_id=room)
        shelf_fill._place_item(db, _item(db, title="Already here"), shelf)
        db.commit()
        return shelf

    @pytest.fixture(autouse=True)
    def _no_upc_lookup(self, monkeypatch):
        async def _forbidden(upc, client):
            raise AssertionError("no UPC lookup")

        monkeypatch.setattr(upcitemdb, "lookup", _forbidden)

    def _scan_bare(self, editor_client, shelf):
        return editor_client.post(
            "/api/shelf-fill/scan",
            data={"isbn": KRISTY_UPC, "location_id": shelf, "media_type": "book"},
        )

    def test_the_card_posts_back_to_shelf_fill_and_the_book_gets_a_position(
        self, editor_client, db, shelf_with_a_copy
    ):
        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            if isbn == KRISTY_ISBN13:
                metadata = {"title": "Kristy", "authors": "Ann M. Martin"}
                return metadata, "openlibrary", {}, provider_result.found(
                    "openlibrary", metadata
                )
            return None, "manual", {}, provider_result.no_match("openlibrary")

        card = self._scan_bare(editor_client, shelf_with_a_copy)

        assert card.status_code == 200
        assert 'data-scan-status="legacy_incomplete"' in card.text
        # The finding: the continuation must stay on this route.
        assert self._action(card.text) == "/api/shelf-fill/scan"
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1

        payload = self._hidden(card.text)
        payload["legacy_supplement"] = KRISTY_SUPPLEMENT
        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ), patch("app.routers.items.cover_queue.enqueue"):
            filed = editor_client.post(self._action(card.text), data=payload)

        assert filed.status_code == 200
        assert 'data-testid="shelf-fill-position"' in filed.text
        assert 'data-testid="shelf-fill-summary-counts"' in filed.text
        item = db.execute(
            "SELECT id FROM items WHERE isbn = ?", (KRISTY_ISBN13,)
        ).fetchone()
        assert item is not None
        copy = db.execute(
            "SELECT position_order FROM item_copies "
            "WHERE item_id = ? AND is_primary = 1",
            (item["id"],),
        ).fetchone()
        assert copy["position_order"] == 2

    def test_the_continuation_carries_tags_through_to_a_tagged_placed_item(
        self, editor_client, db, shelf_with_a_copy
    ):
        """G103 pin, tags flavor: the bare-UPC continuation's hidden `tags`
        input (T2) must survive to the item this route finally places — not
        only the position, which the sibling test above already covers."""
        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            if isbn == KRISTY_ISBN13:
                metadata = {"title": "Kristy", "authors": "Ann M. Martin"}
                return metadata, "openlibrary", {}, provider_result.found(
                    "openlibrary", metadata
                )
            return None, "manual", {}, provider_result.no_match("openlibrary")

        card = editor_client.post(
            "/api/shelf-fill/scan",
            data={
                "isbn": KRISTY_UPC, "location_id": shelf_with_a_copy,
                "media_type": "book", "tags": "Cookbook",
            },
        )

        assert card.status_code == 200
        assert 'data-scan-status="legacy_incomplete"' in card.text
        assert self._action(card.text) == "/api/shelf-fill/scan"

        payload = self._hidden(card.text)
        assert payload.get("tags") == "Cookbook"
        payload["legacy_supplement"] = KRISTY_SUPPLEMENT
        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ), patch("app.routers.items.cover_queue.enqueue"):
            filed = editor_client.post(self._action(card.text), data=payload)

        assert filed.status_code == 200
        assert 'data-testid="shelf-fill-position"' in filed.text
        item = db.execute(
            "SELECT id FROM items WHERE isbn = ?", (KRISTY_ISBN13,)
        ).fetchone()
        assert item is not None
        copy = db.execute(
            "SELECT position_order FROM item_copies "
            "WHERE item_id = ? AND is_primary = 1",
            (item["id"],),
        ).fetchone()
        assert copy["position_order"] == 2
        assert [t["name"] for t in tags_svc.get_item_tags(db, item["id"])] == ["Cookbook"]

    def test_the_ambiguous_hop_also_stays_on_shelf_fill_and_places(
        self, editor_client, db, shelf_with_a_copy
    ):
        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            metadata = {"title": f"Candidate {isbn}", "authors": "Scholastic"}
            return metadata, "openlibrary", {}, provider_result.found(
                "openlibrary", metadata
            )

        card = self._scan_bare(editor_client, shelf_with_a_copy)
        payload = self._hidden(card.text)
        payload["legacy_supplement"] = KRISTY_SUPPLEMENT

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ), patch("app.routers.items.cover_queue.enqueue"):
            ambiguous = editor_client.post(self._action(card.text), data=payload)

            assert 'data-scan-status="legacy_ambiguous"' in ambiguous.text
            assert self._action(ambiguous.text) == "/api/shelf-fill/scan"

            chosen = self._hidden(ambiguous.text)
            chosen["legacy_confirm_isbn13"] = KRISTY_ISBN13
            filed = editor_client.post(self._action(ambiguous.text), data=chosen)

        assert filed.status_code == 200
        assert 'data-testid="shelf-fill-position"' in filed.text
        item = db.execute(
            "SELECT id FROM items WHERE isbn = ?", (KRISTY_ISBN13,)
        ).fetchone()
        assert item is not None
        copy = db.execute(
            "SELECT position_order FROM item_copies "
            "WHERE item_id = ? AND is_primary = 1",
            (item["id"],),
        ).fetchone()
        assert copy["position_order"] == 2
        mapping = db.execute(
            "SELECT isbn13 FROM legacy_book_mappings WHERE barcode = ?",
            (KRISTY_UPC5,),
        ).fetchone()
        assert mapping["isbn13"] == KRISTY_ISBN13

    def test_shelf_fill_does_not_offer_a_manual_add_it_cannot_handle(
        self, editor_client, shelf_with_a_copy
    ):
        """diff-review codex B1.

        The only `[data-manual-add]` listener is `scan.js:122`, and
        `shelf_fill.html` loads `shelf-fill.js` instead — so a manual-add
        button rendered here is a control that does nothing when clicked.
        Shelf Fill already offers no manual add (`_render_error` passes no
        `offer_manual`); the new card must match the page it is hosted on.
        """
        card = self._scan_bare(editor_client, shelf_with_a_copy)

        assert 'data-scan-status="legacy_incomplete"' in card.text
        assert self._action(card.text) == "/api/shelf-fill/scan"
        assert "data-manual-add" not in card.text
        # The escape that does work here is still offered.
        assert "Scan the printed ISBN instead" in card.text

    def test_an_ordinary_isbn_still_places_unchanged(
        self, editor_client, db, shelf_with_a_copy
    ):
        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            metadata = {"title": "Ordinary Book", "authors": "Someone"}
            return metadata, "openlibrary", {}, provider_result.found(
                "openlibrary", metadata
            )

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ), patch("app.routers.items.cover_queue.enqueue"):
            filed = editor_client.post(
                "/api/shelf-fill/scan",
                data={
                    "isbn": "9780439136365",
                    "location_id": shelf_with_a_copy,
                    "media_type": "book",
                },
            )

        assert filed.status_code == 200
        assert 'data-testid="shelf-fill-position"' in filed.text
        item = db.execute(
            "SELECT id FROM items WHERE title = 'Ordinary Book'"
        ).fetchone()
        copy = db.execute(
            "SELECT position_order FROM item_copies "
            "WHERE item_id = ? AND is_primary = 1",
            (item["id"],),
        ).fetchone()
        assert copy["position_order"] == 2


class TestShelfFillScanPassesDefaultTags:
    """T2 — `shelf_fill_scan`'s direct call to `items.scan_isbn` must pass
    `tags=tags` by keyword (drift 2/G77): unforwarded, a direct call hands a
    `Form` object to `parse_tag_list` instead of a string."""

    def test_a_tagged_scan_lands_tagged_and_placed(self, editor_client, db):
        shelf = location_svc.create_location(db, "Shelf")
        db.commit()

        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            metadata = {"title": "Tagged Book", "authors": "Someone"}
            return metadata, "openlibrary", {}, provider_result.found(
                "openlibrary", metadata
            )

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ), patch("app.routers.items.cover_queue.enqueue"):
            filed = editor_client.post(
                "/api/shelf-fill/scan",
                data={
                    "isbn": "9780439136365",
                    "location_id": shelf,
                    "media_type": "book",
                    "tags": "Cookbook",
                },
            )

        assert filed.status_code == 200
        assert 'data-testid="shelf-fill-position"' in filed.text
        item = db.execute(
            "SELECT id FROM items WHERE title = 'Tagged Book'"
        ).fetchone()
        assert [t["name"] for t in tags_svc.get_item_tags(db, item["id"])] == ["Cookbook"]

    def test_no_tags_field_creates_no_tag_rows(self, editor_client, db):
        shelf = location_svc.create_location(db, "Shelf")
        db.commit()

        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            metadata = {"title": "Untagged Book", "authors": "Someone"}
            return metadata, "openlibrary", {}, provider_result.found(
                "openlibrary", metadata
            )

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ), patch("app.routers.items.cover_queue.enqueue"):
            filed = editor_client.post(
                "/api/shelf-fill/scan",
                data={
                    "isbn": "9780439136365",
                    "location_id": shelf,
                    "media_type": "book",
                },
            )

        assert filed.status_code == 200
        assert db.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
