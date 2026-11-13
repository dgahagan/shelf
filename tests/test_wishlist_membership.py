"""Issue #125: wishlist membership is its own state, not derived from `owned`.

The write funnel (`app/services/item_write.py`) accepts a virtual
`wishlisted: bool` field and writes `list_items` membership through
`app/services/lists.py`. Each test here drives one such writer end to end
and asserts the membership it should leave — `owned = 1` is never a member
(`_assert_ownership_partition(db)`, the fixture-sanity check from
`tests/conftest.py`), but `owned = 0` with no membership is a legal
"neither" state and no test here should expect the reverse.

These are the sites the plan's recon found with **no** wishlist-mode pin
at all before wishlist membership existed as its own concept: the film UPC
branch, the store's unreadable and bare-fallback rows, periodicals confirm,
and music add. Everywhere else that already had a wishlist-mode test gained
one line — see test_scan_modes.py, test_upc_manual_add.py,
test_scan_upc_enrichment.py, test_store.py and test_intake.py.
"""

from unittest.mock import AsyncMock, patch

from app.routers import hardcover as hc_router
from app.services import (
    komga_records,
    lists,
    musicbrainz,
    provider_result,
    romm_records,
    tmdb,
    upcitemdb,
)
from tests.conftest import _assert_ownership_partition, _insert_item
from tests.test_komga_records import _candidate as _komga_candidate
from tests.test_reading_imports import GOODREADS_HEADER, _gr_row, _post_csv
from tests.test_romm_records import _candidate as _romm_candidate

DVD_UPC = "085391163121"
GOODFELLAS = (
    "Goodfellas [DVD]  Feature Thriller Drama  Action  Suspense  Drama  "
    "Crime  Drama Drama"
)

# ISSN 0161-7370 (Popular Science) encoded as a 977 EAN — a valid periodical
# barcode, per tests/test_periodicals.py.
POPULAR_SCIENCE_EAN = "9770161737008"

_MUSIC_RELEASE = {
    "title": "The Dark Side of the Moon",
    "artist_credit": "Pink Floyd",
    "musicbrainz_release_id": "11111111-1111-1111-1111-111111111111",
    "musicbrainz_release_group_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "release_type": "Album",
    "release_status": "Official",
    "release_date": "1973-03-23",
    "first_release_date": "1973-03-01",
    "country": "GB",
    "label": "Harvest",
    "catalog_number": "SHVL 804",
    "barcode": None,
    "packaging": "Gatefold Cover",
    "media_count": 1,
    "format_summary": "12\" Vinyl",
    "source": "musicbrainz",
    "media": [
        {
            "position": 1,
            "format": "12\" Vinyl",
            "title": None,
            "track_count": 1,
            "tracks": [
                {
                    "position": 1,
                    "number": "A1",
                    "title": "Speak to Me",
                    "artist_credit": "Pink Floyd",
                    "duration_ms": 65000,
                    "musicbrainz_recording_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                },
            ],
        }
    ],
}


def _set_tmdb_key(monkeypatch, key="0123456789abcdef0123456789abcdef"):
    monkeypatch.setenv("TMDB_API_KEY", key)


class TestFilmUpcWishlistMode:
    """`_scan_upc` (items_common.py) — the DVD/disc UPC branch, wishlist mode.

    Mocked the way TestGameScanHonoursWishlistMode mocks the game UPC branch
    in tests/test_scan_upc_enrichment.py:277 — here the film provider (TMDb)
    stands in for IGDB.
    """

    def test_wishlist_mode_stores_unowned_and_is_on_the_wishlist(
        self, editor_client, db, monkeypatch
    ):
        async def _lookup(upc, client):
            return provider_result.found(
                "upcitemdb", {"title": GOODFELLAS, "category": None, "brand": None, "images": []}
            )
        monkeypatch.setattr(upcitemdb, "lookup", _lookup)

        async def _lookup_by_title(query, key, client):
            return provider_result.no_match("tmdb")
        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post(
            "/api/scan",
            data={"isbn": DVD_UPC, "media_type": "dvd", "mode": "wishlist"},
        )

        assert resp.status_code == 200
        row = db.execute("SELECT * FROM items WHERE upc IS NOT NULL").fetchone()
        assert row["owned"] == 0
        _assert_ownership_partition(db)
        assert "wishlisted" in resp.text.lower()


class TestStoreQueueWishlistMembership:
    """store.py — the unreadable-barcode row and the bare-fallback row.

    Both are `insert_item(..., owned=0, ...)` sites that never went through
    a follow-up UPDATE, so a missing `wishlisted=True` here would silently
    create an owned=0 row with no wishlist membership — the exact bug the
    per-writer pins exist to catch.
    """

    def test_unreadable_barcode_row_is_on_the_wishlist(self, admin_client, db):
        resp = admin_client.post("/api/store/queue", json={"isbns": ["not-an-isbn"]})
        result = resp.json()["results"][0]
        assert result["status"] == "unreadable"

        row = db.execute("SELECT owned FROM items WHERE id = ?", (result["item_id"],)).fetchone()
        assert row["owned"] == 0
        _assert_ownership_partition(db)

    def test_bare_fallback_row_is_on_the_wishlist(self, admin_client, db):
        with patch("app.routers.items_common._lookup_metadata",
                   new=AsyncMock(return_value=(None, None, {}, False))):
            resp = admin_client.post("/api/store/queue", json={"isbns": ["9789000000111"]})
        result = resp.json()["results"][0]
        assert result["status"] == "added_bare"

        row = db.execute("SELECT owned FROM items WHERE id = ?", (result["item_id"],)).fetchone()
        assert row["owned"] == 0
        _assert_ownership_partition(db)


class TestPeriodicalsConfirmWishlistMode:
    """periodicals.py — /api/periodicals/confirm with mode=wishlist."""

    def test_wishlist_mode_stores_unowned_and_is_on_the_wishlist(self, editor_client, db):
        resp = editor_client.post(
            "/api/periodicals/confirm",
            data={
                "raw_barcode": POPULAR_SCIENCE_EAN,
                "publication_title": "Popular Science",
                "mode": "wishlist",
            },
        )
        assert resp.status_code in (200, 303)

        row = db.execute(
            "SELECT owned FROM items WHERE title LIKE 'Popular Science%'"
        ).fetchone()
        assert row is not None
        assert row["owned"] == 0
        _assert_ownership_partition(db)


class TestMusicAddWishlistMode:
    """music.py — /api/music/add with owned=0."""

    def test_owned_zero_stores_unowned_and_is_on_the_wishlist(
        self, editor_client, db, monkeypatch
    ):
        async def _lookup_release(release_id, client):
            return provider_result.found("musicbrainz", _MUSIC_RELEASE)
        monkeypatch.setattr(musicbrainz, "lookup_release", _lookup_release)

        resp = editor_client.post(
            "/api/music/add",
            data={
                "release_id": _MUSIC_RELEASE["musicbrainz_release_id"],
                "media_type": "vinyl",
                "owned": "0",
            },
        )
        assert resp.status_code in (200, 303)

        row = db.execute(
            "SELECT owned FROM items WHERE title = ?", (_MUSIC_RELEASE["title"],)
        ).fetchone()
        assert row is not None
        assert row["owned"] == 0
        _assert_ownership_partition(db)


class TestHardcoverWishlistMembership:
    """hardcover.py — add-to-shelf and library sync (T6)."""

    def test_add_to_shelf_is_a_member(self, editor_client, db):
        resp = editor_client.post(
            "/api/hardcover/add-to-shelf",
            json={"title": "Add To Shelf Wishlist"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True

        row = db.execute("SELECT owned FROM items WHERE id = ?", (body["item_id"],)).fetchone()
        assert row["owned"] == 0
        assert lists.is_member(db, lists.WISHLIST, body["item_id"])
        _assert_ownership_partition(db)

    def test_sync_want_to_read_book_is_a_member(self, db):
        result, _cover_job = hc_router._import_single_book_metadata(
            {"title": "Sync Want To Read", "reading_status": "want_to_read"},
            overwrite=False, title_index={},
        )
        assert result == "added"

        item = db.execute(
            "SELECT id, owned FROM items WHERE title = ?", ("Sync Want To Read",)
        ).fetchone()
        assert item["owned"] == 0
        assert lists.is_member(db, lists.WISHLIST, item["id"])
        _assert_ownership_partition(db)

    def test_sync_read_book_is_not_a_member(self, db):
        result, _cover_job = hc_router._import_single_book_metadata(
            {"title": "Sync Already Read", "reading_status": "read"},
            overwrite=False, title_index={},
        )
        assert result == "added"

        item = db.execute(
            "SELECT id, owned FROM items WHERE title = ?", ("Sync Already Read",)
        ).fetchone()
        assert item["owned"] == 1
        assert not lists.is_member(db, lists.WISHLIST, item["id"])
        _assert_ownership_partition(db)


class TestCsvImportWishlistMembership:
    """items_csv.py — new-row insert and the tracker-update path (T6)."""

    def test_new_row_with_to_read_wishlist_is_a_member(self, admin_client, db):
        csv_content = GOODREADS_HEADER + "\n" + _gr_row(
            title="CSV Wishlist New", isbn13="9780553283686", isbn10="0553283685",
            shelf="to-read", date_read="")
        data = _post_csv(admin_client, csv_content, to_read_wishlist="1").json()
        assert data["imported"] == 1

        item = db.execute(
            "SELECT id, owned FROM items WHERE isbn = '9780553283686'"
        ).fetchone()
        assert item["owned"] == 0
        assert lists.is_member(db, lists.WISHLIST, item["id"])
        _assert_ownership_partition(db)

    def test_tracker_update_to_unowned_is_neither(self, admin_client, db):
        """With the to-read option off, a zero-owned tracker row is neither
        (#125) — un-owning no longer implies wishing."""
        item_id = _insert_item(
            db, title="Tracker To Unowned", isbn="9780553283686", media_type="book", owned=1
        )
        db.execute("COMMIT")

        csv_content = GOODREADS_HEADER + "\n" + _gr_row(
            title="Tracker To Unowned", isbn13="9780553283686", isbn10="0553283685",
            owned_copies="0")
        data = _post_csv(admin_client, csv_content, mode="update").json()
        assert data["imported"] == 1

        row = db.execute("SELECT owned FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["owned"] == 0
        assert not lists.is_member(db, lists.WISHLIST, item_id)
        _assert_ownership_partition(db)

    def test_tracker_update_to_owned_removes_membership(self, admin_client, db):
        item_id = _insert_item(
            db, title="Tracker To Owned", isbn="9780553283686", media_type="book", owned=0,
            wishlisted=True,
        )
        db.execute("COMMIT")

        csv_content = GOODREADS_HEADER + "\n" + _gr_row(
            title="Tracker To Owned", isbn13="9780553283686", isbn10="0553283685",
            owned_copies="1")
        data = _post_csv(admin_client, csv_content, mode="update").json()
        assert data["imported"] == 1

        row = db.execute("SELECT owned FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["owned"] == 1
        assert not lists.is_member(db, lists.WISHLIST, item_id)
        _assert_ownership_partition(db)


class TestEditFormWishlistMembership:
    """items.py's edit form — two independent checkboxes, owned and wishlisted
    (#125 plan 2). The only coupling left is that owned = 1 is never a member.
    """

    def _post(self, client, item_id, data):
        resp = client.post(f"/api/items/{item_id}", data=data, follow_redirects=False)
        return resp

    def _state(self, db, item_id):
        row = db.execute("SELECT owned FROM items WHERE id = ?", (item_id,)).fetchone()
        return row["owned"], lists.is_member(db, lists.WISHLIST, item_id)

    def test_owned_one_to_zero_with_wishlist_unchecked_is_neither(self, editor_client, db):
        item_id = _insert_item(db, title="Edit To Neither", isbn="9780000000026", owned=1)
        db.commit()

        resp = self._post(editor_client, item_id, {"owned": "0", "wishlisted": "0"})
        assert resp.status_code == 303
        assert resp.headers["location"] == f"/item/{item_id}"

        assert self._state(db, item_id) == (0, False)
        _assert_ownership_partition(db)

    def test_owned_zero_alone_leaves_membership_alone(self, editor_client, db):
        """An absent `wishlisted` key means "leave membership alone" (G87):
        un-owning no longer implies wishing."""
        item_id = _insert_item(db, title="Unown Only", isbn="9780000000026", owned=1)
        db.commit()

        resp = self._post(editor_client, item_id, {"owned": "0"})
        assert resp.status_code == 303

        assert self._state(db, item_id) == (0, False)
        _assert_ownership_partition(db)

    def test_owned_zero_with_wishlist_checked_is_a_member(self, editor_client, db):
        item_id = _insert_item(db, title="Edit To Wishlist", isbn="9780000000026", owned=1)
        db.commit()

        resp = self._post(editor_client, item_id, {"owned": "0", "wishlisted": "1"})
        assert resp.status_code == 303

        assert self._state(db, item_id) == (0, True)
        _assert_ownership_partition(db)

    def test_unchecking_the_wishlist_removes_membership_and_keeps_unowned(self, editor_client, db):
        item_id = _insert_item(db, title="Edit Off Wishlist", isbn="9780000000026", owned=0,
                               wishlisted=True)
        db.commit()

        resp = self._post(editor_client, item_id, {"owned": "0", "wishlisted": "0"})
        assert resp.status_code == 303

        assert self._state(db, item_id) == (0, False)
        _assert_ownership_partition(db)

    def test_wishlisted_alone_no_longer_flips_owned(self, editor_client, db):
        item_id = _insert_item(db, title="Neither To Wishlist", isbn="9780000000026", owned=0)
        db.commit()

        resp = self._post(editor_client, item_id, {"wishlisted": "1"})
        assert resp.status_code == 303

        assert self._state(db, item_id) == (0, True)
        _assert_ownership_partition(db)

    def test_owned_and_wishlisted_together_is_refused(self, editor_client, db):
        item_id = _insert_item(db, title="Both Refused", isbn="9780000000026", owned=0,
                               wishlisted=True)
        db.commit()

        resp = self._post(editor_client, item_id,
                          {"owned": "1", "wishlisted": "1", "title": "Changed"})
        assert resp.status_code == 303
        assert resp.headers["location"] == f"/item/{item_id}/edit?error=invalid_wishlisted"

        assert self._state(db, item_id) == (0, True)
        title = db.execute("SELECT title FROM items WHERE id = ?", (item_id,)).fetchone()["title"]
        assert title == "Both Refused"
        banner = editor_client.get(resp.headers["location"]).text
        assert 'data-testid="edit-error"' in banner
        assert "can't also be on your wishlist" in banner
        _assert_ownership_partition(db)

    def test_owned_zero_to_one_removes_membership(self, editor_client, db):
        item_id = _insert_item(db, title="Edit To Owned", isbn="9780000000026", owned=0,
                                wishlisted=True)
        db.commit()

        # The browser posts wishlisted=0 here: the box is disabled once owned
        # is checked, so only the hidden input submits (G90).
        resp = self._post(editor_client, item_id, {"owned": "1", "wishlisted": "0"})
        assert resp.status_code == 303

        assert self._state(db, item_id) == (1, False)
        _assert_ownership_partition(db)

    def test_owned_one_alone_removes_membership(self, editor_client, db):
        item_id = _insert_item(db, title="Edit To Owned Alone", isbn="9780000000026", owned=0,
                                wishlisted=True)
        db.commit()

        resp = self._post(editor_client, item_id, {"owned": "1"})
        assert resp.status_code == 303

        assert self._state(db, item_id) == (1, False)
        _assert_ownership_partition(db)


class TestBulkEditWishlistMembership:
    """items.py's /api/items/bulk-update — the owned and wishlisted fields
    (T3). `wishlisted` is a submitted key passed straight to the funnel, not
    derived from `owned` — see `app/services/item_write.py::_pop_wishlisted`
    and `_refuse_owned_wishlist`."""

    def _three_owned_items(self, db):
        ids = [
            _insert_item(db, title=f"Bulk Owned {i}", isbn=f"978000000{i:04d}", owned=1)
            for i in range(3)
        ]
        db.commit()
        return ids

    def test_bulk_owned_zero_leaves_them_neither(self, admin_client, db):
        """Bulk `{"owned": 0}` alone writes no membership — a neither row,
        not an implicit wishlist add (issue #125 replaces the old
        `wishlisted = (owned == 0)` derivation)."""
        ids = self._three_owned_items(db)

        resp = admin_client.post(
            "/api/items/bulk-update",
            json={"item_ids": ids, "updates": {"owned": 0}},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        for item_id in ids:
            row = db.execute("SELECT owned FROM items WHERE id = ?", (item_id,)).fetchone()
            assert row["owned"] == 0
            assert not lists.is_member(db, lists.WISHLIST, item_id)
        _assert_ownership_partition(db)

    def test_bulk_owned_one_removes_all_membership(self, admin_client, db):
        ids = [
            _insert_item(db, title=f"Bulk Unowned {i}", isbn=f"978000001{i:04d}", owned=0,
                         wishlisted=True)
            for i in range(3)
        ]
        db.commit()

        resp = admin_client.post(
            "/api/items/bulk-update",
            json={"item_ids": ids, "updates": {"owned": 1}},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        for item_id in ids:
            row = db.execute("SELECT owned FROM items WHERE id = ?", (item_id,)).fetchone()
            assert row["owned"] == 1
            assert not lists.is_member(db, lists.WISHLIST, item_id)
        _assert_ownership_partition(db)

    def test_bulk_invalid_owned_is_still_refused(self, admin_client, db):
        ids = self._three_owned_items(db)

        resp = admin_client.post(
            "/api/items/bulk-update",
            json={"item_ids": ids, "updates": {"owned": "2"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["message"] == "Owned must be 0 or 1"

        for item_id in ids:
            row = db.execute("SELECT owned FROM items WHERE id = ?", (item_id,)).fetchone()
            assert row["owned"] == 1
        _assert_ownership_partition(db)

    def test_bulk_wishlisted_false_removes_membership(self, admin_client, db):
        isbns = ["9780000003003", "9780000003010", "9780000003027"]
        ids = [
            _insert_item(db, title=f"Bulk Member {i}", isbn=isbn, owned=0, wishlisted=True)
            for i, isbn in enumerate(isbns)
        ]
        db.commit()

        resp = admin_client.post(
            "/api/items/bulk-update",
            json={"item_ids": ids, "updates": {"wishlisted": False}},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        for item_id in ids:
            row = db.execute("SELECT owned FROM items WHERE id = ?", (item_id,)).fetchone()
            assert row["owned"] == 0
            assert not lists.is_member(db, lists.WISHLIST, item_id)
        _assert_ownership_partition(db)

    def test_bulk_wishlisted_true_over_neither_rows_makes_members(self, admin_client, db):
        isbns = ["9780000003034", "9780000003041", "9780000003058"]
        ids = [
            _insert_item(db, title=f"Bulk Neither {i}", isbn=isbn, owned=0)
            for i, isbn in enumerate(isbns)
        ]
        db.commit()

        resp = admin_client.post(
            "/api/items/bulk-update",
            json={"item_ids": ids, "updates": {"wishlisted": True}},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        for item_id in ids:
            row = db.execute("SELECT owned FROM items WHERE id = ?", (item_id,)).fetchone()
            assert row["owned"] == 0
            assert lists.is_member(db, lists.WISHLIST, item_id)
        _assert_ownership_partition(db)

    def test_bulk_owned_and_wishlisted_together_is_refused(self, admin_client, db):
        ids = self._three_owned_items(db)

        resp = admin_client.post(
            "/api/items/bulk-update",
            json={"item_ids": ids, "updates": {"owned": 1, "wishlisted": True}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False

        for item_id in ids:
            row = db.execute("SELECT owned FROM items WHERE id = ?", (item_id,)).fetchone()
            assert row["owned"] == 1
            assert not lists.is_member(db, lists.WISHLIST, item_id)
        _assert_ownership_partition(db)

    def test_bulk_wishlisted_true_over_mixed_selection_refuses_whole(self, admin_client, db):
        neither_id_1 = _insert_item(db, title="Mixed Neither 1",
                                     isbn="9780000003096", owned=0)
        owned_id = _insert_item(db, title="Mixed Owned",
                                 isbn="9780000003102", owned=1)
        neither_id_2 = _insert_item(db, title="Mixed Neither 2",
                                     isbn="9780000003119", owned=0)
        db.commit()
        ids = [neither_id_1, owned_id, neither_id_2]

        resp = admin_client.post(
            "/api/items/bulk-update",
            json={"item_ids": ids, "updates": {"wishlisted": True}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False

        for item_id in ids:
            assert not lists.is_member(db, lists.WISHLIST, item_id)
        assert db.execute(
            "SELECT owned FROM items WHERE id = ?", (owned_id,)
        ).fetchone()["owned"] == 1
        for item_id in (neither_id_1, neither_id_2):
            assert db.execute(
                "SELECT owned FROM items WHERE id = ?", (item_id,)
            ).fetchone()["owned"] == 0
        _assert_ownership_partition(db)


class TestServiceBackedInsertsAreNeverMembers:
    """RomM (`romm_records.py`) and Komga (`komga_records.py`) insert with
    `owned = 1` through `insert_item` and never touch ownership again — they
    never pass `wishlisted`, so `_apply_membership` must write nothing, and
    the row must never become a wishlist member (T7)."""

    def test_romm_insert_is_not_a_member(self, db):
        result = romm_records.persist_candidate(db, _romm_candidate())

        row = db.execute(
            "SELECT owned FROM items WHERE id = ?", (result["item_id"],)
        ).fetchone()
        assert row["owned"] == 1
        assert not lists.is_member(db, lists.WISHLIST, result["item_id"])
        _assert_ownership_partition(db)

    def test_komga_insert_is_not_a_member(self, db):
        result = komga_records.persist_candidate(db, _komga_candidate())

        row = db.execute(
            "SELECT owned FROM items WHERE id = ?", (result["item_id"],)
        ).fetchone()
        assert row["owned"] == 1
        assert not lists.is_member(db, lists.WISHLIST, result["item_id"])
        _assert_ownership_partition(db)
