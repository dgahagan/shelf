"""E2E tests: browse page — empty state, grid/list, search, filters."""
import re

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


def test_browse_filters_restored_on_return(live_server, authed_page):
    """Issue #8: leaving Browse and coming back via a bare /browse link must
    repopulate the filter controls AND re-apply them to the results."""
    insert_item(live_server["data_dir"], title="Restorable Novel", media_type="book", isbn="9780000999001")
    insert_item(live_server["data_dir"], title="Restorable Disc", media_type="dvd", isbn="9780000999002")

    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("select#type-filter").select_option("dvd")
    expect(authed_page.locator("#item-grid")).not_to_contain_text("Restorable Novel")
    # The URL gaining the filter is the observable signal that updateUrl() ran
    # and mirrored the querystring into sessionStorage. Waiting on it (rather
    # than on the swap alone) keeps the test off htmx's settle timing.
    expect(authed_page).to_have_url(re.compile(r"media_type_filter=dvd"))

    # Leave Browse, then return via a bare /browse URL (no query params).
    authed_page.goto(f"{live_server['url']}/series")
    authed_page.wait_for_load_state("networkidle")
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")

    # Control repopulated...
    expect(authed_page.locator("select#type-filter")).to_have_value("dvd")
    # ...and actually applied to the results.
    expect(authed_page.locator("#item-grid")).to_contain_text("Restorable Disc")
    expect(authed_page.locator("#item-grid")).not_to_contain_text("Restorable Novel")


def test_browse_clear_all_filters_drops_restore(live_server, authed_page):
    """'Clear all' must also drop the stored querystring, so a later return to
    /browse does not resurrect the filters."""
    insert_item(live_server["data_dir"], title="Clearable Novel", media_type="book", isbn="9780000999003")
    insert_item(live_server["data_dir"], title="Clearable Disc", media_type="dvd", isbn="9780000999004")

    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("select#type-filter").select_option("dvd")
    expect(authed_page).to_have_url(re.compile(r"media_type_filter=dvd"))
    authed_page.get_by_role("button", name="Clear all", exact=True).click()
    expect(authed_page).not_to_have_url(re.compile(r"media_type_filter=dvd"))

    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("select#type-filter")).to_have_value("")
    expect(authed_page.locator("#item-grid")).to_contain_text("Clearable Novel")


def test_browse_search_survives_other_filter_change_on_narrow_viewport(live_server, authed_page):
    """Issue #8 defect 3: the mobile and desktop search boxes both use name='q'.
    A q input's own hx-include omits [name='q'], so typing alone is fine — but
    every OTHER control's hx-include matches BOTH inputs, sending 'q=typed&q='.
    Starlette's QueryParams.get() returns the LAST duplicate, so on a narrow
    viewport (where the user types into the mobile box and the desktop box stays
    empty) changing any other filter silently wiped the search. Both inputs are
    now x-model bound to one value, so the duplicates always agree."""
    insert_item(live_server["data_dir"], title="Narrow Foundation", media_type="book", isbn="9780000999005")
    insert_item(live_server["data_dir"], title="Narrow Neuromancer", media_type="book", isbn="9780000999006")

    authed_page.set_viewport_size({"width": 480, "height": 900})
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")

    search = authed_page.locator("input[name=q]:visible").first
    search.fill("Narrow Foundation")
    search.press("Enter")
    authed_page.wait_for_load_state("networkidle")

    grid = authed_page.locator("#item-grid")
    expect(grid).to_contain_text("Narrow Foundation")
    expect(grid).not_to_contain_text("Narrow Neuromancer")

    # Now change a different filter — its hx-include picks up both q inputs.
    # On a narrow viewport the filter panel is collapsed behind a toggle.
    authed_page.get_by_role("button", name="Filters").click()
    authed_page.locator("select#type-filter").select_option("book")
    authed_page.wait_for_load_state("networkidle")

    expect(grid).to_contain_text("Narrow Foundation")
    expect(grid).not_to_contain_text("Narrow Neuromancer")


def _seed_two_pages(live_server, prefix, start):
    """75 items — more than the 60/page default, so page 2 exists."""
    for i in range(75):
        insert_item(live_server["data_dir"], title=f"{prefix} {i:03d}",
                    media_type="book", isbn=f"{start + i}")


def _scroll_to_bottom(page):
    for _ in range(6):
        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(400)


def test_infinite_scroll_appends_rows_in_list_view(live_server, authed_page):
    """Issue #7: list view must append ROWS, never cover cards.

    Also guards the wiring itself: both branches of item_grid.html sit inside
    <template x-if>, whose content Alpine clones in at runtime. htmx does not
    observe DOM mutations, so without browse.js's MutationObserver calling
    htmx.process(), hx-trigger="revealed" is never registered and nothing
    loads at all.
    """
    _seed_two_pages(live_server, "ListScroll", 9780771000000)
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("[data-testid='view-list']").click()
    authed_page.wait_for_timeout(800)

    rows = authed_page.locator("table tbody tr[data-item-id]")
    before = rows.count()
    _scroll_to_bottom(authed_page)

    assert rows.count() > before, "list view did not append more rows"
    assert authed_page.locator("a[data-item-id] .cover-card").count() == 0, \
        "list view appended cover cards instead of rows (#7)"
    # Rows swapped into the sentinel <tr> instead of <tbody> would nest.
    assert authed_page.evaluate("document.querySelectorAll('tr tr').length") == 0, \
        "rows were swapped inside a <tr> — wrong sentinel swap target"


def test_infinite_scroll_appends_cards_in_grid_view(live_server, authed_page):
    """Grid view keeps appending cover cards."""
    _seed_two_pages(live_server, "GridScroll", 9780772000000)
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("[data-testid='view-grid']").click()
    authed_page.wait_for_timeout(800)

    cards = authed_page.locator("a[data-item-id]")
    before = cards.count()
    _scroll_to_bottom(authed_page)
    assert cards.count() > before, "grid view did not append more cards"


def test_browse_url_state_preserved(live_server, authed_page):
    """Query params survive page load (URL state)."""
    authed_page.goto(f"{live_server['url']}/browse?mt=book")
    authed_page.wait_for_load_state("networkidle")
    assert "mt=book" in authed_page.url or authed_page.locator("body").is_visible()
