"""A person re-adding something they deleted gets it back, and is told so.

Sites 1–4 of the plan's call-site table: the scan-ISBN path, `_scan_upc`,
`_scan_upc_game` and `manual_add`. Sites 5–10 (T8): the catalogue game/DVD/
book adds, the two Photo Intake insert paths and the Hardcover add-to-shelf
endpoint. Each seeds a row, trashes it through `item_write.trash_item` — no
route writes `deleted_at` in this release — and commits **before** the
request, because the route opens its own connection and an uncommitted seed
is both a deadlock and a vacuous pin (G48).

`restored` is an OK-class status reaching five consumers, three of which
render an unlisted status as an error (G62). The pins below cover the server
side and the persisted `recent_scans` fragment; `tests/e2e/test_scan.py`
carries the browser-side vocabulary.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from app.routers import items_catalog
from app.services import item_write
from app.services.item_write import insert_item, trash_item

ISBN = "9780441013593"
UPC = "012569803121"


def _row(db, item_id, *cols):
    return db.execute(
        f"SELECT {', '.join(cols)} FROM items WHERE id = ?", (item_id,)
    ).fetchone()


def _last_scan(db):
    return db.execute(
        "SELECT result, item_id FROM scan_log ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _provider_says(title="Provider Spelling", authors="P. Rovider"):
    """Stub the metadata cascade with a DIFFERENT title from the stored one.

    The cascade really does run on a re-add: the route's early duplicate
    guards read `items_live` and correctly miss a trashed row, so the lookup
    happens and the funnel resolves the collision afterwards. That is the
    design's accepted cost, and it is what makes these pins meaningful —
    the provider's spelling is on the table, and the card must still show
    the row's own stored title.
    """
    from app.services import provider_result

    async def _lookup(isbn13, hc_token, client, *, google_api_key=None):
        meta = {"title": title, "authors": authors}
        return meta, "openlibrary", {}, provider_result.found(
            "openlibrary", meta)

    return patch("app.routers.items_common._lookup_metadata", new=_lookup)


class TestScanIsbnRestores:
    """Site 1 — `_save_item` via `POST /api/scan`."""

    def test_a_trashed_isbn_scanned_again_is_restored_and_says_so(
        self, admin_client, db
    ):
        item_id = insert_item(db, title="Stored Title", source="manual",
                              isbn=ISBN, media_type="book", authors="A. Writer")
        trash_item(db, item_id)
        db.commit()

        with _provider_says():
            resp = admin_client.post("/api/scan", data={
                "isbn": ISBN, "media_type": "book", "mode": "add"})

        assert resp.status_code == 200
        assert 'data-scan-status="restored"' in resp.text
        # The row is live again, same id, and the card shows the STORED
        # title — not a provider spelling, which the funnel never wrote.
        assert _row(db, item_id, "deleted_at")["deleted_at"] is None
        assert "Stored Title" in resp.text
        assert "Restored from Trash" in resp.text

    def test_the_scan_log_records_restored(self, admin_client, db):
        item_id = insert_item(db, title="Logged", source="manual",
                              isbn=ISBN, media_type="book")
        trash_item(db, item_id)
        db.commit()

        with _provider_says():
            admin_client.post("/api/scan", data={
                "isbn": ISBN, "media_type": "book", "mode": "add"})

        last = _last_scan(db)
        assert last["result"] == "restored"
        assert last["item_id"] == item_id

    def test_add_mode_over_a_trashed_wishlist_row_ends_owned(
        self, admin_client, db
    ):
        item_id = insert_item(db, title="Wanted", source="manual", isbn=ISBN,
                              media_type="book", owned=0, wishlisted=True)
        trash_item(db, item_id)
        db.commit()

        with _provider_says():
            resp = admin_client.post("/api/scan", data={
                "isbn": ISBN, "media_type": "book", "mode": "add"})

        assert 'data-scan-status="restored"' in resp.text
        assert _row(db, item_id, "owned")["owned"] == 1

    def test_wishlist_mode_over_a_trashed_owned_row_stays_owned(
        self, admin_client, db
    ):
        """Ownership only ever moves toward owned (G100). This is the pin
        `claude-R1` showed would red at site 4 if `manual_add` kept its
        second-transaction demote."""
        item_id = insert_item(db, title="Owned", source="manual", isbn=ISBN,
                              media_type="book", owned=1)
        trash_item(db, item_id)
        db.commit()

        with _provider_says():
            admin_client.post("/api/scan", data={
                "isbn": ISBN, "media_type": "book", "mode": "wishlist"})

        assert _row(db, item_id, "owned")["owned"] == 1

    def test_an_existing_cover_is_not_replaced(self, admin_client, db):
        """The scan path enqueues rather than downloading, and
        `resolve_missing_cover` returns early on a row that has a cover — so
        the stored cover survives with no branch on this path at all."""
        item_id = insert_item(db, title="Covered", source="manual", isbn=ISBN,
                              media_type="book")
        db.execute("UPDATE items SET cover_path = 'covers/mine.jpg' "
                   "WHERE id = ?", (item_id,))
        trash_item(db, item_id)
        db.commit()

        with _provider_says():
            admin_client.post("/api/scan", data={
                "isbn": ISBN, "media_type": "book", "mode": "add"})

        assert _row(db, item_id, "cover_path")["cover_path"] == "covers/mine.jpg"


class TestScanUpcRestores:
    """Sites 2 and 3 — `_scan_upc` (film) and `_scan_upc_game`, each with its
    own `restored_status` projection. No provider is configured, so each
    files the cleaned UPC Item DB title — a spelling the stored row does not
    carry, which the card must not show (`codex-M3`)."""

    DVD_UPC = "085391163121"
    GAME_UPC = "045496590741"

    @pytest.fixture(autouse=True)
    def _no_providers(self, monkeypatch):
        from app.services import igdb, provider_result, upcitemdb

        async def _lookup(upc, client):
            return provider_result.found("upcitemdb", {
                "title": self.retail_title, "category": None,
                "brand": None, "images": []})

        async def _no_game(*a, **k):
            return provider_result.no_match("igdb")

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)
        monkeypatch.setattr(igdb, "search_games", _no_game)
        monkeypatch.delenv("TMDB_API_KEY", raising=False)
        monkeypatch.setenv("IGDB_CLIENT_ID", "cid")
        monkeypatch.setenv("IGDB_CLIENT_SECRET", "secret")

    def _restore_by_scan(self, admin_client, db, upc, media_type):
        from app.services import upc as upc_svc

        item_id = insert_item(db, title="Stored Title", source="manual",
                              upc=upc_svc.normalize_upc(upc),
                              media_type=media_type)
        trash_item(db, item_id)
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": upc, "media_type": media_type, "mode": "add"})
        return item_id, resp

    def _assert_restored(self, db, item_id, resp):
        assert resp.status_code == 200
        assert 'data-scan-status="restored"' in resp.text
        assert "Stored Title" in resp.text
        assert "Provider Spelling" not in resp.text
        assert _row(db, item_id, "deleted_at")["deleted_at"] is None
        last = _last_scan(db)
        assert (last["result"], last["item_id"]) == ("restored", item_id)

    def test_a_trashed_disc_scanned_again_is_restored_and_says_so(
        self, admin_client, db
    ):
        self.retail_title = "Provider Spelling [DVD]"
        item_id, resp = self._restore_by_scan(
            admin_client, db, self.DVD_UPC, "dvd")
        self._assert_restored(db, item_id, resp)

    def test_a_trashed_game_scanned_again_is_restored_and_says_so(
        self, admin_client, db
    ):
        self.retail_title = "Provider Spelling - Nintendo Switch"
        item_id, resp = self._restore_by_scan(
            admin_client, db, self.GAME_UPC, "video_game")
        self._assert_restored(db, item_id, resp)


class TestManualAddKeepsARestoredCover:
    """Site 4 — `manual_add` downloads inline, so it needs
    `keeps_stored_cover` itself. Both the scan-preview rename and the
    download write `<item_id>.jpg`, which is the user's own file."""

    def _seed_covered(self, db):
        item_id = insert_item(db, title="Covered", source="manual", isbn=ISBN,
                              media_type="book")
        db.execute("UPDATE items SET cover_path = 'covers/mine.jpg' "
                   "WHERE id = ?", (item_id,))
        trash_item(db, item_id)
        db.commit()
        return item_id

    def test_no_preview_is_consumed_and_no_download_runs(
        self, editor_client, db, monkeypatch
    ):
        from app.services import covers
        item_id = self._seed_covered(db)
        preview = covers.COVERS_DIR / f"preview_{ISBN}.jpg"
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_bytes(b"preview")
        download = AsyncMock(return_value="covers/theirs.jpg")
        monkeypatch.setattr(covers, "download_cover", download)

        resp = editor_client.post("/api/items/manual", data={
            "title": "Covered", "isbn": ISBN, "media_type": "book"})

        assert 'data-scan-status="restored"' in resp.text
        assert _row(db, item_id, "cover_path")["cover_path"] == "covers/mine.jpg"
        assert preview.exists()
        assert not (covers.COVERS_DIR / f"{item_id}.jpg").exists()
        download.assert_not_awaited()

    def test_an_explicit_upload_still_wins(self, editor_client, db):
        item_id = self._seed_covered(db)

        resp = editor_client.post(
            "/api/items/manual",
            data={"title": "Covered", "isbn": ISBN, "media_type": "book"},
            files={"cover": ("c.jpg", b"\xff\xd8\xff" + b"x" * 200, "image/jpeg")},
        )

        assert 'data-scan-status="restored"' in resp.text
        assert _row(db, item_id, "cover_path")["cover_path"] == f"covers/{item_id}.jpg"


class TestStoreQueueKeepsARestoredCover:
    """`POST /api/store/queue` downloads inline too. Its download sits in a
    `try/except Exception`, so a raising stub would be swallowed — the pin
    asserts the mock was never awaited instead."""

    def test_a_restored_row_with_a_cover_makes_no_outbound_cover_call(
        self, admin_client, db
    ):
        item_id = insert_item(db, title="Covered", source="manual", isbn=ISBN,
                              media_type="book")
        db.execute("UPDATE items SET cover_path = 'covers/mine.jpg' "
                   "WHERE id = ?", (item_id,))
        trash_item(db, item_id)
        db.commit()
        download = AsyncMock(return_value="covers/theirs.jpg")

        with _provider_says(), \
             patch("app.routers.store.covers.download_cover", new=download):
            resp = admin_client.post("/api/store/queue", json={"isbns": [ISBN]})

        assert resp.json()["results"][0]["status"] == "restored"
        download.assert_not_awaited()
        assert _row(db, item_id, "cover_path")["cover_path"] == "covers/mine.jpg"


class TestTheRecentScansFragmentRendersItAsSuccess:
    """`recent_scans.html` is **persisted** — its class chain reads the stored
    `scan_log.result`, so a status missing from the success tuple stays red
    forever, on every future page load (G62)."""

    def test_a_restored_scan_is_not_rendered_as_an_error(self, admin_client, db):
        item_id = insert_item(db, title="Recent", source="manual", isbn=ISBN,
                              media_type="book")
        trash_item(db, item_id)
        db.commit()

        with _provider_says():
            admin_client.post("/api/scan", data={
                "isbn": ISBN, "media_type": "book", "mode": "add"})

        # The fragment itself, which is what a later page load renders from
        # the persisted scan_log row.
        resp = admin_client.get("/api/recent-scans?mode=add")
        assert resp.status_code == 200
        assert "restored" in resp.text
        # The badge span carrying the status also carries its class group.
        marker = resp.text[max(0, resp.text.rindex("restored") - 400):
                           resp.text.rindex("restored") + 20]
        assert "text-shelf-success" in marker, (
            "a status missing from recent_scans.html's success tuple renders "
            "red forever — the row is persisted"
        )
        assert "text-shelf-error" not in marker


class TestTheCatalogueSitesCannotRestore:
    """Sites 5, 6 and 12 in the table: an insert carrying neither an ISBN nor
    a UPC cannot reach the funnel's lookup, so it must create a new row and
    must not raise. Pinned so nobody later writes a restore pin that could
    never go red."""

    def test_a_trashed_same_title_row_does_not_block_a_new_one(self, db):
        original = insert_item(db, title="Same Name", source="test",
                               media_type="video_game")
        trash_item(db, original)

        fresh = insert_item(db, title="Same Name", source="test",
                            media_type="video_game")

        assert fresh != original
        assert item_write.was_restored(fresh) is False


class TestShelfFillHandlesTheStatus:
    """`shelf_fill.py` post-processes `scan_isbn`'s status in **Python**
    (`status in {...}`), so an unlisted status falls through the position
    assignment, the OOB summary refresh and `_render_result` entirely —
    G103's shape, one layer in from the template (`claude-R2`)."""

    def test_restored_is_in_the_post_processing_set(self):
        import re
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1]
               / "app" / "routers" / "shelf_fill.py").read_text()
        # The gate that guards the post-processing block, not the earlier
        # legacy-status check — match on the one that names "added".
        gates = [m.group(1) for m in re.finditer(r'status in \{([^}]*)\}', src)]
        match = next((g for g in gates if '"added"' in g), None)
        assert match, "shelf_fill's post-processing gate moved — re-aim this pin"
        assert "restored" in match, (
            "a status shelf_fill does not list falls through its whole "
            "post-processing block, silently"
        )
        assert "promoted" in match, (
            "a promoted wishlist scan must be placed, not dropped (codex-R1)"
        )


class TestCatalogueGameAndDvdCannotRestore:
    """Sites 5 and 6, through the actual routes (the funnel-level pin lives
    above in `TestTheCatalogueSitesCannotRestore`). Neither insert carries an
    ISBN or a UPC, so the funnel's restore lookup never runs — a trashed
    same-title row must not block, and must not raise, a fresh add."""

    def test_a_trashed_same_title_game_does_not_block_a_new_add(
        self, editor_client, db, monkeypatch
    ):
        monkeypatch.setattr(items_catalog, "get_setting", lambda db, key: "configured")
        original = insert_item(db, title="Same Game", source="test", media_type="video_game")
        trash_item(db, original)
        db.commit()

        monkeypatch.setattr(
            items_catalog.igdb, "lookup_game",
            AsyncMock(return_value={
                "title": "Same Game", "description": None, "publisher": None,
                "publish_year": None, "series_name": None, "cover_url": None,
            }),
        )

        resp = editor_client.post("/api/games/add", data={"igdb_id": "123"})

        assert resp.status_code == 200
        assert 'data-scan-status="added"' in resp.text
        rows = db.execute(
            "SELECT id FROM items WHERE title = 'Same Game' AND media_type = 'video_game'"
        ).fetchall()
        assert len(rows) == 2
        new_id = next(r["id"] for r in rows if r["id"] != original)
        assert item_write.was_restored(new_id) is False

    def test_a_trashed_same_title_dvd_does_not_block_a_new_add(self, editor_client, db):
        original = insert_item(db, title="Same DVD", source="test", media_type="dvd")
        trash_item(db, original)
        db.commit()

        resp = editor_client.post("/api/dvds/add", data={"title": "Same DVD"})

        assert resp.status_code == 200
        assert 'data-scan-status="added"' in resp.text
        rows = db.execute(
            "SELECT id FROM items WHERE title = 'Same DVD' AND media_type = 'dvd'"
        ).fetchall()
        assert len(rows) == 2
        new_id = next(r["id"] for r in rows if r["id"] != original)
        assert item_write.was_restored(new_id) is False


class TestCatalogueBookAddRestores:
    """Site 7 — `_save_item` via `POST /api/books/add`."""

    def test_a_trashed_isbn_added_again_is_restored_and_says_so(
        self, editor_client, db
    ):
        item_id = insert_item(db, title="Stored Title", source="manual",
                              isbn=ISBN, media_type="book", authors="A. Writer")
        trash_item(db, item_id)
        db.commit()

        with _provider_says():
            resp = editor_client.post(
                "/api/books/add", data={"isbn": ISBN, "media_type": "book"})

        assert resp.status_code == 200
        assert 'data-scan-status="restored"' in resp.text
        assert _row(db, item_id, "deleted_at")["deleted_at"] is None
        # The stored title, not the provider's spelling.
        assert "Stored Title" in resp.text
        assert "Restored from Trash" in resp.text

    def test_a_restored_row_with_a_cover_makes_no_outbound_cover_call(
        self, editor_client, db, monkeypatch
    ):
        item_id = insert_item(db, title="Covered", source="manual", isbn=ISBN,
                              media_type="book")
        db.execute("UPDATE items SET cover_path = 'covers/mine.jpg' WHERE id = ?",
                   (item_id,))
        trash_item(db, item_id)
        db.commit()

        async def _boom(*args, **kwargs):
            raise AssertionError(
                "download_cover must be skipped on a kept-cover restore")
        monkeypatch.setattr(items_catalog.covers, "download_cover", _boom)

        with _provider_says():
            resp = editor_client.post(
                "/api/books/add", data={"isbn": ISBN, "media_type": "book"})

        assert resp.status_code == 200
        assert _row(db, item_id, "cover_path")["cover_path"] == "covers/mine.jpg"


class TestIntakePrintedIsbnRestores:
    """Site 8 — `_save_item` via `POST /api/intake/confirm`'s printed-ISBN
    (strong) path."""

    def test_a_trashed_isbn_confirmed_again_is_restored(
        self, admin_client, db, monkeypatch
    ):
        item_id = insert_item(db, title="Stored Title", source="manual",
                              isbn=ISBN, media_type="book", authors="A. Writer")
        trash_item(db, item_id)
        db.commit()

        from app.routers import items_common
        from app.services import provider_result

        async def fake(isbn13, hc_token, client, *, google_api_key=None):
            meta = {"title": "Stored Title", "authors": "A. Writer"}
            return meta, "openlibrary", {}, provider_result.found("openlibrary", meta)

        monkeypatch.setattr(items_common, "_lookup_metadata", fake)

        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Stored Title", "isbn": ISBN}],
        })

        data = resp.json()
        assert data["ok"] is True
        assert data["skipped"] == []
        assert len(data["added"]) == 1
        assert data["added"][0]["restored"] is True
        assert _row(db, item_id, "deleted_at")["deleted_at"] is None


class TestIntakeWeakPathRestores:
    """Site 9 — the weak-path insert via `POST /api/intake/confirm`."""

    @respx.mock
    def test_a_trashed_isbn_surfaced_by_open_library_is_restored(
        self, admin_client, db
    ):
        item_id = insert_item(db, title="Weak Path Book", source="manual",
                              isbn=ISBN, media_type="book", authors="A. Writer")
        trash_item(db, item_id)
        db.commit()

        respx.get("https://openlibrary.org/search.json").mock(
            return_value=httpx.Response(200, json={"docs": [{
                "title": "Weak Path Book", "author_name": ["A. Writer"], "isbn": [ISBN],
            }]}))

        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Weak Path Book", "authors": "A. Writer"}],
        })

        data = resp.json()
        assert data["ok"] is True
        assert data["skipped"] == []
        assert len(data["added"]) == 1
        assert data["added"][0]["restored"] is True
        assert _row(db, item_id, "deleted_at")["deleted_at"] is None

    def test_a_restored_disc_row_with_a_cover_makes_no_outbound_cover_call(
        self, admin_client, db, monkeypatch
    ):
        """The one way site 9's own insert can carry both an identifier (so
        it can restore) and a cover_url (so the inline download this pin
        guards even exists): a disc/game row whose printed ISBN survives the
        book-metadata cascade's miss (6a — 'nothing has contradicted the
        printed digits') into the weak path, where the disc/game provider
        lookup separately finds a cover. `title_lookup` and `_lookup_metadata`
        are otherwise unrelated cascades; this is their one overlap."""
        from app.routers import items_common
        from app.services import covers as covers_svc, provider_result
        from tests.test_intake import _set_provider_creds, _stub_tmdb

        item_id = insert_item(db, title="Some Movie", source="manual", isbn=ISBN,
                              media_type="dvd")
        db.execute("UPDATE items SET cover_path = 'covers/mine.jpg' WHERE id = ?",
                   (item_id,))
        trash_item(db, item_id)
        db.commit()

        async def miss(isbn13, hc_token, client, *, google_api_key=None):
            return None, "manual", {}, False
        monkeypatch.setattr(items_common, "_lookup_metadata", miss)

        _set_provider_creds(monkeypatch)
        _stub_tmdb(monkeypatch, provider_result.found("tmdb", {
            "title": "Some Movie", "description": None, "publish_year": None,
            "cover_url": "https://image.tmdb.org/t/p/w500/poster.jpg",
        }))

        async def _boom(item_id, url, client):
            raise AssertionError(
                "cover download must be skipped on a kept-cover restore")
        monkeypatch.setattr(covers_svc, "_download_to_item", _boom)

        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Some Movie", "isbn": ISBN, "media_type": "dvd"}],
        })

        data = resp.json()
        assert data["ok"] is True
        assert data["skipped"] == []
        assert len(data["added"]) == 1
        assert data["added"][0]["restored"] is True
        assert _row(db, item_id, "cover_path")["cover_path"] == "covers/mine.jpg"


class TestHardcoverAddToShelfRestores:
    """Site 10 — `insert_item` via `POST /api/hardcover/add-to-shelf`."""

    def test_a_trashed_isbn_added_again_is_restored(self, editor_client, db):
        item_id = insert_item(db, title="Stored Title", source="manual",
                              isbn=ISBN, media_type="book", authors="A. Writer")
        trash_item(db, item_id)
        db.commit()

        resp = editor_client.post(
            "/api/hardcover/add-to-shelf",
            json={"title": "Provider Spelling", "isbn": ISBN},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["item_id"] == item_id
        assert data["restored"] is True
        assert _row(db, item_id, "deleted_at")["deleted_at"] is None

    def test_a_restored_row_with_a_cover_makes_no_outbound_cover_call(
        self, editor_client, db, monkeypatch
    ):
        item_id = insert_item(db, title="Covered", source="manual", isbn=ISBN,
                              media_type="book")
        db.execute("UPDATE items SET cover_path = 'covers/mine.jpg' WHERE id = ?",
                   (item_id,))
        trash_item(db, item_id)
        db.commit()

        from app.routers import hardcover as hc_router

        async def _boom(*args, **kwargs):
            raise AssertionError(
                "download_cover must be skipped on a kept-cover restore")
        monkeypatch.setattr(hc_router.covers, "download_cover", _boom)

        resp = editor_client.post(
            "/api/hardcover/add-to-shelf",
            json={"title": "Provider Spelling", "isbn": ISBN,
                  "cover_url": "https://hardcover.app/x.jpg"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["restored"] is True
        assert _row(db, item_id, "cover_path")["cover_path"] == "covers/mine.jpg"

    # The payload `components-item.js`'s series "missing books" button sends:
    # a hardcover_book_id and no isbn, so the funnel's isbn/upc key never
    # sees the trashed row.
    _MISSING_BOOK = {"title": "Provider Spelling", "authors": "P. Rovider",
                     "cover_url": None, "hardcover_book_id": 4242,
                     "series_name": "Saga", "series_position": 3}

    def _seed_by_hardcover_id(self, db, **fields):
        item_id = insert_item(db, title="Stored Title", source="hardcover",
                              media_type="book", hardcover_book_id=4242,
                              **fields)
        trash_item(db, item_id)
        db.commit()
        return item_id

    def test_a_trashed_book_found_by_hardcover_id_is_restored(
        self, editor_client, db
    ):
        item_id = self._seed_by_hardcover_id(db, owned=0)

        data = editor_client.post("/api/hardcover/add-to-shelf",
                                  json=self._MISSING_BOOK).json()

        assert data["ok"] is True
        assert data["item_id"] == item_id
        assert data["restored"] is True
        assert db.execute(
            "SELECT COUNT(*) c FROM items WHERE hardcover_book_id = 4242"
        ).fetchone()["c"] == 1
        row = _row(db, item_id, "deleted_at", "owned", "title")
        assert row["deleted_at"] is None
        assert row["title"] == "Stored Title"
        from app.services import lists
        assert db.execute(
            f"SELECT {lists.WISHLISTED_SQL} AS w FROM items i WHERE i.id = ?",
            (item_id,),
        ).fetchone()["w"] == 1

    def test_a_restored_owned_book_stays_owned(self, editor_client, db):
        item_id = self._seed_by_hardcover_id(db, owned=1)

        data = editor_client.post("/api/hardcover/add-to-shelf",
                                  json=self._MISSING_BOOK).json()

        assert data["restored"] is True
        assert _row(db, item_id, "owned")["owned"] == 1

    def test_a_live_twin_wins_over_a_trashed_one(self, editor_client, db):
        self._seed_by_hardcover_id(db, owned=0)
        live = insert_item(db, title="Live", source="hardcover",
                           media_type="book", hardcover_book_id=4242)
        db.commit()

        data = editor_client.post("/api/hardcover/add-to-shelf",
                                  json=self._MISSING_BOOK).json()

        assert data["item_id"] == live
        assert "restored" not in data


class TestMusicRestoresAtTheEarliestGuard:
    """Site 11 / halt 6. `music.py`'s first guard reads `music_releases` with
    **no items join**, so a trashed item used to send the user to
    `/music/item/<id>` — a page that bounces to Browse, because it reads
    `items_live`. The restore therefore belongs at that guard, not only in
    the funnel (G100).

    Pinned with the provider **forbidden**, so it can only pass if the early
    guard did the work: reaching the funnel would require a lookup.
    """

    RELEASE = "11111111-2222-3333-4444-555555555555"

    def _seed(self, db, *, title="Kind of Blue"):
        from app.services import music_catalog

        item_id = insert_item(db, title=title, source="musicbrainz",
                              media_type="cd")
        music_catalog.save_release(db, item_id, {
            "musicbrainz_release_id": self.RELEASE, "title": title})
        return item_id

    def test_a_trashed_release_is_restored_rather_than_bounced(
        self, admin_client, db, monkeypatch
    ):
        from app.services import musicbrainz

        item_id = self._seed(db)
        trash_item(db, item_id)
        db.commit()

        async def _forbidden(*a, **k):
            raise AssertionError(
                "the early guard must answer — reaching the provider means "
                "the guard failed to see the trashed release"
            )
        monkeypatch.setattr(musicbrainz, "lookup_release", _forbidden)

        resp = admin_client.post(
            "/api/music/add",
            data={"release_id": self.RELEASE, "media_type": "cd"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert resp.headers["location"] == f"/music/item/{item_id}?restored=1"
        assert _row(db, item_id, "deleted_at")["deleted_at"] is None

    def test_a_live_release_is_not_flagged_as_restored(
        self, admin_client, db, monkeypatch
    ):
        from app.services import musicbrainz

        item_id = self._seed(db)
        db.commit()

        async def _forbidden(*a, **k):
            raise AssertionError("no lookup for a known release")
        monkeypatch.setattr(musicbrainz, "lookup_release", _forbidden)

        resp = admin_client.post(
            "/api/music/add",
            data={"release_id": self.RELEASE, "media_type": "cd"},
            follow_redirects=False,
        )

        assert resp.headers["location"] == f"/music/item/{item_id}"

    def test_the_music_page_renders_the_notice_only_with_the_flag(
        self, admin_client, db
    ):
        item_id = self._seed(db)
        db.commit()

        with_flag = admin_client.get(f"/music/item/{item_id}?restored=1")
        without = admin_client.get(f"/music/item/{item_id}")

        assert 'data-testid="music-restored"' in with_flag.text
        assert 'data-testid="music-restored"' not in without.text


class TestMusicBarcodeRestoreKeepsItsRelease:
    """`claude-m1`. A different MusicBrainz release carrying the trashed
    row's barcode misses the early guard and restores through the funnel.
    A live twin writes nothing, so neither may a restore — unless the row
    has no release record at all (added by UPC scan), when it gets this one.
    """

    OTHER = "99999999-8888-7777-6666-555555555555"
    # Stored as the route stores it — `normalize_upc` pads to thirteen.
    BARCODE = "0" + UPC

    def _add_other_release(self, admin_client, monkeypatch):
        from app.services import musicbrainz, provider_result

        async def _lookup(release_id, client):
            return provider_result.found("musicbrainz", {
                "musicbrainz_release_id": self.OTHER, "title": "Reissue",
                "barcode": UPC, "label": "Provider Label"})
        monkeypatch.setattr(musicbrainz, "lookup_release", _lookup)
        return admin_client.post(
            "/api/music/add", data={"release_id": self.OTHER, "media_type": "cd"},
            follow_redirects=False)

    def _release_of(self, db, item_id):
        return db.execute(
            "SELECT musicbrainz_release_id, label FROM music_releases "
            "WHERE item_id = ?", (item_id,)).fetchone()

    def test_a_stored_release_record_is_not_rewritten(
        self, admin_client, db, monkeypatch
    ):
        from app.services import music_catalog

        item_id = insert_item(db, title="Kind of Blue", source="musicbrainz",
                              media_type="cd", upc=self.BARCODE)
        music_catalog.save_release(db, item_id, {
            "musicbrainz_release_id": TestMusicRestoresAtTheEarliestGuard.RELEASE,
            "title": "Kind of Blue", "label": "Stored Label"})
        trash_item(db, item_id)
        db.commit()

        resp = self._add_other_release(admin_client, monkeypatch)

        assert resp.headers["location"] == f"/music/item/{item_id}?restored=1"
        release = self._release_of(db, item_id)
        assert release["musicbrainz_release_id"] == \
            TestMusicRestoresAtTheEarliestGuard.RELEASE
        assert release["label"] == "Stored Label"

    def test_a_restored_row_without_a_release_record_gets_one(
        self, admin_client, db, monkeypatch
    ):
        item_id = insert_item(db, title="Scanned CD", source="upc_scan",
                              media_type="cd", upc=self.BARCODE)
        trash_item(db, item_id)
        db.commit()

        resp = self._add_other_release(admin_client, monkeypatch)

        assert resp.headers["location"] == f"/music/item/{item_id}?restored=1"
        assert self._release_of(db, item_id)["musicbrainz_release_id"] == self.OTHER


class TestPeriodicalConfirmRestoresATrashedIssue:
    """T9's acceptance. The periodical insert carries no isbn and no upc, so
    the earliest guard's `restore_item` is the ONLY periodical restore
    (`claude-R5`). `TestNoRouteCallsThem` proves the call exists; this
    proves it restores the row the guard found (`claude-M3`, M13)."""

    FORM = {"raw_barcode": "9770161737008",
            "publication_title": "Popular Science", "issue_number": "7"}

    def test_a_trashed_issue_confirmed_again_is_restored(
        self, editor_client, db
    ):
        first = editor_client.post("/api/periodicals/confirm", data=self.FORM,
                                   follow_redirects=False)
        item_id = db.execute("SELECT id FROM items").fetchone()["id"]
        assert first.headers["location"].endswith(f"/{item_id}")
        trash_item(db, item_id)
        db.commit()

        again = editor_client.post("/api/periodicals/confirm", data=self.FORM,
                                   follow_redirects=False)

        assert again.status_code == 303
        assert again.headers["location"] == f"/item/{item_id}"
        assert _row(db, item_id, "deleted_at")["deleted_at"] is None
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 1


class TestFindDuplicateIssueLetsLiveWin:
    """`claude-R4` / `codex-R1`. `periodical_issues` has **no unique index**
    and all three strategies are bare `LIMIT 1` with no `ORDER BY`, so a
    trashed issue can shadow a live twin that matches equally well. Without
    live-first ordering, deleting one of two equally-matching issues and
    re-scanning resurrects the deleted one and leaves the live one alone.
    """

    def _publication(self, db):
        return db.execute(
            "INSERT INTO periodical_publications (title) VALUES ('The Monthly')"
        ).lastrowid

    def _issue(self, db, publication_id, *, title, issue_number):
        from app.services import periodical_records

        item_id = insert_item(db, title=title, source="test",
                              media_type="magazine")
        periodical_records.link_issue(
            db, item_id=item_id, publication_id=publication_id,
            issue_number=issue_number)
        return item_id

    def test_a_live_issue_wins_over_a_trashed_twin(self, db):
        from app.services import periodical_records

        pub = self._publication(db)
        trashed = self._issue(db, pub, title="Issue 5 (old)", issue_number="5")
        live = self._issue(db, pub, title="Issue 5", issue_number="5")
        trash_item(db, trashed)

        hit = periodical_records.find_duplicate_issue(
            db, pub, issue_number="5")

        assert hit == live, (
            "a trashed issue shadowed the live twin — the reads must run "
            "joined to items_live first"
        )
        # And the trashed one stays trashed: it was not the row acted on.
        assert _row(db, trashed, "deleted_at")["deleted_at"] is not None

    def test_a_trashed_issue_is_still_found_when_no_live_row_matches(self, db):
        from app.services import periodical_records

        pub = self._publication(db)
        trashed = self._issue(db, pub, title="Issue 9", issue_number="9")
        trash_item(db, trashed)

        assert periodical_records.find_duplicate_issue(
            db, pub, issue_number="9") == trashed
