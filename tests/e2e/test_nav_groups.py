"""E2E tests: the nav group disclosures (Explore, Tools) and the headed menu.

The desktop row folds Series/Music/Periodicals/Stats into Explore and
Intake/Shelf Fill into Tools; below lg the hamburger lists the same links
under headings. Everything here runs against the session admin (Everything
profile) through `nav_page`, which resets the shared nav baseline.
"""
import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import _csrf_headers, assert_page_clean, attach_page_guard

pytestmark = pytest.mark.e2e

ACTIVE = "bg-shelf-hover"


def _configure_all(page, base):
    """Vision + Hardcover on, so Tools and the full row render."""
    headers = _csrf_headers(page)
    resp = page.request.post(
        f"{base}/api/settings/vision", form={"vision_provider": "ollama"}, headers=headers
    )
    assert resp.status in (200, 303)
    resp = page.request.post(
        f"{base}/api/settings", form={"hardcover_token": "test-hc-token-e2e"}, headers=headers
    )
    assert resp.status in (200, 303)


def _group(page, key):
    return page.locator(f'[data-nav-group-button="{key}"]')


def _panel(page, key):
    return page.locator(f'[data-nav-group-panel="{key}"]')


def _open_browse(page, base, width=1280):
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{base}/browse")
    page.wait_for_load_state("networkidle")


def test_explore_opens_navigates_and_marks_itself_active(live_server, nav_page):
    base = live_server["url"]
    _configure_all(nav_page, base)
    _open_browse(nav_page, base)

    expect(_panel(nav_page, "explore")).to_be_hidden()
    _group(nav_page, "explore").click()
    for key in ("series", "music", "periodicals", "stats"):
        expect(nav_page.locator(f'[data-nav-group-panel="explore"] [data-nav-tab="{key}"]')).to_be_visible()

    with nav_page.expect_navigation():
        nav_page.locator('[data-nav-group-panel="explore"] [data-nav-tab="periodicals"]').click()
    assert nav_page.url == f"{base}/periodicals"
    expect(_group(nav_page, "explore")).to_have_class(re.compile(ACTIVE))


def test_group_closes_on_escape_outside_click_and_opening_another(live_server, nav_page):
    base = live_server["url"]
    _configure_all(nav_page, base)
    _open_browse(nav_page, base)

    _group(nav_page, "explore").click()
    expect(_panel(nav_page, "explore")).to_be_visible()
    nav_page.keyboard.press("Escape")
    expect(_panel(nav_page, "explore")).to_be_hidden()

    _group(nav_page, "explore").click()
    expect(_panel(nav_page, "explore")).to_be_visible()
    nav_page.locator("main").click(position={"x": 5, "y": 400})
    expect(_panel(nav_page, "explore")).to_be_hidden()

    _group(nav_page, "explore").click()
    expect(_panel(nav_page, "explore")).to_be_visible()
    _group(nav_page, "tools").click()
    expect(_panel(nav_page, "tools")).to_be_visible()
    expect(_panel(nav_page, "explore")).to_be_hidden()


def test_tabbing_out_of_a_menu_closes_it(live_server, nav_page):
    """Keyboard focus leaving a group, or the account menu, closes it (qa
    Observation 2). Tab walks the four Explore links, then on to Tools."""
    base = live_server["url"]
    _configure_all(nav_page, base)
    _open_browse(nav_page, base)

    _group(nav_page, "explore").focus()
    nav_page.keyboard.press("Enter")
    expect(_panel(nav_page, "explore")).to_be_visible()
    nav_page.keyboard.press("Tab")
    expect(nav_page.locator('[data-nav-group-panel="explore"] [data-nav-tab="series"]')).to_be_focused()
    for _ in range(4):
        nav_page.keyboard.press("Tab")
    expect(_group(nav_page, "tools")).to_be_focused()
    expect(_panel(nav_page, "explore")).to_be_hidden()

    account = nav_page.locator('[data-testid="account-menu-button"]')
    account.focus()
    nav_page.keyboard.press("Enter")
    panel = nav_page.locator('[data-testid="account-menu-panel"]')
    expect(panel).to_be_visible()
    items = panel.locator("a, button")
    for _ in range(items.count() + 1):
        nav_page.keyboard.press("Tab")
    expect(panel).to_be_hidden()


def test_opened_panels_stay_inside_the_viewport_at_1024(live_server, nav_page):
    base = live_server["url"]
    _configure_all(nav_page, base)
    _open_browse(nav_page, base, width=1024)

    for key in ("explore", "tools"):
        _group(nav_page, key).click()
        # Visible member first (G42): a rect before paint is all zeros.
        expect(nav_page.locator(f'[data-nav-group-panel="{key}"] [data-nav-tab]').first).to_be_visible()
        rect = nav_page.evaluate(
            "(k) => { const r = document.querySelector('[data-nav-group-panel=\"' + k + '\"]')"
            ".getBoundingClientRect(); return {left: r.left, right: r.right, w: window.innerWidth}; }",
            key,
        )
        assert rect["left"] >= 0, f"{key} panel starts off-screen: {rect}"
        assert rect["right"] <= rect["w"], f"{key} panel overflows the viewport: {rect}"
        nav_page.keyboard.press("Escape")
        expect(_panel(nav_page, key)).to_be_hidden()


def test_hamburger_lists_group_headings_and_navigates(live_server, nav_page):
    base = live_server["url"]
    _configure_all(nav_page, base)
    nav_page.set_viewport_size({"width": 390, "height": 800})
    nav_page.goto(f"{base}/browse")
    nav_page.wait_for_load_state("networkidle")

    nav_page.locator('[data-testid="nav-menu-button"]').click()
    expect(nav_page.locator('[data-nav-menu-heading="explore"]')).to_be_visible()
    expect(nav_page.locator('[data-nav-menu-heading="tools"]')).to_be_visible()
    with nav_page.expect_navigation():
        nav_page.locator('[data-nav-menu-tab="stats"]').click()
    assert nav_page.url == f"{base}/stats"


def test_standard_profile_shows_shelf_fill_as_a_plain_tab(live_server, browser, setup_admin):
    """Under Standard with no vision provider, Tools has one visible member,
    so it renders as a plain tab. Everything is restored afterwards: the
    server DB is shared by the whole session."""
    base = live_server["url"]
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = attach_page_guard(ctx.new_page())
    page.on("dialog", lambda d: d.accept())
    try:
        page.goto(f"{base}/login")
        page.fill("input[name=username]", setup_admin["username"])
        page.fill("input[name=password]", setup_admin["password"])
        page.click("button[type=submit]")
        page.wait_for_url(f"{base}/", timeout=10_000)

        def _apply(name):
            page.goto(f"{base}/settings")
            page.click('[data-testid="tab-features"]')
            expect(page.locator('[data-testid="feature-row-series"]')).to_be_visible()
            page.click(f'[data-testid="profile-apply-{name}"]')
            expect(page.locator('[data-testid="feature-profiles"]')).to_have_attribute(
                "data-profile-current", name
            )

        try:
            _apply("standard")
            page.goto(f"{base}/browse")
            expect(page.locator('[data-nav-tab="shelf-fill"]')).to_be_visible()
            expect(page.locator('[data-nav-group="tools"]')).to_have_count(0)
        finally:
            _apply("everything")
        assert_page_clean(page)
    finally:
        ctx.close()
