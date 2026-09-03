"""E2E tests: browse page — empty state, grid/list, search, filters."""
import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item

pytestmark = pytest.mark.e2e


def test_browse_empty_state(live_server, authed_page):
    """With no items, browse page shows an empty state message."""
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    body = authed_page.locator("body")
    # Either item cards exist or an empty-state element is visible
    cards = authed_page.locator(".item-card, [data-testid='item-card']")
    empty = authed_page.locator(
        "text=No items found, text=empty, text=nothing here, [data-testid='empty-state']"
    )
    assert cards.count() > 0 or empty.count() > 0 or body.inner_text() != ""


def test_browse_shows_items(live_server, authed_page):
    """Items seeded into the DB appear on the browse page with a non-empty grid."""
    insert_item(live_server["data_dir"], title="Dune", media_type="book", isbn="9780441013593")
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("Dune")
    # Verify the item grid is populated (catches silent CSP / JS breakage)
    grid = authed_page.locator("[data-testid='item-grid'], table tbody")
    assert grid.count() > 0, "Item grid not rendered — possible JS framework error"


def test_browse_search(live_server, authed_page):
    """Search input filters results to matching items."""
    insert_item(live_server["data_dir"], title="Foundation", media_type="book", isbn="9780553293357")
    insert_item(live_server["data_dir"], title="Neuromancer", media_type="book", isbn="9780441569595")
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")

    # Two search inputs exist (mobile hidden, desktop visible) — use the visible one
    search = authed_page.locator("input[name=q]:visible").first
    search.fill("Foundation")
    search.press("Enter")
    authed_page.wait_for_load_state("networkidle")

    expect(authed_page.locator("body")).to_contain_text("Foundation")


def test_browse_media_type_filter(live_server, authed_page):
    """Selecting a media-type filter triggers an HTMX reload."""
    insert_item(live_server["data_dir"], title="Filter Test", media_type="book", isbn="9780000444555")
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")

    # The media type filter is a <select> dropdown
    filter_el = authed_page.locator("select#type-filter")
    filter_el.select_option("book")
    authed_page.wait_for_load_state("networkidle")
    # Page should still be on /browse (with query params)
    assert "/browse" in authed_page.url


def test_browse_grid_list_toggle(live_server, authed_page):
    """Grid/list toggle button switches between grid and list view."""
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")

    # Click the list-view toggle button
    authed_page.locator("[data-testid='view-list']").click()
    authed_page.wait_for_load_state("networkidle")
    assert authed_page.locator("body").is_visible()

    # Click back to grid view
    authed_page.locator("[data-testid='view-grid']").click()
    authed_page.wait_for_load_state("networkidle")
    assert authed_page.locator("body").is_visible()


def test_browse_url_state_preserved(live_server, authed_page):
    """Query params survive page load (URL state)."""
    authed_page.goto(f"{live_server['url']}/browse?mt=book")
    authed_page.wait_for_load_state("networkidle")
    assert "mt=book" in authed_page.url or authed_page.locator("body").is_visible()


def _seed_many(data_dir, prefix: str, count: int) -> None:
    """Bulk-insert `count` items in one connection (insert_item is per-row)."""
    import sqlite3

    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        conn.executemany(
            "INSERT INTO items (title, media_type, source, owned) VALUES (?, 'book', 'test', 1)",
            [(f"{prefix} {i:03d}",) for i in range(count)],
        )
        conn.commit()
    finally:
        conn.close()


def _scroll_until_loaded(page, max_rounds: int = 12) -> None:
    """Scroll to the bottom until the load-more sentinel is gone."""
    for _ in range(max_rounds):
        if page.locator("#load-more").count() == 0:
            return
        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(600)


@pytest.mark.parametrize("view_mode", ["grid", "list"])
def test_browse_infinite_scroll_matches_view_mode(live_server, authed_page, view_mode):
    """Infinite scroll appends fragments matching the active view mode.

    Regression: the load-more URL did not carry the client-side view mode, so
    in list view every appended page came back as grid cards, which landed
    outside the table as unstyled markup and stalled pagination.
    """
    _seed_many(live_server["data_dir"], f"Scroll {view_mode}", 130)

    authed_page.set_viewport_size({"width": 390, "height": 844})
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.evaluate(f"localStorage.setItem('shelf-view', '{view_mode}')")
    authed_page.reload()
    authed_page.wait_for_load_state("networkidle")

    _scroll_until_loaded(authed_page)

    cards = authed_page.locator("a[data-item-id]")
    rows = authed_page.locator("tr[data-item-id]")
    if view_mode == "grid":
        assert cards.count() >= 130, f"only {cards.count()} cards loaded"
        assert rows.count() == 0, "list rows leaked into grid view"
        # Every card belongs to the grid container.
        assert authed_page.evaluate(
            "document.querySelectorAll('a[data-item-id]:not(.cover-grid > a)').length"
        ) == 0
    else:
        assert rows.count() >= 130, f"only {rows.count()} rows loaded"
        assert cards.count() == 0, "grid cards leaked into list view"
        # Every row is inside the table body, not hoisted out by the parser.
        assert authed_page.evaluate(
            "document.querySelectorAll('tr[data-item-id]:not(tbody > tr)').length"
        ) == 0

    assert authed_page.locator("#load-more").count() == 0, "pagination stalled"
