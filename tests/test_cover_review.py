"""The cover review queue.

G14: `app.main` is never imported at module scope here — the `client` fixtures
do that inside the isolated data dir, and a module-scope import would bind the
real one.
G48: every seed goes through the `db` fixture and is committed before the
request, or the app's own connection cannot see it and the failure reads like a
routing bug.
"""
import pytest

from tests.conftest import _insert_item, _insert_location


def _seed(db, **kwargs):
    """A cover-less item, which is what the queue is about."""
    kwargs.setdefault("cover_path", None)
    return _insert_item(db, **kwargs)


def _fake_search(db, monkeypatch, **kwargs):
    """Seed one item and make `search_covers` answer with a single candidate."""
    from app.services import covers
    from app.services import provider_result

    item_id = _seed(db, **kwargs)
    db.commit()

    async def fake_search(item, query, client, *, creds):
        return provider_result.found("Open Library", [
            {"url": "https://covers.openlibrary.org/b/id/1-L.jpg",
             "thumbnail": "https://covers.openlibrary.org/b/id/1-S.jpg",
             "source": "Open Library"},
        ])

    monkeypatch.setattr(covers, "search_covers", fake_search)
    return item_id


class TestTheQueuePredicate:
    def test_the_queue_includes_a_disc_and_a_game_with_no_isbn(self, admin_client, db):
        """The regression that separates this from Retry Missing Covers.

        That sweep is media-type-gated to COVER_REQUEUE_MEDIA_TYPES, so a DVD or
        a video game is invisible to it — deliberately, because the automatic
        chain would write a novel's ISBN onto the disc (G29). This queue shows
        them, because a human decides.
        """
        _seed(db, title="Some Film", media_type="dvd", isbn=None)
        _seed(db, title="Some Game", media_type="video_game", isbn=None)
        db.commit()

        r = admin_client.get("/cover-review")
        assert r.status_code == 200
        assert "of 2" in r.text
        # The first card is one of the two, and neither is filtered out.
        r2 = admin_client.get("/api/covers/review/next?after=1&pos=1&total=2")
        assert r2.status_code == 200

    def test_an_item_with_a_cover_is_not_in_the_queue(self, admin_client, db):
        _seed(db, title="Has A Cover", cover_path="covers/1.jpg")
        db.commit()
        r = admin_client.get("/cover-review")
        assert "No covers need attention" in r.text

    def test_a_dismissed_item_is_not_in_the_queue(self, admin_client, db):
        item_id = _seed(db, title="Dismissed")
        db.execute("UPDATE items SET cover_review_dismissed = 1 WHERE id = ?", (item_id,))
        db.commit()
        r = admin_client.get("/cover-review")
        assert "No covers need attention" in r.text

    def test_an_empty_queue_renders_the_terminal_state(self, admin_client, db):
        r = admin_client.get("/cover-review")
        assert r.status_code == 200
        assert "No covers need attention" in r.text
        assert "cover-review-empty" in r.text

    def test_the_counter_renders_one_of_n_on_page_load(self, admin_client, db):
        for i in range(3):
            _seed(db, title=f"Item {i}", isbn=None)
        db.commit()
        r = admin_client.get("/cover-review")
        assert "1 of 3" in r.text


class TestZeroCopyItemsStayVisible:
    def test_a_located_item_with_no_copy_rows_still_shows_its_location(
        self, admin_client, db
    ):
        """G86: physical locations moved to `item_copies`, and the backfill is
        deliberately conservative — an upgraded database's located wishlist row
        has NO copy row at all. An inner join would drop it out of the queue
        entirely; the fallback to the `items.location_id` seam is what keeps it.
        """
        loc_id = _insert_location(db, name="Hall Closet")
        _seed(db, title="Located But Uncopied", location_id=loc_id)
        db.execute("DELETE FROM item_copies")  # the pre-backfill state
        db.commit()

        r = admin_client.get("/cover-review")
        assert r.status_code == 200
        assert "Located But Uncopied" in r.text
        assert "Hall Closet" in r.text

    def test_an_item_with_no_location_at_all_still_appears(self, admin_client, db):
        _seed(db, title="Nowhere In Particular")
        db.commit()
        r = admin_client.get("/cover-review")
        assert "Nowhere In Particular" in r.text


class TestSkipWritesNothing:
    def test_skip_advances_without_touching_the_row(self, admin_client, db):
        first = _seed(db, title="First", isbn=None)
        _seed(db, title="Second", isbn=None)
        db.commit()
        before = dict(db.execute("SELECT * FROM items WHERE id = ?", (first,)).fetchone())

        r = admin_client.get(
            f"/api/covers/review/next?after={first}&pos=1&total=2"
        )
        assert r.status_code == 200

        after = dict(db.execute("SELECT * FROM items WHERE id = ?", (first,)).fetchone())
        assert before == after, "Skip must write nothing"

    def test_skipping_past_the_last_item_says_the_pass_ended_not_that_the_queue_is_clear(
        self, admin_client, db
    ):
        """The seek walks forward only, so the row just skipped is still in the
        queue. Saying "No covers need attention" there asserts a resolution the
        reviewer never made."""
        only = _seed(db, title="Only One")
        db.commit()
        r = admin_client.get(f"/api/covers/review/next?after={only}&pos=1&total=1")
        assert r.status_code == 200
        assert "No covers need attention" not in r.text
        assert "cover-review-exhausted" in r.text
        assert "1 item you skipped" in r.text
        assert 'data-testid="cover-review-restart"' in r.text

    def test_the_end_of_pass_state_counts_every_row_still_waiting(
        self, admin_client, db
    ):
        _seed(db, title="One", isbn=None)
        _seed(db, title="Two", isbn=None)
        last = _seed(db, title="Three", isbn=None)
        db.commit()
        # `last` is the newest row, so it sorts FIRST; seeking past the oldest
        # is what exhausts the pass.
        oldest = db.execute(
            "SELECT id FROM items ORDER BY updated_at ASC, id ASC LIMIT 1"
        ).fetchone()["id"]
        r = admin_client.get(
            f"/api/covers/review/next?after={oldest}&pos=3&total=3"
        )
        assert "cover-review-exhausted" in r.text
        assert "3 items you skipped" in r.text
        assert last is not None

    def test_dismissing_the_last_row_of_a_clear_queue_still_says_no_covers_need_attention(
        self, admin_client, db
    ):
        """The other end, and the one the old copy was written for. Dismissing
        the only item empties the predicate, so there is genuinely nothing
        left."""
        only = _seed(db, title="Only One")
        db.commit()
        r = admin_client.post(
            f"/api/covers/review/{only}/dismiss", data={"pos": "1", "total": "1"}
        )
        assert r.status_code == 200
        assert "No covers need attention" in r.text
        assert "cover-review-exhausted" not in r.text


class TestTheStructureTheSwapDependsOn:
    def test_the_page_and_the_card_do_not_share_a_dom_id(self, admin_client, db):
        """Every action swaps #cover-review with outerHTML. If the page wrapper
        carried the same id, the first successful action would delete the
        throttle notice nested inside it and the 429 handler would have nothing
        left to unhide.
        """
        _seed(db, title="Anything")
        db.commit()
        r = admin_client.get("/cover-review")
        assert r.text.count('id="cover-review"') == 1
        assert 'id="cover-review-page"' in r.text

    def test_the_throttle_notice_is_not_inside_the_swapped_element(
        self, admin_client, db
    ):
        _seed(db, title="Anything")
        db.commit()
        body = admin_client.get("/cover-review").text
        notice = body.index('id="cover-review-throttled"')
        card = body.index('id="cover-review"')
        assert notice < card, "the notice must precede (and sit outside) the card"

    def test_the_card_does_not_host_item_details_candidates_container(
        self, admin_client, db
    ):
        """The card renders the picker INLINE, so the search box must replace
        the picker rather than refill a sibling. An empty #cover-candidates
        beside an already-rendered picker is what made the first keystroke nest
        a second search box and a second upload form inside the first."""
        item_id = _seed(db, title="Anything")
        db.commit()
        r = admin_client.get("/cover-review")
        assert 'id="cover-candidates"' not in r.text
        assert f'id="cover-picker-{item_id}"' in r.text
        assert f'hx-target="#cover-picker-{item_id}"' in r.text

    def test_the_picker_points_at_the_queue_not_at_item_detail(self, admin_client, db):
        item_id = _seed(db, title="Anything")
        db.commit()
        r = admin_client.get("/cover-review")
        assert f"/api/covers/review/{item_id}/cover-search" in r.text
        assert f"/api/items/{item_id}/cover-search" not in r.text

    def test_the_manual_url_form_is_suppressed_in_the_queue(self, admin_client, db):
        """Design decision: strict allowlist throughout — the queue's verbs are
        select, upload, dismiss and skip. No arbitrary URL paste."""
        _seed(db, title="Anything")
        db.commit()
        r = admin_client.get("/cover-review")
        assert 'data-testid="cover-url"' not in r.text

    def test_the_upload_form_swaps_its_response_in_the_queue(self, admin_client, db):
        """It ships as hx-swap="none", which discards the body. The queue's
        upload answers with the next item's card, so it must actually swap."""
        _seed(db, title="Anything")
        db.commit()
        r = admin_client.get("/cover-review")
        assert 'hx-post="/api/covers/review/' in r.text
        upload = r.text[r.text.index('data-testid="cover-upload"') - 400:]
        assert 'hx-swap="none"' not in upload[:400]


class TestTheSearchRouteExists:
    def test_the_queues_own_cover_search_answers(self, admin_client, db, monkeypatch):
        """The picker's search box emits {{ action_prefix }}/cover-search, which
        in the queue is /api/covers/review/{id}/cover-search. Without this route
        every keystroke in the queue's search box 404s."""
        from app.services import covers
        from app.services import provider_result

        item_id = _seed(db, title="Searchable")
        db.commit()

        async def fake_search(item, query, client, *, creds):
            return provider_result.found("Open Library", [
                {"url": "https://covers.openlibrary.org/b/id/1-L.jpg",
                 "thumbnail": "https://covers.openlibrary.org/b/id/1-S.jpg",
                 "source": "Open Library"},
            ])

        monkeypatch.setattr(covers, "search_covers", fake_search)
        r = admin_client.get(f"/api/covers/review/{item_id}/cover-search?query=x")
        assert r.status_code == 200
        assert "Open Library" in r.text

    def test_the_search_route_404s_for_an_unknown_item(self, admin_client, db):
        r = admin_client.get("/api/covers/review/999999/cover-search")
        assert r.status_code == 404

    def test_the_search_response_replaces_the_picker_rather_than_duplicating_it(
        self, admin_client, db, monkeypatch
    ):
        """The regression for the nested picker. The response is a whole copy of
        the fragment — search box, gallery and upload form — so it has to land
        on the picker's own wrapper with outerHTML, and carry exactly one of
        each."""
        item_id = _fake_search(db, monkeypatch, title="Searchable")
        r = admin_client.get(
            f"/api/covers/review/{item_id}/cover-search?query=x&pos=2&total=3"
        )
        assert r.status_code == 200
        assert r.text.count(f'id="cover-picker-{item_id}"') == 1
        assert r.text.count('data-testid="cover-upload"') == 1
        assert r.text.count(f'id="cover-query-{item_id}"') == 1
        assert f'hx-target="#cover-picker-{item_id}"' in r.text
        assert 'hx-swap="outerHTML"' in r.text
        assert 'id="cover-candidates"' not in r.text

    def test_the_search_response_carries_the_live_counter_not_the_route_defaults(
        self, admin_client, db, monkeypatch
    ):
        """`pos`/`total` reach the search as query params and must come back out
        on every control the response re-renders. Without this the next pick
        posted pos=1,total=0 and the counter read "2 of 0"."""
        item_id = _fake_search(db, monkeypatch, title="Searchable")
        r = admin_client.get(
            f"/api/covers/review/{item_id}/cover-search?query=x&pos=2&total=3"
        )
        # the candidate the reviewer would click
        assert 'cover-select" hx-vals=' in r.text
        assert '"pos":"2", "total":"3"' in r.text
        # the upload form beside it
        assert '<input type="hidden" name="pos" value="2">' in r.text
        assert '<input type="hidden" name="total" value="3">' in r.text
        # and the search box itself, so the NEXT keystroke does not drop them
        assert '"pos":"2","total":"3"' in r.text

    def test_picking_from_a_search_result_advances_the_counter_honestly(
        self, admin_client, db, monkeypatch
    ):
        """End to end for the counter: search at 2 of 3, pick, land on 3 of 3."""
        from app.services import covers

        _seed(db, title="Other One", isbn=None)
        item_id = _seed(db, title="Searchable", isbn=None)
        db.commit()

        async def fake_download(item_id_, url, client):
            return "covers/fake.jpg"

        monkeypatch.setattr(covers, "_download_to_item", fake_download)
        r = admin_client.post(
            f"/api/covers/review/{item_id}/cover-select",
            data={"url": "https://example.com/c.jpg", "pos": "2", "total": "3"},
        )
        assert r.status_code == 200
        assert "3 of 3" in r.text


class TestAuthorization:
    def test_the_page_is_not_reachable_unauthenticated(self, client, db):
        """The regression test for the /covers/ skip-prefix trap.

        `main.py:86` lists /covers/ in _SKIP_AUTH_PREFIXES and `:477` mounts it
        as StaticFiles ahead of every router, so a page at /covers/review would
        have bypassed AuthMiddleware entirely AND been shadowed by the static
        handler. The proof is the CONTRAST, asserted by request rather than by
        reading main.py: our route is intercepted by the auth middleware, while
        a genuinely skip-prefixed path is not.
        """
        ours = client.get("/cover-review", follow_redirects=False)
        assert ours.status_code == 303, "auth middleware must intercept this route"
        assert ours.headers["location"] in ("/login", "/setup")
        assert "Covers needing attention" not in ours.text

        # A path that IS under the skip prefix never reaches the auth middleware,
        # so it is answered by the static mount instead of being redirected.
        skipped = client.get("/covers/nothing-here.jpg", follow_redirects=False)
        assert skipped.status_code != 303, (
            "/covers/ is auth-skipped and statically mounted — this is exactly "
            "why the queue's page must not live under it"
        )

    def test_the_api_is_not_reachable_unauthenticated(self, client, db):
        """Also intercepted before the route. The status differs by install
        state (401 once set up, 303 to /setup on a fresh one) — what matters is
        that it is never served."""
        r = client.get("/api/covers/review/next", follow_redirects=False)
        assert r.status_code in (303, 401)
        assert "cover-review" not in r.text

    def test_a_viewer_is_refused_the_page(self, viewer_client, db):
        """A page route redirects rather than 403ing — `require_role` returns a
        bare 403 only under /api/ (app/auth.py:301,312). The queue follows the
        app's convention, so this pins the redirect, not a status code the rest
        of the app does not use for pages."""
        r = viewer_client.get("/cover-review", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/browse"

    def test_a_viewer_is_refused_the_skip(self, viewer_client, db):
        r = viewer_client.get("/api/covers/review/next", follow_redirects=False)
        assert r.status_code == 403

    def test_an_editor_is_allowed(self, editor_client, db):
        r = editor_client.get("/cover-review")
        assert r.status_code == 200


class TestTheHardRule:
    def test_the_module_never_reaches_the_automatic_chain(self):
        """G29's hard rule, asserted on the source rather than by behaviour.

        An unfiltered `cover_path IS NULL` predicate is safe here ONLY because
        nothing in this module hands its rows to resolve_missing_cover, whose
        title-search fallback invents a book for an authorless row.
        """
        from pathlib import Path
        for module in ("app/routers/cover_review.py",
                       "app/routers/cover_review_actions.py"):
            src = Path(module).read_text()
            body = "\n".join(
                line for line in src.splitlines()
                if not line.strip().startswith("#")
            )
            # The docstrings name them to explain the rule; no CALL may exist.
            assert "resolve_missing_cover(" not in body, module
            assert "_search_isbn_for_item(" not in body, module

    def test_the_module_never_uses_the_bulk_settings_accessor(self):
        """G15: provider credentials are in SECRET_ENV_VARS and the bulk
        accessor returns only keys with a row, so an env-only install would be
        told its provider is unconfigured."""
        from pathlib import Path
        for module in ("app/routers/cover_review.py",
                       "app/routers/cover_review_actions.py"):
            assert "get_all_settings" not in Path(module).read_text(), module


class TestPickingACover:
    def test_a_pick_advances_and_never_redirects(self, admin_client, db, monkeypatch):
        """The point of this endpoint. `items_covers.cover_select` sets
        HX-Redirect to /item/{id}, which would throw the reviewer out of the
        queue after every single pick."""
        from unittest.mock import AsyncMock
        from app.services import covers

        # The queue is newest-first (updated_at DESC, id DESC), so pin the
        # order explicitly rather than relying on insert order.
        first = _seed(db, title="Aaa First", isbn=None)
        second = _seed(db, title="Bbb Second", isbn=None)
        db.execute("UPDATE items SET updated_at = '2020-01-02' WHERE id = ?", (first,))
        db.execute("UPDATE items SET updated_at = '2020-01-01' WHERE id = ?", (second,))
        db.commit()

        monkeypatch.setattr(
            covers, "_download_to_item", AsyncMock(return_value="covers/1.jpg"))

        r = admin_client.post(
            f"/api/covers/review/{first}/cover-select",
            data={"url": "https://covers.openlibrary.org/b/id/1-L.jpg",
                  "pos": "1", "total": "2"},
        )
        assert r.status_code == 200
        assert "HX-Redirect" not in r.headers
        assert db.execute(
            "SELECT cover_path FROM items WHERE id = ?", (first,)
        ).fetchone()["cover_path"] == "covers/1.jpg"
        # and it advanced to the other item
        assert "Bbb Second" in r.text

    def test_picking_a_cover_for_an_isbnless_dvd_writes_no_isbn(
        self, admin_client, db, monkeypatch
    ):
        """The Dune defect, asserted directly on the database.

        G29's automatic chain accepts the first Open Library hit for an
        authorless row and stores the ISBN it found — which is how a DVD titled
        "Dune" took the novel's ISBN and cover. This queue must never do that.
        """
        from unittest.mock import AsyncMock
        from app.services import covers

        dvd = _seed(db, title="Dune", media_type="dvd", isbn=None, authors=None)
        db.commit()

        monkeypatch.setattr(
            covers, "_download_to_item", AsyncMock(return_value="covers/9.jpg"))

        admin_client.post(
            f"/api/covers/review/{dvd}/cover-select",
            data={"url": "https://image.tmdb.org/t/p/w500/x.jpg",
                  "pos": "1", "total": "1"},
        )
        row = db.execute(
            "SELECT isbn, isbn10, cover_path FROM items WHERE id = ?", (dvd,)
        ).fetchone()
        assert row["isbn"] is None, "the queue must never invent an ISBN"
        assert row["isbn10"] is None
        assert row["cover_path"] == "covers/9.jpg"

    def test_a_failed_download_keeps_the_reviewer_on_the_same_item(
        self, admin_client, db, monkeypatch
    ):
        """Advancing past a failure would hide it from the person who chose."""
        from unittest.mock import AsyncMock
        from app.services import covers

        first = _seed(db, title="Aaa First", isbn=None)
        second = _seed(db, title="Bbb Second", isbn=None)
        db.execute("UPDATE items SET updated_at = '2020-01-02' WHERE id = ?", (first,))
        db.execute("UPDATE items SET updated_at = '2020-01-01' WHERE id = ?", (second,))
        db.commit()

        monkeypatch.setattr(
            covers, "_download_to_item", AsyncMock(return_value=None))

        r = admin_client.post(
            f"/api/covers/review/{first}/cover-select",
            data={"url": "https://not-allowed.example/x.jpg",
                  "pos": "1", "total": "2"},
        )
        assert r.status_code == 200
        assert "HX-Redirect" not in r.headers
        assert "Aaa First" in r.text, "must stay on the item that failed"
        assert db.execute(
            "SELECT cover_path FROM items WHERE id = ?", (first,)
        ).fetchone()["cover_path"] is None

    def test_a_viewer_cannot_pick(self, viewer_client, db):
        item_id = _seed(db, title="Anything")
        db.commit()
        r = viewer_client.post(
            f"/api/covers/review/{item_id}/cover-select",
            data={"url": "https://covers.openlibrary.org/b/id/1-L.jpg"},
        )
        assert r.status_code == 403


class TestUploadingACover:
    def _png(self):
        # A minimal valid PNG — magic bytes are what save_uploaded_cover checks.
        return (b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)

    def test_an_upload_advances_and_never_redirects(self, admin_client, db):
        first = _seed(db, title="Aaa First", isbn=None)
        second = _seed(db, title="Bbb Second", isbn=None)
        db.execute("UPDATE items SET updated_at = '2020-01-02' WHERE id = ?", (first,))
        db.execute("UPDATE items SET updated_at = '2020-01-01' WHERE id = ?", (second,))
        db.commit()

        r = admin_client.post(
            f"/api/covers/review/{first}/cover-upload",
            files={"cover": ("c.png", self._png(), "image/png")},
            data={"pos": "1", "total": "2"},
        )
        assert r.status_code == 200
        assert "HX-Redirect" not in r.headers
        assert db.execute(
            "SELECT cover_path FROM items WHERE id = ?", (first,)
        ).fetchone()["cover_path"] is not None
        assert "Bbb Second" in r.text

    def test_a_rejected_upload_keeps_the_reviewer_on_the_same_item(
        self, admin_client, db
    ):
        first = _seed(db, title="Aaa First", isbn=None)
        second = _seed(db, title="Bbb Second", isbn=None)
        db.execute("UPDATE items SET updated_at = '2020-01-02' WHERE id = ?", (first,))
        db.execute("UPDATE items SET updated_at = '2020-01-01' WHERE id = ?", (second,))
        db.commit()

        r = admin_client.post(
            f"/api/covers/review/{first}/cover-upload",
            files={"cover": ("x.txt", b"not an image at all", "text/plain")},
            data={"pos": "1", "total": "2"},
        )
        assert r.status_code == 200
        assert "Aaa First" in r.text
        assert db.execute(
            "SELECT cover_path FROM items WHERE id = ?", (first,)
        ).fetchone()["cover_path"] is None

    def test_a_viewer_cannot_upload(self, viewer_client, db):
        item_id = _seed(db, title="Anything")
        db.commit()
        r = viewer_client.post(
            f"/api/covers/review/{item_id}/cover-upload",
            files={"cover": ("c.png", self._png(), "image/png")},
        )
        assert r.status_code == 403


class TestTheSeekSurvivesTheWrite:
    def test_a_pick_does_not_walk_the_reviewer_back_to_the_top(
        self, admin_client, db, monkeypatch
    ):
        """Setting a cover bumps `updated_at`, which moves the row to the FRONT
        of the queue's ordering. A seek computed after the write would therefore
        return the top of the queue rather than the next item — so the key must
        be captured before the write.
        """
        from unittest.mock import AsyncMock
        from app.services import covers

        # Three items, distinct timestamps so the order is unambiguous.
        a = _seed(db, title="Aaa", isbn=None)
        b = _seed(db, title="Bbb", isbn=None)
        c = _seed(db, title="Ccc", isbn=None)
        db.execute("UPDATE items SET updated_at = '2020-01-03' WHERE id = ?", (a,))
        db.execute("UPDATE items SET updated_at = '2020-01-02' WHERE id = ?", (b,))
        db.execute("UPDATE items SET updated_at = '2020-01-01' WHERE id = ?", (c,))
        db.commit()

        monkeypatch.setattr(
            covers, "_download_to_item", AsyncMock(return_value="covers/1.jpg"))

        # Picking for the FIRST item must advance to the second, not restart.
        r = admin_client.post(
            f"/api/covers/review/{a}/cover-select",
            data={"url": "https://covers.openlibrary.org/b/id/1-L.jpg",
                  "pos": "1", "total": "3"},
        )
        assert "Bbb" in r.text
        assert "Ccc" not in r.text

    def test_the_last_pick_lands_on_the_terminal_state(
        self, admin_client, db, monkeypatch
    ):
        from unittest.mock import AsyncMock
        from app.services import covers

        only = _seed(db, title="Only", isbn=None)
        db.commit()
        monkeypatch.setattr(
            covers, "_download_to_item", AsyncMock(return_value="covers/1.jpg"))
        r = admin_client.post(
            f"/api/covers/review/{only}/cover-select",
            data={"url": "https://covers.openlibrary.org/b/id/1-L.jpg",
                  "pos": "1", "total": "1"},
        )
        assert "No covers need attention" in r.text


class TestDismissal:
    def test_dismiss_sets_the_column_and_advances(self, admin_client, db):
        first = _seed(db, title="Aaa", isbn=None)
        second = _seed(db, title="Bbb", isbn=None)
        db.execute("UPDATE items SET updated_at = '2020-01-02' WHERE id = ?", (first,))
        db.execute("UPDATE items SET updated_at = '2020-01-01' WHERE id = ?", (second,))
        db.commit()

        r = admin_client.post(
            f"/api/covers/review/{first}/dismiss",
            data={"pos": "1", "total": "2"},
        )
        assert r.status_code == 200
        assert "HX-Redirect" not in r.headers
        assert db.execute(
            "SELECT cover_review_dismissed FROM items WHERE id = ?", (first,)
        ).fetchone()["cover_review_dismissed"] == 1
        assert "Bbb" in r.text

    def test_a_dismissed_item_does_not_reappear(self, admin_client, db):
        item_id = _seed(db, title="Gone For Good", isbn=None)
        db.commit()
        admin_client.post(f"/api/covers/review/{item_id}/dismiss",
                          data={"pos": "1", "total": "1"})
        r = admin_client.get("/cover-review")
        assert "Gone For Good" not in r.text
        assert "No covers need attention" in r.text

    def test_the_dismissal_lives_in_the_database_not_in_memory(
        self, admin_client, db
    ):
        """The only sense in which a unit test can prove "survives a restart":
        the value is a column, read back on a fresh connection."""
        item_id = _seed(db, title="Durable", isbn=None)
        db.commit()
        admin_client.post(f"/api/covers/review/{item_id}/dismiss",
                          data={"pos": "1", "total": "1"})

        from app.database import get_db
        with get_db() as fresh:
            row = fresh.execute(
                "SELECT cover_review_dismissed FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        assert row["cover_review_dismissed"] == 1

    def test_a_viewer_cannot_dismiss(self, viewer_client, db):
        item_id = _seed(db, title="Anything")
        db.commit()
        r = viewer_client.post(f"/api/covers/review/{item_id}/dismiss")
        assert r.status_code == 403


class TestRemoveCoverIsTheEscapeHatch:
    def test_removing_a_cover_returns_a_dismissed_item_to_the_queue(
        self, admin_client, db
    ):
        """The cross-cutting pin (design section 4).

        Without this, an item dismissed once could never be reviewed again after
        a later removal — the queue's Remove control renders only when a cover
        exists, so a dismissed cover-less item has no way back at all.
        """
        item_id = _seed(db, title="Second Chance", isbn=None)
        db.execute(
            "UPDATE items SET cover_path = 'covers/5.jpg', cover_review_dismissed = 1 "
            "WHERE id = ?", (item_id,))
        db.commit()

        r = admin_client.post(f"/api/items/{item_id}/cover-remove")
        assert r.status_code == 200

        row = db.execute(
            "SELECT cover_path, cover_review_dismissed FROM items WHERE id = ?",
            (item_id,)).fetchone()
        assert row["cover_path"] is None
        assert row["cover_review_dismissed"] == 0, (
            "removal must clear the dismissal, or the item can never be "
            "reviewed again"
        )

        # and it is genuinely back in the queue
        page = admin_client.get("/cover-review")
        assert "Second Chance" in page.text


class TestRetryMissingCoversStaysDismissalBlind:
    def test_the_bulk_sweep_still_picks_up_a_dismissed_item(
        self, admin_client, db, monkeypatch
    ):
        """A DECISION, pinned so a later "fix" has to argue with an assertion.

        Retry Missing Covers deliberately does NOT filter on
        `cover_review_dismissed`. Both sweep routes are admin-only and
        media-type-gated, so G29's Dune defect cannot fire and no wrong data is
        written — the worst case is that an admin who explicitly asked for a
        sweep gets one on a row they earlier dismissed.

        The deciding factor is the other direction: there is **no un-dismiss
        path in the UI**. The Remove cover control renders only inside
        `{% if cover_path %}`, and a dismissed item is by definition
        cover-less, so this sweep is currently the only way an accidentally
        dismissed item can come back on its own. Making it dismissal-aware
        would make "Not available" permanent short of a database edit.

        Revisit if and when un-dismissing from the UI ships.
        """
        from unittest.mock import AsyncMock
        from app.routers import items_common

        dismissed = _seed(db, title="Dismissed Book", media_type="book",
                          isbn="9780000000033")
        db.execute("UPDATE items SET cover_review_dismissed = 1 WHERE id = ?",
                   (dismissed,))
        db.commit()

        seen = []

        async def fake_resolve(item_id, client):
            seen.append(item_id)
            return False

        monkeypatch.setattr(items_common, "resolve_missing_cover", fake_resolve)
        r = admin_client.post("/api/covers/bulk-retry")
        assert r.status_code == 200
        assert dismissed in seen, (
            "the bulk sweep is deliberately dismissal-blind — see the docstring"
        )


class TestTheCounterSurvivesAPick:
    """Found by the E2E walk-through: the picker's own select button and upload
    form did not carry pos/total, so the server fell back to its defaults and
    the on-screen counter read "2 of 0" immediately after a real pick. Item
    ordering was never affected — the seek reads the database row, not the
    carried value — but the number a reviewer reads was wrong.
    """

    def test_the_card_emits_the_counter_fields_for_the_picker(
        self, admin_client, db
    ):
        for i in range(3):
            _seed(db, title=f"Item {i}", isbn=None)
        db.commit()
        r = admin_client.get("/cover-review")
        assert 'name="pos"' in r.text
        assert 'name="total"' in r.text
        assert 'value="3"' in r.text

    def test_item_detail_emits_no_counter_fields(self, admin_client, db):
        """The fragment is shared. Item detail is not a counted host and must
        stay byte-identical to what it rendered before the queue existed."""
        item_id = _seed(db, title="On Item Detail", cover_path=None)
        db.commit()
        from unittest.mock import AsyncMock
        from app.services import covers, provider_result
        r = admin_client.get(f"/item/{item_id}")
        assert 'name="pos"' not in r.text
        assert 'name="total"' not in r.text

    def test_an_upload_carrying_the_counter_advances_it_correctly(
        self, admin_client, db
    ):
        first = _seed(db, title="Aaa", isbn=None)
        second = _seed(db, title="Bbb", isbn=None)
        db.execute("UPDATE items SET updated_at = '2020-01-02' WHERE id = ?", (first,))
        db.execute("UPDATE items SET updated_at = '2020-01-01' WHERE id = ?", (second,))
        db.commit()

        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
        r = admin_client.post(
            f"/api/covers/review/{first}/cover-upload",
            files={"cover": ("c.png", png, "image/png")},
            data={"pos": "1", "total": "2"},
        )
        assert "2 of 2" in r.text, "the counter must advance, not reset to 'of 0'"
