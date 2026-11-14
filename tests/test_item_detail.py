"""T1 — record footer, wishlist badge, value as-of date, admin integration block."""
import re
from pathlib import Path

import pytest

from app.services.item_copies import insert_copy
from app.services.item_write import insert_item, update_item_fields
from tests.conftest import _insert_item, _insert_location


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
        assert "via Audiobookshelf" in html
        assert "Source:" not in html


class TestWishlistBadge:
    def test_wishlist_badge_present_when_not_owned(self, viewer_client, db):
        item_id = _insert_item(
            db, title="Wishlist Book", isbn="9789000020027", owned=0, wishlisted=True,
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

    def test_a_game_history_says_played(self, viewer_client, db):
        item_id = _insert_item(db, title="Replayed Game", isbn=None, media_type="video_game")
        self._log(db, item_id, "2025-01-01", "2025-01-20")
        self._log(db, item_id, "2026-02-01", "2026-02-10")
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert "Played 2 times" in html
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
                     series_name="Wish Saga", series_position=2, owned=0, wishlisted=True)
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
    HEADING = ">Status</p>"
    SECTION = 'id="reading-status-section"'
    CLEAR = 'title="Clear status"'

    @pytest.mark.parametrize("media_type", ["cd"])
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

    @pytest.mark.parametrize("media_type, want, doing, done", [
        ("dvd", "Want to Watch", "Watching", "Watched"),
        ("video_game", "Want to Play", "Playing", "Played"),
    ])
    def test_a_disc_or_game_gets_its_own_status_words(
        self, editor_client, db, media_type, want, doing, done,
    ):
        item_id = _insert_item(db, title="Own Verb", isbn=None, media_type=media_type)
        db.commit()

        html = editor_client.get(f"/item/{item_id}").text

        assert self.HEADING in html
        section = re.search(r'<div id="reading-status-section">.*?</div>\s*</div>', html, re.S)
        assert section
        for word in (want, doing, done):
            assert word in section.group(0)
        assert "Want to Read" not in section.group(0)

    def test_the_toggle_rerenders_a_disc_in_its_own_words(self, viewer_client, db):
        """The route renders the fragment with only `item` in context, so the
        words must reach it through the status_labels global."""
        item_id = _insert_item(db, title="Toggle DVD", isbn=None, media_type="dvd")
        db.commit()

        resp = viewer_client.post(f"/api/items/{item_id}/reading-status", data={"status": "read"})

        assert resp.status_code == 200
        assert "Watched" in resp.text
        assert ">Read<" not in resp.text
        assert "Status: Watched" in resp.headers["HX-Trigger"]

    def test_an_isbnless_audiobook_with_an_asin_gets_retry_asin(self, editor_client, db):
        item_id = _insert_item(
            db, title="Audio", isbn=None, asin="B002V0QK4C", media_type="audiobook",
        )
        db.commit()

        html = editor_client.get(f"/item/{item_id}").text

        assert f"/api/items/{item_id}/{self.RETRY}" in html
        assert "Retry ASIN" in html
        assert "Retry ISBN" not in html

    @pytest.mark.parametrize("media_type, asin", [("audiobook", None), ("ebook", "B002V0QK4C")])
    def test_no_retry_without_an_isbn_or_an_audiobook_asin(
        self, editor_client, db, media_type, asin,
    ):
        item_id = _insert_item(db, title="Nope", isbn=None, asin=asin, media_type=media_type)
        db.commit()

        html = editor_client.get(f"/item/{item_id}").text

        assert f"/api/items/{item_id}/{self.RETRY}" not in html

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
        fragment without status_media_types in context and must not care. A CD,
        because a DVD now keeps its section after clearing."""
        item_id = _insert_item(
            db, title="Read CD", isbn=None, media_type="cd", reading_status="read",
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


class TestFetchSynopsisControl:
    """The "Fetch synopsis" button (T6) renders only for the synopsis
    media-type set — `synopsis_media_types`, not `book_media_types` — so
    comics and manga, which the metadata sources can't answer for, don't
    get an offer that always fails. Asserted on the element (`data-testid`),
    never on its prose, per G69."""

    TESTID = 'data-testid="fetch-synopsis"'

    def test_a_book_with_no_description_offers_it(self, editor_client, db):
        item_id = _insert_item(db, title="No Synopsis Yet", isbn=None, media_type="book")
        db.commit()

        assert self.TESTID in editor_client.get(f"/item/{item_id}").text

    @pytest.mark.parametrize("media_type", ["comic", "manga"])
    def test_a_comic_or_manga_does_not_offer_it(self, editor_client, db, media_type):
        item_id = _insert_item(db, title="No Synopsis Comic", isbn=None, media_type=media_type)
        db.commit()

        assert self.TESTID not in editor_client.get(f"/item/{item_id}").text


class TestLinkedItemsAreFormatOnly:
    """"Also available as:" claims the target is the same content in another
    format. media_groups.link_items() also writes 'related' and 'adaptation'
    links, for which that claim is false — a novel and its film adaptation are
    different works — so the item page renders format links only.

    Found on a live test drive (2026-09-08): an 'adaptation' link between two
    distinct books rendered as "Also available as: Book".
    """

    def test_format_links_are_shown(self, viewer_client, db):
        from app.services import media_groups

        a = _insert_item(db, title="Novel", media_type="book")
        b = _insert_item(db, title="Novel (audio)", media_type="audiobook")
        media_groups.link_items(db, a, b, link_type="format")
        db.commit()

        html = viewer_client.get(f"/item/{a}").text

        assert "Also available as:" in html
        assert f'/item/{b}' in html

    @pytest.mark.parametrize("link_type", ["related", "adaptation"])
    def test_non_format_links_are_not_shown_as_another_format(
        self, viewer_client, db, link_type
    ):
        from app.services import media_groups

        a = _insert_item(db, title="Novel", media_type="book")
        b = _insert_item(db, title="The Film Of It", media_type="dvd")
        media_groups.link_items(db, a, b, link_type=link_type)
        db.commit()

        html = viewer_client.get(f"/item/{a}").text

        assert "Also available as:" not in html

    def test_the_audiobookshelf_linker_still_shows_because_it_defaults_to_format(
        self, viewer_client, db
    ):
        """The ABS linker omits link_type and relies on the column default."""
        a = _insert_item(db, title="Novel", media_type="book")
        b = _insert_item(db, title="Novel (audio)", media_type="audiobook")
        db.execute(
            "INSERT INTO item_links (item_a_id, item_b_id) VALUES (?, ?)",
            (min(a, b), max(a, b)),
        )
        db.commit()

        html = viewer_client.get(f"/item/{a}").text

        assert "Also available as:" in html


class TestCopiesBlock:
    """The item page reads `item_copies`, not just the `items.location_id`
    seam (issue #116). A merged item legitimately has copies in two places;
    before this the page showed only the first."""

    def test_one_placed_copy_renders_the_single_location_line(self, viewer_client, db):
        office = _insert_location(db, "Office / Bookcase 2 / Shelf 3")
        item_id = _insert_item(db, title="One Copy", isbn="9789000020034",
                               location_id=office)
        insert_copy(db, {"item_id": item_id, "copy_number": 1,
                         "location_id": office, "is_primary": 1})
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert "Location:" in html
        assert "Office / Bookcase 2 / Shelf 3" in html
        assert ">Copies<" not in html

    def test_a_position_is_shown_when_set_and_absent_when_not(self, viewer_client, db):
        office = _insert_location(db, "Office")
        placed = _insert_item(db, title="Placed", isbn="9789000020041",
                              location_id=office)
        insert_copy(db, {"item_id": placed, "copy_number": 1, "location_id": office,
                         "is_primary": 1, "position_order": 4})
        loose = _insert_item(db, title="Loose", isbn="9789000020058",
                             location_id=office)
        insert_copy(db, {"item_id": loose, "copy_number": 1, "location_id": office,
                         "is_primary": 1})
        db.commit()

        assert "· position 4" in viewer_client.get(f"/item/{placed}").text
        assert "position" not in viewer_client.get(f"/item/{loose}").text

    def test_two_copies_render_both_locations_under_a_copies_heading(
        self, viewer_client, db
    ):
        office = _insert_location(db, "Office")
        loft = _insert_location(db, "Loft")
        item_id = _insert_item(db, title="Merged", isbn="9789000020065",
                               location_id=office)
        insert_copy(db, {"item_id": item_id, "copy_number": 1, "location_id": office,
                         "is_primary": 1})
        insert_copy(db, {"item_id": item_id, "copy_number": 2, "location_id": loft,
                         "condition": "Fair", "provenance": "gift"})
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert ">Copies<" in html
        assert "Office" in html
        assert "Loft" in html
        assert "#1" in html and "#2" in html
        assert "Fair" in html and "gift" in html

    def test_a_copy_with_no_location_reads_as_no_location_not_none(
        self, viewer_client, db
    ):
        office = _insert_location(db, "Office")
        item_id = _insert_item(db, title="Half Placed", isbn="9789000020072",
                               location_id=office)
        insert_copy(db, {"item_id": item_id, "copy_number": 1, "location_id": office,
                         "is_primary": 1})
        insert_copy(db, {"item_id": item_id, "copy_number": 2})
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert "No location" in html
        assert "None" not in html

    def test_an_item_with_no_copies_renders_neither_line_nor_heading(
        self, viewer_client, db
    ):
        item_id = _insert_item(db, title="Unplaced", isbn="9789000020089")
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert "Location:" not in html
        assert ">Copies<" not in html

    def test_a_located_item_with_no_copies_still_shows_its_location(
        self, viewer_client, db
    ):
        """An upgraded database holds located items with no copy rows at all
        (G86). `main` rendered `Location: X` for them straight off
        `items.location_name`; hiding the wrapper on `copies == []` dropped
        that line, so the page showed no location for an item that has one —
        a regression, and the item-page half of B5. Distinct from the test
        above, whose item has no location either."""
        shelf = _insert_location(db, "Wishlist Shelf")
        item_id = _insert_item(db, title="Wanted", isbn="9789000020119",
                               location_id=shelf)
        db.execute("DELETE FROM item_copies WHERE item_id = ?", (item_id,))
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert "Location:" in html
        assert "Wishlist Shelf" in html
        assert ">Copies<" not in html
        assert 'id="item-copies" class="hidden"' not in html

    def test_clearing_a_location_leaves_no_false_location_line(self, viewer_client, db):
        """`update_item_fields(..., location_id=None)` keeps the primary copy
        and nulls its location (pinned in tests/test_item_copies.py). The page
        must show the same clean empty state it showed before this fragment
        existed, not the string `Location: None`."""
        office = _insert_location(db, "Office")
        item_id = _insert_item(db, title="Cleared", isbn="9789000020096",
                               location_id=office)
        insert_copy(db, {"item_id": item_id, "copy_number": 1, "location_id": office,
                         "is_primary": 1})
        db.commit()

        update_item_fields(db, item_id, {"location_id": None})
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert "Location:" not in html
        assert ">Copies<" not in html
        assert "None" not in html

    def test_a_lone_unlocated_copy_shows_its_details_and_edit(self, admin_client, db):
        """An import can give an item one copy with a condition and a price
        but no location (LibraryThing). Nothing on the page showed that data
        or opened the copy's editor (test-drive imports-lt-libib, Obs 2)."""
        decoy = _insert_item(db, title="Decoy", isbn="9789000020300")
        insert_copy(db, {"item_id": decoy, "copy_number": 1, "is_primary": 1})
        item_id = _insert_item(db, title="Imported", isbn="9789000020317")
        db.execute("DELETE FROM item_copies WHERE item_id = ?", (item_id,))
        only = insert_copy(db, {"item_id": item_id, "copy_number": 1, "is_primary": 1,
                                "condition": "Fair", "acquired_date": "2019-11-02",
                                "acquisition_source": "Library sale",
                                "acquisition_price": 2.5})
        db.commit()

        html = admin_client.get(f"/item/{item_id}").text

        assert "No location</span> · Fair · 2019-11-02 · Library sale · " in html
        assert f'data-testid="edit-copy-{only}"' in html
        assert "Location:" not in html
        assert ">Copies<" not in html

    def test_a_lone_unlocated_copy_with_details_is_shown_to_a_viewer(
        self, viewer_client, db
    ):
        item_id = _insert_item(db, title="Imported", isbn="9789000020324")
        db.execute("DELETE FROM item_copies WHERE item_id = ?", (item_id,))
        insert_copy(db, {"item_id": item_id, "copy_number": 1, "is_primary": 1,
                         "condition": "Good"})
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert 'id="item-copies" class="hidden"' not in html
        assert "No location</span> · Good" in html
        assert "edit-copy-" not in html

    def test_a_lone_placed_copy_shows_its_details(self, viewer_client, db):
        office = _insert_location(db, "Office")
        item_id = _insert_item(db, title="Placed", isbn="9789000020331",
                               location_id=office)
        db.execute("DELETE FROM item_copies WHERE item_id = ?", (item_id,))
        insert_copy(db, {"item_id": item_id, "copy_number": 1, "is_primary": 1,
                         "location_id": office, "condition": "Very good"})
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert "Office</a> · Very good" in html

    def test_a_viewer_sees_the_block_and_it_carries_no_controls(
        self, viewer_client, db
    ):
        """Item detail is `viewer`; every copy mutation is `editor`. A control
        rendered to a viewer produces a 403 on click, which is the failure
        that split exists to avoid — so the viewer's page must carry none.

        Asserted against the viewer's *rendered page* rather than the fragment
        source: since the copies surface landed, the source legitimately holds
        controls, and they are gated by role at render time. That is the thing
        worth pinning."""
        office = _insert_location(db, "Office")
        loft = _insert_location(db, "Loft")
        item_id = _insert_item(db, title="Read Only", isbn="9789000020102",
                               location_id=office)
        insert_copy(db, {"item_id": item_id, "copy_number": 1, "location_id": office,
                         "is_primary": 1})
        insert_copy(db, {"item_id": item_id, "copy_number": 2, "location_id": loft})
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert 'id="item-copies"' in html
        assert "Loft" in html
        assert 'data-testid="add-copy"' not in html
        assert "/api/items/%d/copies" % item_id not in html

    def test_an_editor_gets_an_add_control_on_every_arm(self, admin_client, db):
        """placed, listed, legacy and the empty state all offer Add copy. The
        empty state is the one that changed: an owned but unlocated item is
        exactly where "I own two of these" starts, so it renders for an editor
        rather than hiding."""
        office = _insert_location(db, "Office")
        loft = _insert_location(db, "Loft")

        placed = _insert_item(db, title="Placed", isbn="9789000020126",
                              location_id=office)
        insert_copy(db, {"item_id": placed, "copy_number": 1,
                         "location_id": office, "is_primary": 1})

        listed = _insert_item(db, title="Listed", isbn="9789000020133",
                              location_id=office)
        insert_copy(db, {"item_id": listed, "copy_number": 1,
                         "location_id": office, "is_primary": 1})
        insert_copy(db, {"item_id": listed, "copy_number": 2, "location_id": loft})

        legacy = _insert_item(db, title="Legacy", isbn="9789000020140",
                              location_id=office)
        db.execute("DELETE FROM item_copies WHERE item_id = ?", (legacy,))

        empty = _insert_item(db, title="Empty", isbn="9789000020157")
        db.execute("DELETE FROM item_copies WHERE item_id = ?", (empty,))
        db.commit()

        for item_id in (placed, listed, legacy, empty):
            html = admin_client.get(f"/item/{item_id}").text
            assert 'data-testid="add-copy"' in html, f"no Add copy on item {item_id}"
            assert f'hx-post="/api/items/{item_id}/copies"' in html
            # `hidden` is a CSS class, so the control is in the markup either
            # way — asserting only on the markup passes against a block that
            # is hidden from the editor who is supposed to use it.
            assert 'id="item-copies" class="hidden"' not in html, (
                f"the block is hidden from an editor on item {item_id}"
            )

    def test_the_empty_state_stays_hidden_for_a_viewer(self, viewer_client, db):
        """The wrapper is only `hidden` when there is nothing to show *and*
        the user cannot edit — otherwise an empty grid cell shifts the cells
        after it into the wrong column."""
        item_id = _insert_item(db, title="Nothing", isbn="9789000020164")
        db.execute("DELETE FROM item_copies WHERE item_id = ?", (item_id,))
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert 'id="item-copies" class="hidden"' in html

    def test_the_add_form_offers_every_location_and_a_no_location_option(
        self, admin_client, db
    ):
        _insert_location(db, "Attic")
        _insert_location(db, "Basement")
        item_id = _insert_item(db, title="Picker", isbn="9789000020171")
        db.commit()

        html = admin_client.get(f"/item/{item_id}").text

        assert '<option value="">No location</option>' in html
        assert ">Attic<" in html and ">Basement<" in html

    def test_every_control_settles_its_own_swap_destination(self):
        """G54 — a control in a swapped-in fragment has no ancestor outside it,
        so an omitted target swaps the whole block into the control itself.
        The regex reads each element's own opening tag; a page-level
        `assert "hx-target" in html` is satisfiable by any sibling and defends
        nothing."""
        fragments = Path(__file__).resolve().parents[1] / "app" / "templates" / "fragments"
        # Both halves of the surface: the collapsed block and the panel that
        # replaces it. The panel is swapped-in content twice over, so its
        # Save, Cancel and Remove have no ancestor inside the page at all.
        for name in ("item_copies.html", "item_copy_edit.html"):
            body = re.sub(r"\{#.*?#\}", "", (fragments / name).read_text(), flags=re.S)
            issuing = re.findall(r"<[a-z]+\b[^>]*hx-(?:post|get|put|delete)=[^>]*>",
                                 body, re.S)
            assert issuing, f"{name} should issue at least one request"
            for tag in issuing:
                assert re.search(r'hx-target|hx-swap="(none|outerHTML)"', tag), \
                    f"{name}: {tag}"

    def test_a_location_path_links_to_browse_on_every_arm(self, viewer_client, db):
        """The link carries the registered `location_filter` parameter — held
        to the registry by tests/test_nav.py — and scopes to that node only."""
        office = _insert_location(db, "Office")
        loft = _insert_location(db, "Loft")

        placed = _insert_item(db, title="Placed Link", isbn="9789000020188",
                              location_id=office)
        insert_copy(db, {"item_id": placed, "copy_number": 1,
                         "location_id": office, "is_primary": 1})

        listed = _insert_item(db, title="Listed Link", isbn="9789000020195",
                              location_id=office)
        insert_copy(db, {"item_id": listed, "copy_number": 1,
                         "location_id": office, "is_primary": 1})
        insert_copy(db, {"item_id": listed, "copy_number": 2, "location_id": loft})

        legacy = _insert_item(db, title="Legacy Link", isbn="9789000020201",
                              location_id=office)
        db.execute("DELETE FROM item_copies WHERE item_id = ?", (legacy,))
        db.commit()

        assert f"/browse?location_filter={office}" in viewer_client.get(
            f"/item/{placed}").text
        listed_html = viewer_client.get(f"/item/{listed}").text
        assert f"/browse?location_filter={office}" in listed_html
        assert f"/browse?location_filter={loft}" in listed_html
        assert f"/browse?location_filter={office}" in viewer_client.get(
            f"/item/{legacy}").text

    def test_every_row_offers_an_editor_its_own_edit_control(self, admin_client, db):
        """Each row's control names its own copy id. Several rows carry the
        same label, so a control that pointed at a shared route would open the
        wrong copy's panel — and nothing on the page would look wrong."""
        office = _insert_location(db, "Office")
        loft = _insert_location(db, "Loft")
        item_id = _insert_item(db, title="Two Rows", isbn="9789000020225",
                               location_id=office)
        first = insert_copy(db, {"item_id": item_id, "copy_number": 1,
                                 "location_id": office, "is_primary": 1})
        second = insert_copy(db, {"item_id": item_id, "copy_number": 2,
                                  "location_id": loft})
        db.commit()

        html = admin_client.get(f"/item/{item_id}").text

        for copy_id in (first, second):
            assert f'data-testid="edit-copy-{copy_id}"' in html
            assert f'hx-get="/api/items/{item_id}/copies/{copy_id}/edit"' in html

    def test_a_single_copy_line_also_offers_edit(self, admin_client, db):
        office = _insert_location(db, "Office")
        # A decoy copy on another item first, so this item's copy is not id 1.
        # `item_copies.id` is AUTOINCREMENT and each test gets a fresh
        # database, so without the decoy a control hardcoded to `/copies/1/`
        # satisfies the assertion below by coincidence — the pin would pass
        # against a control that opens the wrong copy's panel.
        decoy = _insert_item(db, title="Decoy", isbn="9789000020256")
        insert_copy(db, {"item_id": decoy, "copy_number": 1, "is_primary": 1})
        item_id = _insert_item(db, title="One Row", isbn="9789000020232",
                               location_id=office)
        only = insert_copy(db, {"item_id": item_id, "copy_number": 1,
                                "location_id": office, "is_primary": 1})
        assert only != 1, "the decoy should have taken id 1"
        db.commit()

        html = admin_client.get(f"/item/{item_id}").text

        assert f'data-testid="edit-copy-{only}"' in html
        # The destination, not just the marker: a control carrying the right
        # test id and the wrong copy id opens someone else's panel, and
        # nothing on the page looks wrong.
        assert f'hx-get="/api/items/{item_id}/copies/{only}/edit"' in html

    def test_a_viewer_gets_no_edit_control(self, viewer_client, db):
        office = _insert_location(db, "Office")
        loft = _insert_location(db, "Loft")
        item_id = _insert_item(db, title="No Edit", isbn="9789000020249",
                               location_id=office)
        insert_copy(db, {"item_id": item_id, "copy_number": 1,
                         "location_id": office, "is_primary": 1})
        insert_copy(db, {"item_id": item_id, "copy_number": 2, "location_id": loft})
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert "data-testid=\"edit-copy-" not in html
        assert "/edit" not in html

    def test_a_copy_with_no_location_gets_no_link(self, viewer_client, db):
        office = _insert_location(db, "Office")
        item_id = _insert_item(db, title="Half Linked", isbn="9789000020218",
                               location_id=office)
        insert_copy(db, {"item_id": item_id, "copy_number": 1,
                         "location_id": office, "is_primary": 1})
        insert_copy(db, {"item_id": item_id, "copy_number": 2})
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert "No location" in html
        assert "/browse?location_filter=None" not in html


class TestAuthorLinks:
    """T7 — each author of a co-authored item links separately to Browse's
    per-author filter, rather than one link searching the whole joined
    string (which for a co-authored book matched only that exact pairing)."""

    @staticmethod
    def _author_ids(db, item_id):
        return [r[0] for r in db.execute(
            "SELECT author_id FROM item_authors WHERE item_id = ? ORDER BY position",
            (item_id,))]

    def test_two_authors_render_separate_links_in_order_with_role(
        self, viewer_client, db
    ):
        item_id = insert_item(
            db, title="Three-Body", authors="Cixin Liu, Ken Liu - translator")
        db.commit()
        ids = self._author_ids(db, item_id)

        html = viewer_client.get(f"/item/{item_id}").text

        first = html.index(f"/browse?author_filter={ids[0]}")
        second = html.index(f"/browse?author_filter={ids[1]}")
        assert first < second
        assert "Cixin Liu" in html
        assert "Ken Liu" in html
        assert "&middot; translator" in html
        assert "?q=" not in html

    def test_single_author_renders_exactly_one_link(self, viewer_client, db):
        item_id = insert_item(db, title="Solo", authors="Kent Beck")
        db.commit()
        author_id = self._author_ids(db, item_id)[0]

        html = viewer_client.get(f"/item/{item_id}").text

        assert html.count("/browse?author_filter=") == 1
        assert f"/browse?author_filter={author_id}" in html
        assert "Kent Beck" in html

    def test_index_rows_missing_falls_back_to_unlinked_text(
        self, viewer_client, db
    ):
        """The index is derived; if its rows are gone (should not happen),
        the page still shows the stored string, just without a link."""
        item_id = insert_item(db, title="Orphaned", authors="Some Author")
        db.commit()
        db.execute("DELETE FROM item_authors WHERE item_id = ?", (item_id,))
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert "Some Author" in html
        assert "/browse?author_filter=" not in html

    def test_author_name_is_html_escaped(self, viewer_client, db):
        item_id = insert_item(db, title="Escaped", authors='<b>Evil</b>, "Quoted" Name')
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert "<b>Evil</b>" not in html
        assert "&lt;b&gt;Evil&lt;/b&gt;" in html
        assert '"Quoted" Name' not in html
        assert "&#34;Quoted&#34; Name" in html or "&quot;Quoted&quot; Name" in html


class TestAsinRow:
    def test_admin_sees_the_asin_row(self, admin_client, db):
        item_id = _insert_item(
            db, title="Audible Twin", isbn=None, media_type="audiobook", asin="B0D1234567",
        )
        db.commit()

        html = admin_client.get(f"/item/{item_id}").text

        assert '<dt class="text-shelf-muted">asin</dt><dd>B0D1234567</dd>' in html

    def test_viewer_does_not_see_the_panel_or_the_asin(self, viewer_client, db):
        item_id = _insert_item(
            db, title="Audible Twin V", isbn=None, media_type="audiobook", asin="B0D1234567",
        )
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert 'data-testid="integration-ids"' not in html
        assert "B0D1234567" not in html


class TestFillFromAudibleControl:
    BUTTON = 'data-testid="fill-from-audible"'

    def test_editor_sees_it_on_an_audiobook_with_an_asin(self, editor_client, db):
        item_id = _insert_item(db, title="Audio", isbn=None, asin="B08G9PRS1K", media_type="audiobook")
        db.commit()
        html = editor_client.get(f"/item/{item_id}").text
        assert self.BUTTON in html
        assert f'hx-post="/api/items/{item_id}/fill-from-audible"' in html

    def test_viewer_does_not(self, viewer_client, db):
        item_id = _insert_item(db, title="Audio", isbn=None, asin="B08G9PRS1K", media_type="audiobook")
        db.commit()
        assert self.BUTTON not in viewer_client.get(f"/item/{item_id}").text

    @pytest.mark.parametrize("fields", [
        {"asin": None, "media_type": "audiobook"},
        {"asin": "B08G9PRS1K", "media_type": "book"},
    ])
    def test_absent_without_an_audiobook_asin(self, editor_client, db, fields):
        item_id = _insert_item(db, title="Audio", isbn=None, **fields)
        db.commit()
        assert self.BUTTON not in editor_client.get(f"/item/{item_id}").text
