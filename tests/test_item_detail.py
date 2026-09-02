"""T1 — record footer, wishlist badge, value as-of date, admin integration block."""
import pytest

from tests.conftest import _insert_item


class TestRecordFooter:
    def test_footer_shows_dates_and_source_outside_grid(self, viewer_client, db):
        item_id = _insert_item(
            db, title="Footer Book", isbn="9789000020010", source="audiobookshelf",
        )
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert 'data-testid="record-footer"' in html
        assert "Added 20" in html
        assert "Updated 20" in html
        assert "via audiobookshelf" in html
        assert "Source:" not in html


class TestWishlistBadge:
    def test_wishlist_badge_present_when_not_owned(self, viewer_client, db):
        item_id = _insert_item(
            db, title="Wishlist Book", isbn="9789000020027", owned=0,
        )
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert 'data-testid="wishlist-badge"' in html

    def test_wishlist_badge_absent_when_owned(self, viewer_client, db):
        item_id = _insert_item(
            db, title="Owned Book", isbn="9789000020034", owned=1,
        )
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert 'data-testid="wishlist-badge"' not in html


class TestEstimatedValueAsOf:
    def test_estimated_value_shows_as_of_date(self, viewer_client, db):
        item_id = _insert_item(
            db, title="Estimated Value Book", isbn="9789000020041",
            estimated_value=12.5, value_updated_at="2026-03-01 10:00:00",
        )
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert "(as of 2026-03-01)" in html

    def test_manual_value_does_not_show_as_of_date(self, viewer_client, db):
        item_id = _insert_item(
            db, title="Manual Value Book", isbn="9789000020058",
            manual_value=20, value_updated_at="2026-03-01 10:00:00",
        )
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert "(manual)" in html
        assert "as of" not in html


class TestIntegrationBlockAdminOnly:
    def test_admin_sees_integration_block(self, admin_client, db):
        item_id = _insert_item(
            db, title="Integration Book Admin", isbn="9789000020065",
            abs_id="li_abc123",
        )
        db.commit()

        html = admin_client.get(f"/item/{item_id}").text

        assert 'data-testid="integration-ids"' in html
        assert "li_abc123" in html

    def test_editor_does_not_see_integration_block(self, editor_client, db):
        item_id = _insert_item(
            db, title="Integration Book Editor", isbn="9780900002007",
            abs_id="li_abc123",
        )
        db.commit()

        html = editor_client.get(f"/item/{item_id}").text

        assert 'data-testid="integration-ids"' not in html
        assert "li_abc123" not in html

    def test_viewer_does_not_see_integration_block(self, viewer_client, db):
        item_id = _insert_item(
            db, title="Integration Book Viewer", isbn="9789000020089",
            abs_id="li_abc123",
        )
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert 'data-testid="integration-ids"' not in html
        assert "li_abc123" not in html


class TestReadingHistory:
    """T2 — reading history, rendered by BOTH renderers of the fragment."""

    @staticmethod
    def _log(db, item_id, started, finished):
        db.execute(
            "INSERT INTO reading_log (item_id, status, date_started, date_finished) "
            "VALUES (?, 'read', ?, ?)",
            (item_id, started, finished),
        )

    def test_reading_history_lists_every_row(self, viewer_client, db):
        item_id = _insert_item(db, title="Reread Book", isbn="9789000020096")
        self._log(db, item_id, "2025-01-01", "2025-01-20")
        self._log(db, item_id, "2026-02-01", "2026-02-10")
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert 'data-testid="reading-history"' in html
        assert "Read 2 times" in html
        assert "2025-01-20" in html
        assert "2026-02-10" in html
        # newest first
        assert html.index("2026-02-10") < html.index("2025-01-20")

    def test_reading_history_suppressed_below_two_rows(self, viewer_client, db):
        one_id = _insert_item(db, title="Read Once", isbn="9789000020102")
        self._log(db, one_id, "2025-01-01", "2025-01-20")
        none_id = _insert_item(db, title="Never Read", isbn="9789000020119")
        db.commit()

        assert 'data-testid="reading-history"' not in viewer_client.get(f"/item/{one_id}").text
        assert 'data-testid="reading-history"' not in viewer_client.get(f"/item/{none_id}").text

    def test_reading_status_fragment_carries_history_after_marking_read(self, viewer_client, db):
        """The two-renderer pin: fails if only pages.py is wired."""
        item_id = _insert_item(db, title="Toggle Book", isbn="9789000020126")
        self._log(db, item_id, "2025-01-01", "2025-01-20")
        db.commit()

        resp = viewer_client.post(
            f"/api/items/{item_id}/reading-status", data={"status": "read"}
        )

        assert resp.status_code == 200
        assert 'data-testid="reading-history"' in resp.text
        assert "Read 2 times" in resp.text

    def test_reading_status_fragment_without_history_still_renders(self, viewer_client, db):
        item_id = _insert_item(db, title="Fresh Book", isbn="9789000020133")
        db.commit()

        resp = viewer_client.post(
            f"/api/items/{item_id}/reading-status", data={"status": "reading"}
        )

        assert resp.status_code == 200
        assert 'data-testid="reading-history"' not in resp.text
        assert f'hx-post="/api/items/{item_id}/reading-status"' in resp.text


class TestSeriesProgress:
    """T3 — series progress from two labelled sources, never blended."""

    def test_series_progress_local_gaps(self, viewer_client, db):
        first = _insert_item(db, title="Gap One", isbn="9780900002014",
                             series_name="Gap Saga", series_position=1, owned=1)
        _insert_item(db, title="Gap Three", isbn="9789000020157",
                     series_name="Gap Saga", series_position=3, owned=1)
        _insert_item(db, title="Gap Four", isbn="9789000020164",
                     series_name="Gap Saga", series_position=4, owned=1)
        db.commit()

        html = viewer_client.get(f"/item/{first}").text

        assert 'data-testid="series-progress"' in html
        assert "you own 3 of 1–4" in html
        assert "missing #2" in html
        assert "(Hardcover)" not in html
        assert "0 in series" not in html

    def test_series_progress_hardcover_clause_only_with_meta(self, viewer_client, db):
        item_id = _insert_item(db, title="HC One", isbn="9789000020171",
                               series_name="Gap Saga", series_position=1, owned=1)
        _insert_item(db, title="HC Three", isbn="9789000020188",
                     series_name="Gap Saga", series_position=3, owned=1)
        # lower-case on purpose — pins the COLLATE NOCASE lookup
        db.execute(
            "INSERT INTO series_meta (name, hc_total, hc_missing) VALUES ('gap saga', 7, 4)"
        )
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert "7 in series (Hardcover)" in html
        assert "4 missing" not in html

    def test_series_progress_no_hardcover_clause_when_total_null(self, viewer_client, db):
        item_id = _insert_item(db, title="Null HC One", isbn="9789000020195",
                               series_name="Null Saga", series_position=1, owned=1)
        _insert_item(db, title="Null HC Two", isbn="9789000020201",
                     series_name="Null Saga", series_position=2, owned=1)
        db.execute("INSERT INTO series_meta (name, hc_total) VALUES ('Null Saga', NULL)")
        db.commit()

        assert "(Hardcover)" not in viewer_client.get(f"/item/{item_id}").text

    def test_series_progress_wishlist_counts_as_present_for_gaps(self, viewer_client, db):
        first = _insert_item(db, title="Wish One", isbn="9780900002021",
                             series_name="Wish Saga", series_position=1, owned=1)
        _insert_item(db, title="Wish Two", isbn="9789000020225",
                     series_name="Wish Saga", series_position=2, owned=0)
        _insert_item(db, title="Wish Three", isbn="9789000020232",
                     series_name="Wish Saga", series_position=3, owned=1)
        db.commit()

        html = viewer_client.get(f"/item/{first}").text

        assert "you own 2 of 1–3" in html
        assert "missing" not in html

    def test_series_progress_lone_volume_quiet(self, viewer_client, db):
        alone = _insert_item(db, title="Lone One", isbn="9789000020249",
                             series_name="Lone Saga", series_position=1, owned=1)
        stranded = _insert_item(db, title="Lone Three", isbn="9789000020256",
                                series_name="Stranded Saga", series_position=3, owned=1)
        db.commit()

        assert " · you own" not in viewer_client.get(f"/item/{alone}").text
        html = viewer_client.get(f"/item/{stranded}").text
        assert "you own 1 of 1–3" in html
        assert "missing #1, #2" in html

    def test_series_position_renders_fractionally(self, viewer_client, db):
        half = _insert_item(db, title="Novella", isbn="9789000020263",
                            series_name="Frac Saga", series_position=2.5, owned=1)
        whole = _insert_item(db, title="Volume Three", isbn="9789000020270",
                             series_name="Frac Saga", series_position=3.0, owned=1)
        db.commit()

        assert "#2.5" in viewer_client.get(f"/item/{half}").text
        html = viewer_client.get(f"/item/{whole}").text
        assert "#3" in html
        assert "#3.0" not in html

    def test_series_name_links_to_series_page(self, viewer_client, db):
        with_series = _insert_item(db, title="Linked", isbn="9789000020287",
                                   series_name="Link Saga", series_position=1, owned=1)
        without = _insert_item(db, title="Unlinked", isbn="9789000020294", owned=1)
        db.commit()

        assert 'href="/series"' in viewer_client.get(f"/item/{with_series}").text
        html = viewer_client.get(f"/item/{without}").text
        assert 'data-testid="series-progress"' not in html
        assert "Series:" not in html

    def test_case_variant_series_merge_on_both_pages(self, viewer_client, db):
        """R2 pin: one NOCASE identity on the detail page AND on /series."""
        upper = _insert_item(db, title="Dune", isbn="9789000020300",
                             series_name="Dune Saga", series_position=1, owned=1)
        lower = _insert_item(db, title="Dune Messiah", isbn="9789000020317",
                             series_name="dune saga", series_position=3, owned=1)
        db.commit()

        for item_id in (upper, lower):
            html = viewer_client.get(f"/item/{item_id}").text
            assert "you own 2 of 1–3" in html
            assert "missing #2" in html

        series_html = viewer_client.get("/series").text
        # exactly one card on /series, not two: the whole page holds only this
        # series, so the card count is the definitive assertion.
        assert series_html.count('data-testid="series-card"') == 1
        # ...and both spellings' books live inside that one card
        assert "Dune Messiah" in series_html


class TestBookShapedControls:
    """The item page shows only the controls its item can use.

    Retry ISBN keys on the ISBN (the route's own precondition), Push to Hardcover
    on the book family plus an ISBN or an existing Hardcover id, and Reading
    Status on the book family — or on anything else while a status is still set,
    so a stale one can be cleared. Every negative here has row 5 as its positive
    control, and every row seeds and commits before it requests.
    """

    RETRY = "retry-cover"
    PUSH = "Push to Hardcover"
    SYNCED = "Synced to Hardcover"
    HEADING = ">Reading Status</p>"
    SECTION = 'id="reading-status-section"'
    CLEAR = 'title="Clear status"'

    @pytest.mark.parametrize("media_type", ["cd", "video_game"])
    def test_a_disc_or_cartridge_loses_all_three_and_keeps_the_page(
        self, editor_client, db, monkeypatch, media_type,
    ):
        monkeypatch.setenv("HARDCOVER_TOKEN", "hc-token")
        item_id = _insert_item(db, title="Not A Book", isbn=None, media_type=media_type)
        db.commit()

        resp = editor_client.get(f"/item/{item_id}")
        html = resp.text

        assert resp.status_code == 200
        assert self.RETRY not in html
        assert self.PUSH not in html
        assert self.HEADING not in html
        assert self.SECTION not in html
        # The page lost three controls, not the page.
        assert 'data-testid="cover-controls"' in html
        assert f'href="/item/{item_id}/edit' in html
        assert "hx-delete=" in html

    def test_a_book_without_an_isbn_keeps_reading_status_only(
        self, editor_client, db, monkeypatch,
    ):
        monkeypatch.setenv("HARDCOVER_TOKEN", "hc-token")
        item_id = _insert_item(db, title="No ISBN Book", isbn=None, media_type="book")
        db.commit()

        html = editor_client.get(f"/item/{item_id}").text

        assert self.HEADING in html
        assert self.SECTION in html
        assert self.RETRY not in html
        assert self.PUSH not in html

    def test_an_existing_hardcover_id_keeps_the_hardcover_row_without_an_isbn(
        self, editor_client, db, monkeypatch,
    ):
        monkeypatch.setenv("HARDCOVER_TOKEN", "hc-token")
        synced_id = _insert_item(
            db, title="Synced No ISBN", isbn=None, media_type="book",
            hardcover_user_book_id=4242,
        )
        linked_id = _insert_item(
            db, title="Linked No ISBN", isbn=None, media_type="book",
            hardcover_book_id=99,
        )
        db.commit()

        assert self.SYNCED in editor_client.get(f"/item/{synced_id}").text
        assert self.PUSH in editor_client.get(f"/item/{linked_id}").text

    def test_a_book_with_an_isbn_carries_all_three_the_positive_control(
        self, editor_client, db, monkeypatch,
    ):
        monkeypatch.setenv("HARDCOVER_TOKEN", "hc-token")
        item_id = _insert_item(db, title="Full Book", isbn="9789000070015", media_type="book")
        db.commit()

        html = editor_client.get(f"/item/{item_id}").text

        assert self.RETRY in html
        assert self.PUSH in html
        assert self.HEADING in html

    def test_a_disc_with_a_stale_status_can_still_clear_it(self, viewer_client, db):
        """The undo path — and the G65 pin: the toggle route renders the
        fragment without book_media_types in context and must not care."""
        item_id = _insert_item(
            db, title="Read DVD", isbn=None, media_type="dvd", reading_status="read",
        )
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text
        assert self.HEADING in html
        assert self.CLEAR in html

        resp = viewer_client.post(f"/api/items/{item_id}/reading-status", data={"status": ""})
        assert resp.status_code == 200
        assert self.SECTION in resp.text

        assert self.HEADING not in viewer_client.get(f"/item/{item_id}").text

    def test_a_dvd_with_an_isbn_gets_retry_but_not_hardcover(
        self, editor_client, db, monkeypatch,
    ):
        """Retry is route-keyed; Push is family-keyed. e2e/test_browse.py seeds this row."""
        monkeypatch.setenv("HARDCOVER_TOKEN", "hc-token")
        item_id = _insert_item(db, title="ISBN DVD", isbn="9780009990021", media_type="dvd")
        db.commit()

        html = editor_client.get(f"/item/{item_id}").text

        assert self.RETRY in html
        assert self.PUSH not in html

    def test_a_viewer_sees_no_reading_status_on_a_disc(self, viewer_client, db):
        item_id = _insert_item(db, title="Viewer CD", isbn=None, media_type="cd")
        db.commit()

        assert self.HEADING not in viewer_client.get(f"/item/{item_id}").text
