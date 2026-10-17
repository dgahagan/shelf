"""Every reader that means "wishlist" reads the list, not `owned = 0` (#125).

The discriminating row is the whole point of this file: an item with
`owned = 0` and **no** wishlist membership. Under the old readers it was
indistinguishable from a wishlisted item; under the new ones every reader
must exclude it. It is seeded with raw SQL on purpose, so it stays bare even
after `tests/conftest.py`'s `_insert_item` grows a `wishlisted=True` keyword
for the member rows in this file to opt into explicitly.

Plan 1 keeps the invariant *on the wishlist ⇔ `owned = 0`*, so this row
cannot arise through any app writer yet. Plan 2 makes it a real, reachable
state; these pins are what prove the readers are already ready for it.
"""

import re

import pytest

from app.services import lists
from tests.conftest import _insert_item


@pytest.fixture
def rows(db):
    """A member and a bare `owned = 0` row, committed for the request path.

    G48: the `db` fixture yields from inside its own `with get_db()` block, so
    without an explicit commit the request — which opens its own connection —
    sees an empty library and every assertion below passes vacuously.
    """
    member = _insert_item(db, title="Member Wishlist Book", isbn="9780000000019",
                          owned=0, wishlisted=True)
    bare = db.execute(
        "INSERT INTO items (title, media_type, owned, source) "
        "VALUES ('Bare Unowned Book', 'book', 0, 'test') RETURNING id"
    ).fetchone()["id"]
    owned = _insert_item(db, title="Owned Book", isbn="9780000000033", owned=1)
    db.commit()

    assert lists.is_member(db, lists.WISHLIST, member)
    assert not lists.is_member(db, lists.WISHLIST, bare)
    return {"member": member, "bare": bare, "owned": owned}


def _stat(html, label):
    """The figure under a named stat tile on /stats.

    Keyed on the label rather than tile order, so re-arranging the page does
    not silently point this at a different number.
    """
    m = re.search(
        rf'>{label}</p>\s*<p class="text-3xl font-bold">(\d+)</p>', html
    )
    assert m, f"no {label!r} tile found"
    return int(m.group(1))


class TestBrowseAndSearch:
    def test_api_search_owned_0_excludes_the_bare_row(self, admin_client, rows):
        html = admin_client.get("/api/search?owned=0").text
        assert "Member Wishlist Book" in html
        assert "Bare Unowned Book" not in html

    def test_browse_owned_0_excludes_the_bare_row(self, admin_client, rows):
        html = admin_client.get("/browse?owned=0").text
        assert "Member Wishlist Book" in html
        assert "Bare Unowned Book" not in html

    def test_browse_owned_1_is_unchanged(self, admin_client, rows):
        """`owned = 1` is still possession and must not consult the list."""
        html = admin_client.get("/browse?owned=1").text
        assert "Owned Book" in html
        assert "Member Wishlist Book" not in html

    def test_api_search_owned_none_returns_exactly_the_bare_row(self, admin_client, rows):
        html = admin_client.get("/api/search?owned=none").text
        assert "Bare Unowned Book" in html
        assert "Member Wishlist Book" not in html
        assert "Owned Book" not in html

    def test_browse_owned_none_returns_exactly_the_bare_row(self, admin_client, rows):
        html = admin_client.get("/browse?owned=none").text
        assert "Bare Unowned Book" in html
        assert "Member Wishlist Book" not in html
        assert "Owned Book" not in html


class TestCounts:
    def test_browse_wishlist_count_excludes_the_bare_row(self, admin_client, rows):
        html = admin_client.get("/browse").text
        # The filter dropdown carries the cross-filter counts.
        assert "Wishlist (1)" in html, html[html.find("owned-filter") - 200:][:600]

    def test_browse_three_owned_counts_sum_to_the_unfiltered_total(self, admin_client, rows):
        """Owned, Wishlist and Not owned or wishlisted are 1/1/1 here — the
        member, the owned row and the bare row each land in exactly one —
        and must sum to the unfiltered total of 3."""
        html = admin_client.get("/browse").text
        chunk = html[html.find("owned-filter") - 200:][:1200]
        assert "Owned (1)" in chunk, chunk
        assert "Wishlist (1)" in chunk, chunk
        assert "Not owned or wishlisted (1)" in chunk, chunk

    def test_home_wishlist_count_excludes_the_bare_row(self, db, rows):
        from app.services.home_dashboard import dashboard_summary

        summary = dashboard_summary(db)
        assert summary["wishlist_count"] == 1   # membership, not owned = 0
        assert summary["total_count"] == 3
        assert summary["owned_count"] == 1      # possession is unchanged

    def test_home_per_type_wishlist_count_excludes_the_bare_row(self, db, rows):
        from app.services.home_dashboard import dashboard_summary

        books = [t for t in dashboard_summary(db)["media_types"]
                 if t["media_type"] == "book"][0]
        assert books["item_count"] == 3
        assert books["wishlist_count"] == 1

    def test_stats_wishlist_count_excludes_the_bare_row(self, admin_client, rows):
        """Only the *count* excludes it. The bare row still appears in the
        page's recent-additions grid, which is a list of items and not a
        statement about the wishlist."""
        assert _stat(admin_client.get("/stats").text, "Wishlist") == 1

    def test_stats_owned_figure_counts_owned_directly(self, admin_client, rows):
        """Plan 2 (#125) makes the bare row a real, reachable "neither" state,
        so `stats_owned = total - stats_wishlist` is no longer correct — that
        arithmetic would count the bare row as owned. `pages.py` now runs its
        own `COUNT(*) WHERE owned = 1`, so the figure is 1 (only "Owned
        Book"), not 2."""
        assert _stat(admin_client.get("/stats").text, "Owned") == 1


class TestShareLink:
    def _token(self, admin_client, scope):
        """Mirrors tests/test_share_links.py::_create_link — the route answers
        303 to /settings, so the token comes from the row, not the response."""
        from app.database import get_db

        resp = admin_client.post("/api/share", data={"scope": scope, "label": "T"},
                                 follow_redirects=False)
        assert resp.status_code == 303
        with get_db() as db:
            return db.execute(
                "SELECT token FROM share_links ORDER BY id DESC LIMIT 1"
            ).fetchone()["token"]

    def test_wishlist_share_excludes_the_bare_row(self, admin_client, rows):
        token = self._token(admin_client, "wishlist")
        html = admin_client.get(f"/share/{token}").text
        assert "Member Wishlist Book" in html
        assert "Bare Unowned Book" not in html

    def test_collection_share_is_still_possession(self, admin_client, rows):
        token = self._token(admin_client, "collection")
        html = admin_client.get(f"/share/{token}").text
        assert "Owned Book" in html
        assert "Member Wishlist Book" not in html
        assert "Bare Unowned Book" not in html

    def test_share_keeps_its_noindex_header(self, admin_client, rows):
        """The public path is a risk floor: the swap changed the WHERE clause
        and nothing else."""
        token = self._token(admin_client, "wishlist")
        resp = admin_client.get(f"/share/{token}")
        assert resp.headers["X-Robots-Tag"] == "noindex"

    def test_an_unknown_token_still_404s_with_noindex(self, admin_client, rows):
        resp = admin_client.get("/share/nope-not-a-token")
        assert resp.status_code == 404
        assert resp.headers["X-Robots-Tag"] == "noindex"


class TestItemPage:
    """T9: the item page badge (item_detail.html:52) reads `item.wishlisted`."""

    def test_item_page_badge_present_for_member(self, admin_client, rows):
        html = admin_client.get(f"/item/{rows['member']}").text
        assert 'data-testid="wishlist-badge"' in html

    def test_item_page_badge_absent_for_bare_row(self, admin_client, rows):
        html = admin_client.get(f"/item/{rows['bare']}").text
        assert 'data-testid="wishlist-badge"' not in html


class TestSearchCard:
    """T9: `/api/search`'s card ribbon (item_card.html:4)."""

    _CARD_RIBBON = 'text-black shadow-sm">Wishlist</div>'

    def test_card_badge_present_for_member(self, admin_client, rows):
        html = admin_client.get("/api/search?q=Member+Wishlist+Book").text
        assert "Member Wishlist Book" in html
        assert self._CARD_RIBBON in html

    def test_card_badge_absent_for_bare_row(self, admin_client, rows):
        html = admin_client.get("/api/search?q=Bare+Unowned+Book").text
        assert "Bare Unowned Book" in html
        assert self._CARD_RIBBON not in html


class TestListViewRows:
    """T9: `?view=list` rows (item_row.html:49)."""

    _ROW_RIBBON = 'text-black shrink-0">Wishlist</span>'

    def test_row_badge_present_for_member(self, admin_client, rows):
        html = admin_client.get("/api/search?q=Member+Wishlist+Book&view=list").text
        assert "Member Wishlist Book" in html
        assert self._ROW_RIBBON in html

    def test_row_badge_absent_for_bare_row(self, admin_client, rows):
        html = admin_client.get("/api/search?q=Bare+Unowned+Book&view=list").text
        assert "Bare Unowned Book" in html
        assert self._ROW_RIBBON not in html


class TestHomeRecent:
    """T9: Home's recent-items grid (home.html:105).

    Isolated single-item inserts, not the shared `rows` fixture — Home has no
    query filter to isolate one item's card from another's, and the ribbon
    markup is identical for every card, so a shared fixture could not tell
    "no badge on the bare row" from "the member's badge happens to be here
    too". One item per test makes each assertion mean what it says.
    """

    _RIBBON = 'text-black shadow-sm">Wishlist</div>'

    def test_recent_grid_badge_present_for_member(self, admin_client, db):
        _insert_item(db, title="Home Wishlist Item", isbn="9780000000051", owned=0,
                     wishlisted=True)
        db.commit()

        html = admin_client.get("/").text
        assert "Home Wishlist Item" in html
        assert self._RIBBON in html

    def test_recent_grid_badge_absent_for_bare_row(self, admin_client, db):
        db.execute(
            "INSERT INTO items (title, media_type, owned, source) "
            "VALUES ('Home Bare Item', 'book', 0, 'test')"
        )
        db.commit()

        html = admin_client.get("/").text
        assert "Home Bare Item" in html
        assert self._RIBBON not in html


class TestSeriesPage:
    """T9: the series page's volume strip (series.html:202).

    One series per test, same isolation reasoning as TestHomeRecent — the
    series page has no query filter either.
    """

    _RIBBON = 'text-black text-[9px] px-1 rounded-sm font-medium">Wishlist</span>'

    def test_series_badge_present_for_member(self, admin_client, db):
        _insert_item(db, title="Series Wishlist Item", isbn="9780000000052",
                     owned=0, wishlisted=True, series_name="Solo Saga")
        db.commit()

        html = admin_client.get("/series").text
        assert "Solo Saga" in html
        assert self._RIBBON in html

    def test_series_badge_absent_for_bare_row(self, admin_client, db):
        db.execute(
            "INSERT INTO items (title, media_type, owned, source, series_name) "
            "VALUES ('Series Bare Item', 'book', 0, 'test', 'Solo Saga Two')"
        )
        db.commit()

        html = admin_client.get("/series").text
        assert "Solo Saga Two" in html
        assert self._RIBBON not in html

    def test_series_page_counts_owned_and_wishlisted_on_their_own(self, admin_client, db):
        """T4: `series.py` computes `wishlist_count` beside `owned_count`
        rather than deriving it as `items - owned_count`. A series with one
        owned volume, one wishlisted volume and one bare "neither" volume
        must print "1 owned" and "1 wishlisted" — the bare row (#125's third
        state) is what tells the derivation apart from the direct count:
        `items - owned_count` would say "2 wishlisted" here, counting the
        bare row as wishlisted."""
        _insert_item(db, title="Mixed Saga Owned", isbn="9780000000060",
                     owned=1, series_name="Mixed Saga")
        _insert_item(db, title="Mixed Saga Wishlist", isbn="9780000000061",
                     owned=0, wishlisted=True, series_name="Mixed Saga")
        _insert_item(db, title="Mixed Saga Neither", isbn="9780000000062",
                     owned=0, series_name="Mixed Saga")
        db.commit()

        html = admin_client.get("/series").text
        assert "Mixed Saga" in html
        assert "1 owned" in html
        assert "1 wishlisted" in html
        assert "2 wishlisted" not in html


class TestSeriesCheck:
    """`/api/series/check` answers owned / wishlist / missing per book."""

    def test_a_bare_unowned_row_is_not_reported_as_wishlist(self, admin_client, db):
        """Plan 2's third arm: a bare `owned = 0` row with no wishlist
        membership is neither owned nor wishlisted, so the series check
        answers `missing` for it — not `owned` (plan 1's two-way split,
        before the third arm existed) and not `wishlist` (the row is not a
        member)."""
        from unittest.mock import AsyncMock, patch

        db.execute(
            "INSERT INTO items (title, media_type, owned, source, series_name) "
            "VALUES ('Bare Sequel', 'book', 0, 'test', 'Test Saga')"
        )
        member = _insert_item(db, title="Wanted Sequel", isbn="9780000000040",
                              owned=0, wishlisted=True, series_name="Test Saga")
        assert lists.is_member(db, lists.WISHLIST, member)
        db.commit()

        from app.database import get_db
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES "
                "('hardcover_token', 'test-token')"
            )

        books = [
            {"hardcover_book_id": 201, "title": "Bare Sequel", "authors": "A",
             "cover_url": None, "year": 2001, "series_position": 1},
            {"hardcover_book_id": 202, "title": "Wanted Sequel", "authors": "A",
             "cover_url": None, "year": 2002, "series_position": 2},
        ]
        with patch("app.services.hardcover.get_series_books",
                   new=AsyncMock(return_value=books)):
            data = admin_client.get("/api/series/check",
                                    params={"name": "Test Saga"}).json()

        assert data["ok"] is True, data
        by_id = {b["hardcover_book_id"]: b["status"] for b in data["books"]}
        assert by_id[201] == "missing"    # bare row: neither owned nor a member
        assert by_id[202] == "wishlist"   # member
