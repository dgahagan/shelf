"""Parity contract between `/browse`'s first paint and `/api/search`'s
first fragment.

Both routes now derive their filter values, WHERE clause and dropdown counts
from `app/browse_filters.py` and `items_common.filter_counts` (see that
module's docstring and G24 in GOTCHAS.md). These tests pin the contract: the
same query string must produce the same dropdown options, the same result
set, the same `q` truncation, and no duplicated filter markup on `/browse`'s
initial render. They also pin that adding a filter needs no route edit.
"""

import re

import pytest

from tests.conftest import _insert_borrower, _insert_item, _insert_location

SELECT_IDS = ("type-filter", "owned-filter", "location-filter", "reading-status-filter")


def _opts(html, sel_id):
    m = re.search(rf'<select id="{sel_id}"[^>]*>(.*?)</select>', html, re.S)
    assert m, f"no <select id={sel_id!r}> found"
    return [
        re.sub(r"\s+", " ", o).strip()
        for o in re.findall(r"<option[^>]*>(.*?)</option>", m.group(1), re.S)
    ]


@pytest.fixture
def seeded_library(db):
    """One fixture library exercising every dimension the parity claim spans.

    Two media types, two locations plus an unlocated item, a wishlist item, an
    item with a `reading_status`, items with two different languages, a tagged
    item and a lent-out one. The last three matter because `language`, `tag`
    and `lent_out` all narrow the WHERE clause and therefore move every
    dropdown count — the parity contract covers *any* query string, so the
    matrix has to reach them.
    """
    loc_a = _insert_location(db, "Shelf A")
    loc_b = _insert_location(db, "Shelf B")

    _insert_item(
        db, title="Book Read", isbn="9780000001001", media_type="book",
        location_id=loc_a, reading_status="read", language="eng",
    )
    _insert_item(
        db, title="Book Two", isbn="9780000001002", media_type="book",
        location_id=loc_b,
    )
    _insert_item(
        db, title="Book Unlocated", isbn="9780000001003", media_type="book",
    )
    _insert_item(
        db, title="DVD One", isbn="9780000002001", media_type="dvd",
        location_id=loc_a,
    )
    _insert_item(
        db, title="DVD Two", isbn="9780000002002", media_type="dvd",
    )
    _insert_item(
        db, title="Wishlist Book", isbn="9780000003001", media_type="book",
        owned=0,
    )
    deu = _insert_item(
        db, title="Deutsches Buch", isbn="9780000004001", media_type="book",
        location_id=loc_b, language="deu",
    )

    # A tagged item and a lent-out one, so `tag=` and `lent_out=` reach a
    # non-empty result set on both routes rather than trivially matching
    # nothing on each.
    tagged = _insert_item(
        db, title="Signed Copy", isbn="9780000005001", media_type="book",
        location_id=loc_a, language="eng",
    )
    tag_id = db.execute("INSERT INTO tags (name) VALUES (?)", ("signed",)).lastrowid
    db.execute("INSERT INTO item_tags (item_id, tag_id) VALUES (?, ?)", (tagged, tag_id))

    borrower = _insert_borrower(db, "Parity Borrower")
    db.execute(
        "INSERT INTO checkouts (item_id, borrower_id) VALUES (?, ?)", (tagged, borrower)
    )

    db.commit()
    return {"loc_a": loc_a, "loc_b": loc_b, "tagged": tagged, "deu": deu}


QUERYSTRINGS = [
    "",
    "owned=0",
    "owned=1",
    "media_type_filter=dvd",
    # location_filter is filled in per-test from the seeded loc_a id.
    "reading_status=read",
    "owned=1&media_type_filter=book",
    # `language`, `tag` and `lent_out` narrow the where-clause exactly as the
    # four dropdown filters do, so they move the counts too. `sort` and `view`
    # narrow nothing, but a route that mishandled either would still diverge.
    "language=eng",
    "language=deu",
    "tag=signed",
    "lent_out=1",
    "sort=title_asc",
    "view=list",
    "language=eng&owned=1",
    "tag=signed&media_type_filter=book",
]


@pytest.mark.parametrize("qs", QUERYSTRINGS)
def test_dropdown_parity(admin_client, seeded_library, qs):
    b = admin_client.get(f"/browse?{qs}").text
    s = admin_client.get(f"/api/search?{qs}").text
    for sel in SELECT_IDS:
        assert _opts(b, sel) == _opts(s, sel), (sel, qs, _opts(b, sel), _opts(s, sel))


def test_dropdown_parity_location_filter(admin_client, seeded_library):
    qs = f"location_filter={seeded_library['loc_a']}"
    b = admin_client.get(f"/browse?{qs}").text
    s = admin_client.get(f"/api/search?{qs}").text
    for sel in SELECT_IDS:
        assert _opts(b, sel) == _opts(s, sel), (sel, qs, _opts(b, sel), _opts(s, sel))


@pytest.mark.parametrize("qs", QUERYSTRINGS)
def test_result_set_parity(admin_client, seeded_library, qs):
    b = admin_client.get(f"/browse?{qs}")
    s = admin_client.get(f"/api/search?{qs}")
    assert b.status_code == 200
    assert s.status_code == 200
    # `/browse` renders the full page; `/api/search` renders the item_grid
    # fragment. Both embed one card per matching item, so count cards the
    # same way in each, and compare totals via the OOB counts payload that
    # `/api/search` (page<=1) emits — `/browse` computes the identical
    # `filtered_total` via the same helper, surfaced in its "type-filter"
    # All Types option text, already checked for parity above. Here we
    # additionally pin raw item-card counts agree.
    b_cards = len(re.findall(r'data-item-id=', b.text))
    s_cards = len(re.findall(r'data-item-id=', s.text))
    assert b_cards == s_cards, (qs, b_cards, s_cards)
    # Non-vacuity: every query string in the matrix matches something, so a
    # zero here means the fixture never committed, not that the routes agree.
    assert b_cards > 0, (qs, b.text[:400])


def test_result_set_parity_location_filter(admin_client, seeded_library):
    qs = f"location_filter={seeded_library['loc_a']}"
    b = admin_client.get(f"/browse?{qs}")
    s = admin_client.get(f"/api/search?{qs}")
    b_cards = len(re.findall(r'data-item-id=', b.text))
    s_cards = len(re.findall(r'data-item-id=', s.text))
    assert b_cards == s_cards, (qs, b_cards, s_cards)
    assert b_cards > 0, (qs, b.text[:400])


def test_q_truncation(admin_client, db, monkeypatch):
    # `q` truncation is enforced *before* the search hits the DB, so a query
    # long enough to trigger it can never literally appear as a substring of
    # a short title — LIKE '%<200 chars>%' only matches a title that itself
    # contains that run of characters. To exercise the load-more URL (which
    # only renders when there is a next page) without seeding 60+ rows, give
    # the titles a matching long run of characters and lower the page size
    # for this test — see CLAUDE.md's "Config import trap": `DEFAULT_PAGE_SIZE`
    # is bound at import time in both route modules, so patch it there.
    from app.routers import items as items_module
    from app.routers import pages as pages_module

    monkeypatch.setattr(pages_module, "DEFAULT_PAGE_SIZE", 2)
    monkeypatch.setattr(items_module, "DEFAULT_PAGE_SIZE", 2)

    run = "y" * 250
    truncated = "y" * 200
    long_q = "y" * 300
    for i in range(4):
        _insert_item(db, title=f"{run}-{i}", isbn=f"978000000900{i}", media_type="book")
    db.commit()

    resp = admin_client.get(f"/browse?q={long_q}")
    html = resp.text

    # Both the desktop and the mobile search input must carry the truncated
    # value.
    values = re.findall(
        r'<input type="search" name="q"[^>]*value="([^"]*)"', html
    )
    assert len(values) == 2, values
    for v in values:
        assert v == truncated, v

    # The load-more URL's q= must also be truncated.
    load_more_urls = re.findall(r'hx-get="(/api/search\?[^"]*)"', html)
    q_bearing = [u for u in load_more_urls if "q=" in u]
    assert q_bearing, (load_more_urls, html)
    for url in q_bearing:
        m = re.search(r"q=([^&]*)", url)
        assert m and m.group(1) == truncated, url


def test_no_duplicate_ids_on_initial_render(admin_client, seeded_library):
    html = admin_client.get("/browse").text
    for sel in SELECT_IDS:
        assert html.count(f'id="{sel}"') == 1, sel
    assert "hx-swap-oob" not in html


def test_new_filter_needs_no_route_edit(admin_client, seeded_library, monkeypatch):
    from app import browse_filters as bf

    extra = bf.BrowseFilter("t4_throwaway_filter", prefix="Throwaway")
    new_filters = bf.FILTERS + (extra,)
    new_by_name = dict(bf.BY_NAME)
    new_by_name[extra.name] = extra
    new_names = bf.FILTER_NAMES + (extra.name,)

    monkeypatch.setattr(bf, "FILTERS", new_filters)
    monkeypatch.setattr(bf, "BY_NAME", new_by_name)
    monkeypatch.setattr(bf, "FILTER_NAMES", new_names)

    assert admin_client.get("/browse").status_code == 200
    assert admin_client.get("/api/search").status_code == 200

    import inspect

    from app.routers.items import search_items
    from app.routers.pages import browse as browse_route

    for route in (search_items, browse_route):
        params = set(inspect.signature(route).parameters)
        assert not params & set(bf.FILTER_NAMES)


@pytest.mark.parametrize("path", ["/browse", "/api/search"])
@pytest.mark.parametrize("value", ["abc", "1.5", "9" * 22])
def test_an_uncastable_location_filter_does_not_500(
    admin_client, seeded_library, path, value
):
    """Issue #40 — a hand-edited filter URL must not crash either route.

    Both fail the same way because both build one WHERE clause, which is #37
    working as intended; both are fixed the same way for the same reason. A
    valid-but-unused id already rendered an empty result, and an uncastable
    one now gives that same answer instead of a 500.
    """
    # Pin what "the same answer" means: an unused id renders no items, and the
    # unfiltered route does render them — so the assertion below is not vacuous.
    assert "Book Read" in admin_client.get(path).text
    unused = admin_client.get(f"{path}?location_filter=999999")
    assert unused.status_code == 200
    assert "Book Read" not in unused.text

    r = admin_client.get(f"{path}?location_filter={value}")
    assert r.status_code == 200
    assert "Book Read" not in r.text
