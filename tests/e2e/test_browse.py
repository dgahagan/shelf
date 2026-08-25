"""E2E tests: browse page — empty state, grid/list, search, filters."""
import json
import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import assert_page_clean, attach_page_guard, insert_item

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


def _login_with_seeded_storage(browser, live_server, setup_admin, storage):
    """Log in through a fresh context with localStorage seeded before first
    paint. `authed_page` builds its context inside the fixture, so it cannot
    be used here: an `add_init_script` has to run before the very first
    navigation, and the page must still go through the real login flow or
    `/browse` just redirects to `/login`. Mirrors `authed_page`'s five-line
    login sequence. Returns (ctx, page) — the caller owns closing the
    context, after an `assert_page_clean(page)` at the end of the test body.
    """
    ctx = browser.new_context()
    script = "\n".join(
        f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)});"
        for k, v in storage.items()
    )
    ctx.add_init_script(script)
    pg = attach_page_guard(ctx.new_page())
    pg.goto(f"{live_server['url']}/login")
    pg.fill("input[name=username]", setup_admin["username"])
    pg.fill("input[name=password]", setup_admin["password"])
    pg.click("button[type=submit]")
    pg.wait_for_url(f"{live_server['url']}/browse", timeout=10_000)
    return ctx, pg


def test_column_picker_only_in_list_view(live_server, authed_page):
    """The column picker is a list-view-only control."""
    insert_item(live_server["data_dir"], title="Picker Visibility Probe",
                media_type="book", isbn="9780000999101")
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")

    picker = authed_page.locator("[data-testid='column-picker']")
    expect(picker).to_be_hidden()

    authed_page.locator("[data-testid='view-list']").click()
    expect(picker).to_be_visible()


def test_hiding_a_column_persists_across_reload(live_server, authed_page):
    """Unticking a column in the picker hides it live and across a reload,
    and is recorded in the shelf-columns localStorage blob."""
    insert_item(live_server["data_dir"], title="Column Hide Probe",
                media_type="book", isbn="9780000999102", authors="Jane Doe")
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("[data-testid='view-list']").click()
    # G42: the list view lives inside <template x-if> — wait for a positive
    # painted state (a locked, always-on column) before asserting hidden.
    expect(authed_page.locator("td[data-col=title]").first).to_be_visible()

    authed_page.locator("[data-testid='column-picker']").click()
    expect(authed_page.locator("[data-testid='column-menu']")).to_be_visible()
    authed_page.locator("input[type=checkbox][data-col=author]").uncheck()

    expect(authed_page.locator("th[data-col=author]")).to_be_hidden()
    author_cells = authed_page.locator("td[data-col=author]")
    # to_be_hidden() passes on a locator that matches NOTHING, so a typo'd
    # selector would read as "hidden" and this pin would defend nothing (G31).
    assert author_cells.count() > 0
    for i in range(author_cells.count()):
        expect(author_cells.nth(i)).to_be_hidden()

    stored = authed_page.evaluate("JSON.parse(localStorage.getItem('shelf-columns'))")
    assert stored is not None and isinstance(stored, dict)
    assert stored.get("author") is False

    authed_page.reload()
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("td[data-col=title]").first).to_be_visible()
    expect(authed_page.locator("th[data-col=author]")).to_be_hidden()
    author_cells = authed_page.locator("td[data-col=author]")
    assert author_cells.count() > 0
    for i in range(author_cells.count()):
        expect(author_cells.nth(i)).to_be_hidden()


def test_reset_restores_defaults(live_server, authed_page):
    """Reset restores the registry's default-on set and drops the stored blob."""
    insert_item(live_server["data_dir"], title="Reset Defaults Probe",
                media_type="book", isbn="9780000999103")
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("[data-testid='view-list']").click()
    expect(authed_page.locator("td[data-col=title]").first).to_be_visible()

    authed_page.locator("[data-testid='column-picker']").click()
    expect(authed_page.locator("[data-testid='column-menu']")).to_be_visible()
    # Hide two default-on columns, enable a default-off one.
    authed_page.locator("input[type=checkbox][data-col=author]").uncheck()
    authed_page.locator("input[type=checkbox][data-col=location]").uncheck()
    authed_page.locator("input[type=checkbox][data-col=value]").check()

    expect(authed_page.locator("th[data-col=author]")).to_be_hidden()
    expect(authed_page.locator("th[data-col=location]")).to_be_hidden()
    expect(authed_page.locator("th[data-col=value]")).to_be_visible()

    authed_page.locator("[data-testid='columns-reset']").click()

    expect(authed_page.locator("th[data-col=author]")).to_be_visible()
    expect(authed_page.locator("th[data-col=location]")).to_be_visible()
    expect(authed_page.locator("th[data-col=value]")).to_be_hidden()

    stored = authed_page.evaluate("localStorage.getItem('shelf-columns')")
    assert stored is None


def test_stale_storage_is_ignored(live_server, browser, setup_admin):
    """A hand-edited/stale shelf-columns blob (unknown name, locked column
    turned off, a name missing entirely) must not break the page or win over
    a locked column — and a missing name falls back to its registry default."""
    insert_item(live_server["data_dir"], title="Stale Storage Probe",
                media_type="book", isbn="9780000999104")
    storage = {
        "shelf-columns": json.dumps({"bogus": True, "title": False, "author": False}),
        "shelf-view": "list",
    }
    ctx, pg = _login_with_seeded_storage(browser, live_server, setup_admin, storage)
    pg.wait_for_load_state("networkidle")

    # Locked column wins over the hand-edited blob.
    expect(pg.locator("td[data-col=title]").first).to_be_visible()
    # Explicitly turned off in the blob.
    expect(pg.locator("td[data-col=author]").first).to_be_hidden()
    # Not mentioned in the blob at all — falls back to its registry default (on).
    expect(pg.locator("td[data-col=media_type]").first).to_be_visible()

    assert_page_clean(pg)
    ctx.close()


def test_two_tabs_do_not_overwrite_each_others_columns(live_server, browser, setup_admin):
    """Two open Browse tabs are concurrent writers of one `shelf-columns` key.

    Both mount holding a complete snapshot. Tab A hides Author; tab B, whose
    in-memory copy predates that, enables Value. If B serialises its stale
    snapshot wholesale, Author comes back and A's choice is silently lost —
    the user only finds out on reload. Both changes must survive.
    """
    insert_item(live_server["data_dir"], title="Two Tab Probe",
                media_type="book", isbn="9780000999107", authors="Jane Doe")
    ctx, page_a = _login_with_seeded_storage(
        browser, live_server, setup_admin, {"shelf-view": "list"}
    )
    page_a.wait_for_load_state("networkidle")
    expect(page_a.locator("td[data-col=title]").first).to_be_visible()

    # Tab B mounts from the same defaults, BEFORE A changes anything — this is
    # what makes its snapshot stale a moment later.
    page_b = attach_page_guard(ctx.new_page())
    page_b.goto(f"{live_server['url']}/browse")
    page_b.wait_for_load_state("networkidle")
    expect(page_b.locator("td[data-col=title]").first).to_be_visible()

    # A hides a default-on column.
    page_a.locator("[data-testid='column-picker']").click()
    expect(page_a.locator("[data-testid='column-menu']")).to_be_visible()
    page_a.locator("input[type=checkbox][data-col=author]").uncheck()
    expect(page_a.locator("th[data-col=author]")).to_be_hidden()

    # B enables a default-off column, still holding its pre-A snapshot.
    page_b.locator("[data-testid='column-picker']").click()
    expect(page_b.locator("[data-testid='column-menu']")).to_be_visible()
    page_b.locator("input[type=checkbox][data-col=value]").check()
    expect(page_b.locator("th[data-col=value]")).to_be_visible()

    stored = page_b.evaluate("JSON.parse(localStorage.getItem('shelf-columns'))")
    assert stored.get("author") is False, \
        "tab B's write resurrected the column tab A hid (lost update)"
    assert stored.get("value") is True, "tab B's own change was not persisted"

    # The reload is the point: it is where a user would discover the loss.
    page_a.reload()
    page_a.wait_for_load_state("networkidle")
    expect(page_a.locator("td[data-col=title]").first).to_be_visible()
    author_cells = page_a.locator("td[data-col=author]")
    # to_be_hidden() passes on a locator matching nothing (G31).
    assert author_cells.count() > 0
    for i in range(author_cells.count()):
        expect(author_cells.nth(i)).to_be_hidden()
    expect(page_a.locator("th[data-col=value]")).to_be_visible()

    assert_page_clean(page_a)
    assert_page_clean(page_b)
    ctx.close()


def test_locked_columns_are_not_in_the_picker(live_server, authed_page):
    """select, cover and title are always-on and never offered in the menu."""
    insert_item(live_server["data_dir"], title="Locked Columns Probe",
                media_type="book", isbn="9780000999105")
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("[data-testid='view-list']").click()
    expect(authed_page.locator("td[data-col=title]").first).to_be_visible()

    authed_page.locator("[data-testid='column-picker']").click()
    menu = authed_page.locator("[data-testid='column-menu']")
    expect(menu).to_be_visible()

    for name in ("select", "cover", "title"):
        expect(menu.locator(f"input[type=checkbox][data-col={name}]")).to_have_count(0)


def test_infinite_scroll_appends_rows_with_custom_columns(live_server, browser, setup_admin):
    """Same regression as test_infinite_scroll_appends_rows_in_list_view, but
    with a non-default column set (value on, author off) seeded into storage
    before load — the one thing the unit suite cannot see: the MutationObserver
    -> htmx.process() path plus Alpine re-evaluating x-show on rows swapped in
    after mount."""
    _seed_two_pages(live_server, "CustomColScroll", 9780773000000)
    storage = {
        "shelf-columns": json.dumps({"value": True, "author": False}),
        "shelf-view": "list",
    }
    ctx, pg = _login_with_seeded_storage(browser, live_server, setup_admin, storage)
    pg.wait_for_load_state("networkidle")
    expect(pg.locator("td[data-col=title]").first).to_be_visible()
    pg.wait_for_timeout(800)

    rows = pg.locator("table tbody tr[data-item-id]")
    before = rows.count()
    _scroll_to_bottom(pg)

    assert rows.count() > before, "list view did not append more rows"
    assert pg.locator("a[data-item-id] .cover-card").count() == 0, \
        "list view appended cover cards instead of rows (#7)"
    assert pg.evaluate("document.querySelectorAll('tr tr').length") == 0, \
        "rows were swapped inside a <tr> — wrong sentinel swap target"

    # The custom column set must apply to rows appended after mount too.
    author_cells = pg.locator("td[data-col=author]")
    after_count = author_cells.count()
    assert after_count > 0
    for i in range(after_count):
        expect(author_cells.nth(i)).to_be_hidden()

    assert_page_clean(pg)
    ctx.close()


def test_browse_has_no_pageerrors_in_either_view(live_server, authed_page):
    """Grid view, then list view with every pickable column switched on —
    zero uncaught page errors in either state."""
    insert_item(live_server["data_dir"], title="No Page Errors Probe",
                media_type="book", isbn="9780000999106")
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("a[data-item-id]").first).to_be_visible()

    authed_page.locator("[data-testid='view-list']").click()
    expect(authed_page.locator("td[data-col=title]").first).to_be_visible()

    authed_page.locator("[data-testid='column-picker']").click()
    menu = authed_page.locator("[data-testid='column-menu']")
    expect(menu).to_be_visible()
    checkboxes = menu.locator("input[type=checkbox][data-col]")
    col_names = [checkboxes.nth(i).get_attribute("data-col") for i in range(checkboxes.count())]
    for i in range(len(col_names)):
        cb = checkboxes.nth(i)
        if not cb.is_checked():
            cb.check()

    for name in col_names:
        expect(authed_page.locator(f"td[data-col={name}]").first).to_be_visible()


def test_browse_url_state_preserved(live_server, authed_page):
    """Query params survive page load (URL state)."""
    authed_page.goto(f"{live_server['url']}/browse?mt=book")
    authed_page.wait_for_load_state("networkidle")
    assert "mt=book" in authed_page.url or authed_page.locator("body").is_visible()


def _reset_browse_storage(page, base_url):
    """Other tests in this module share the live server's browser context, so
    a stale shelf-sort / shelf-view / shelf-browse-qs would decide the outcome
    here. Clear them from the page's own origin before each scenario."""
    page.goto(f"{base_url}/browse")
    page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")


def test_browse_sort_restored_in_new_session(live_server, authed_page):
    """Issue #13: the sort-only restore path (no stored filter querystring —
    e.g. a brand new tab, since sessionStorage is per-tab) set the select's
    value but fired the request with htmx.trigger, which is unreliable at init
    time. The dropdown showed the saved sort while the rows stayed in the
    server's default newest-first order. Both must now agree."""
    # 'Zza' / 'Zzb' keep both items on page 1 under title_desc, and they stay on
    # page 1 under the default order too — so a single pairwise comparison is
    # valid under either ordering.
    insert_item(live_server["data_dir"], title="Zza Sortprobe Alpha", media_type="book", isbn="9780000999011")
    insert_item(live_server["data_dir"], title="Zzb Sortprobe Beta", media_type="book", isbn="9780000999012")

    _reset_browse_storage(authed_page, live_server["url"])
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")

    # Baseline: the unsorted default. Asserted via the control and URL rather
    # than row order, because 'newest' ties on created_at for rows inserted in
    # the same second and the tie-break is not defined.
    grid = authed_page.locator("#item-grid")
    expect(authed_page.locator("select[name=sort]")).to_have_value("newest")
    assert "sort=" not in authed_page.url
    default_text = grid.inner_text()
    default_alpha_first = default_text.index("Zza Sortprobe Alpha") < default_text.index("Zzb Sortprobe Beta")

    # title_desc is deterministic (Z->A) and, on this data, the opposite of
    # whatever the default produced — so a stale default order is detectable.
    authed_page.locator("select[name=sort]").select_option("title_desc")
    expect(authed_page).to_have_url(re.compile(r"sort=title_desc"))
    text = grid.inner_text()
    assert text.index("Zzb Sortprobe Beta") < text.index("Zza Sortprobe Alpha")
    assert default_alpha_first, (
        "test needs the default order to differ from title_desc to be meaningful"
    )

    # Simulate a new tab: sessionStorage (the filter querystring) is per-tab and
    # starts empty, while localStorage (the sort preference) persists. This is
    # the branch restoreFilters() declines and restoreSort() must handle.
    authed_page.evaluate("() => sessionStorage.removeItem('shelf-browse-qs')")
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")

    # Control repopulated...
    expect(authed_page.locator("select[name=sort]")).to_have_value("title_desc")
    # ...and, the actual regression, applied to the rows.
    expect(authed_page).to_have_url(re.compile(r"sort=title_desc"))
    text = authed_page.locator("#item-grid").inner_text()
    assert text.index("Zzb Sortprobe Beta") < text.index("Zza Sortprobe Alpha"), (
        "sort control shows title_desc but rows came back in the server's default order"
    )


def test_browse_sort_restore_keeps_list_view(live_server, authed_page):
    """The restored-sort request must carry `view`, or the server renders grid
    cards that get swapped into the list table (the issue #7 failure mode)."""
    insert_item(live_server["data_dir"], title="Zzc Listprobe Alpha", media_type="book", isbn="9780000999013")
    insert_item(live_server["data_dir"], title="Zzd Listprobe Beta", media_type="book", isbn="9780000999014")

    _reset_browse_storage(authed_page, live_server["url"])
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("[data-testid='view-list']").click()
    expect(authed_page.locator("#item-grid table")).to_have_count(1)
    # title_desc, not title_asc: ascending happens to match the server's default
    # row order here, so it could not tell a restored sort from a stale one.
    authed_page.locator("select[name=sort]").select_option("title_desc")
    expect(authed_page).to_have_url(re.compile(r"sort=title_desc"))

    authed_page.evaluate("() => sessionStorage.removeItem('shelf-browse-qs')")
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")

    # Still a list, not grid cards stuffed into a table...
    expect(authed_page.locator("#item-grid table")).to_have_count(1)
    # ...and the sort survived alongside the view.
    text = authed_page.locator("#item-grid").inner_text()
    assert text.index("Zzd Listprobe Beta") < text.index("Zzc Listprobe Alpha")


@pytest.mark.parametrize("width", [768, 1062, 1280])
def test_browse_view_toggle_not_clipped(live_server, authed_page, width):
    """Issue #14: the view toggle is a flex item with overflow-hidden (for its
    rounded corners). Without shrink-0 it was squeezed below its content width
    and the List button was clipped at every desktop width."""
    authed_page.set_viewport_size({"width": width, "height": 800})
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")

    metrics = authed_page.evaluate(
        """() => {
            const t = document.querySelector('[data-testid=view-toggle]');
            const l = document.querySelector('[data-testid=view-list]');
            const tb = t.getBoundingClientRect(), lb = l.getBoundingClientRect();
            return {scrollW: t.scrollWidth, clientW: t.clientWidth,
                    listRight: lb.right, toggleRight: tb.right};
        }"""
    )
    assert metrics["scrollW"] <= metrics["clientW"], (
        f"view toggle squeezed at {width}px: content {metrics['scrollW']}px "
        f"in {metrics['clientW']}px box"
    )
    assert metrics["listRight"] <= metrics["toggleRight"] + 0.5, (
        f"List view button clipped at {width}px"
    )


def test_browse_language_filter_narrows_and_composes(live_server, authed_page):
    """T10: the language filter narrows the row set and composes with the
    media-type filter — including after the OOB swap replaces the selects."""
    insert_item(live_server["data_dir"], title="Sprachprobe Deutsch",
                media_type="book", isbn="9783000777001", language="de")
    insert_item(live_server["data_dir"], title="Sprachprobe Deutsch Disc",
                media_type="dvd", isbn="9783000777002", language="de")
    insert_item(live_server["data_dir"], title="Sprachprobe English",
                media_type="book", isbn="9780000777003", language="en")
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")

    # Select renders (library now contains languages) and narrows to German
    lang_el = authed_page.locator("select#language-filter")
    expect(lang_el).to_be_visible()
    lang_el.select_option("de")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("Sprachprobe Deutsch")
    expect(authed_page.locator("body")).not_to_contain_text("Sprachprobe English")

    # Compose with media type ON THE SWAPPED SELECT (exercises the OOB
    # fragment's hx-include carrying [name='language'] — R1)
    type_el = authed_page.locator("select#type-filter")
    type_el.select_option("book")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("Sprachprobe Deutsch")
    expect(authed_page.locator("body")).not_to_contain_text("Sprachprobe Deutsch Disc")
    expect(authed_page.locator("body")).not_to_contain_text("Sprachprobe English")
