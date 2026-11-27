"""E2E tests: nav tabs — visibility rules, responsive layout, back links.

`nav_page` (and its baseline reset) lives in conftest.py.
`live_server` is session-scoped and shared with the rest of the e2e suite,
and nav settings are cached in the server process. Every test that touches
a nav-relevant setting goes through `nav_page`, which resets the baseline
(nothing hidden, no vision provider, no Hardcover token) both before and
after the test so state never leaks into other files.
"""
import re
import sqlite3

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import (
    ADMIN_DISPLAY,
    _csrf_headers,
    assert_page_clean,
    attach_page_guard,
    insert_item,
)

pytestmark = pytest.mark.e2e

ALL_NAV_KEYS = (
    "browse", "scan", "intake", "store", "series", "discover", "stats", "settings", "logs",
    "music", "periodicals", "shelf-fill",
)

LONG_DISPLAY_NAME = "Probe Administrator With A Long Name XYZ"  # 40 chars: long enough that an uncapped name overflows de at 1024 (the 28-char design probe value does not, once grouped)


# ---------------------------------------------------------------------------
# (a) / (b) — integration-gated visibility
# ---------------------------------------------------------------------------


def test_unconfigured_instance_hides_intake_and_discover(live_server, nav_page):
    nav_page.goto(f"{live_server['url']}/browse")
    nav_page.wait_for_load_state("networkidle")

    # Series and Stats live inside the Explore disclosure; open it first.
    nav_page.locator('[data-nav-group-button="explore"]').click()
    for key in ("browse", "store", "series", "stats"):
        expect(nav_page.locator(f'[data-nav-tab="{key}"]')).to_be_visible()
    for key in ("intake", "discover"):
        expect(nav_page.locator(f'[data-nav-tab="{key}"]')).to_have_count(0)


def test_hardcover_token_shows_discover_without_restart(live_server, nav_page):
    resp = nav_page.request.post(
        f"{live_server['url']}/api/settings",
        form={"hardcover_token": "test-hc-token-e2e"},
        headers=_csrf_headers(nav_page),
    )
    assert resp.status in (200, 303)

    nav_page.goto(f"{live_server['url']}/browse")
    nav_page.wait_for_load_state("networkidle")
    expect(nav_page.locator('[data-nav-tab="discover"]')).to_be_visible()


# ---------------------------------------------------------------------------
# (c) / (d) — hidden tabs still serve; Navigation card round-trips
# ---------------------------------------------------------------------------


def test_hidden_tab_route_still_serves(live_server, nav_page):
    """Visibility is presentation only — hiding Stats from the nav must not
    stop /stats from serving."""
    base = live_server["url"]
    nav_page.goto(f"{base}/settings")
    nav_page.wait_for_load_state("networkidle")
    form = nav_page.locator('form[action="/api/settings/nav"]')
    form.locator("input[name=stats]").uncheck()
    with nav_page.expect_navigation():
        form.locator('button[type=submit]').click()

    nav_page.goto(f"{base}/browse")
    nav_page.wait_for_load_state("networkidle")
    expect(nav_page.locator('[data-nav-tab="stats"]')).to_have_count(0)

    resp = nav_page.goto(f"{base}/stats")
    assert resp.status == 200
    expect(nav_page.locator("h1")).to_contain_text(re.compile("stat", re.I))


def test_navigation_card_round_trips(live_server, nav_page):
    base = live_server["url"]
    nav_page.goto(f"{base}/settings")
    nav_page.wait_for_load_state("networkidle")

    form = nav_page.locator('form[action="/api/settings/nav"]')
    series_cb = form.locator("input[name=series]")
    expect(series_cb).to_be_checked()

    series_cb.uncheck()
    with nav_page.expect_navigation():
        form.locator('button[type=submit]').click()

    nav_page.goto(f"{base}/browse")
    nav_page.wait_for_load_state("networkidle")
    expect(nav_page.locator('[data-nav-tab="series"]')).to_have_count(0)

    # Checkbox reflects the persisted state on a fresh render...
    nav_page.goto(f"{base}/settings")
    nav_page.wait_for_load_state("networkidle")
    form = nav_page.locator('form[action="/api/settings/nav"]')
    expect(form.locator("input[name=series]")).not_to_be_checked()

    # ...and re-checking it restores the tab.
    form.locator("input[name=series]").check()
    with nav_page.expect_navigation():
        form.locator('button[type=submit]').click()

    nav_page.goto(f"{base}/browse")
    nav_page.wait_for_load_state("networkidle")
    nav_page.locator('[data-nav-group-button="explore"]').click()
    expect(nav_page.locator('[data-nav-tab="series"]')).to_be_visible()


def test_configure_jumps_to_the_integrations_tab(live_server, nav_page):
    """The Configure link beside an auto-hidden tab must actually switch tabs.

    This is the only gate on the button's `setTab('integrations')` target:
    `make check-alpine` validates expression syntax but never resolves method
    names or tab values, so a typo would ship silently past every other check.
    `nav_page`'s baseline has no Hardcover token, which is what renders the
    Discover row's hint in the first place.
    """
    base = live_server["url"]
    nav_page.goto(f"{base}/settings")
    nav_page.wait_for_load_state("networkidle")
    # setTab persists to localStorage and the browser context is shared across
    # the session, so pick the starting tab rather than inheriting one.
    nav_page.locator('[data-testid="tab-library"]').click()

    nav_form = nav_page.locator('form[action="/api/settings/nav"]')
    expect(nav_form).to_be_visible()

    nav_page.locator('[data-testid="configure-discover"]').click()

    expect(nav_page.locator("#hardcover_token")).to_be_visible()
    expect(nav_form).to_be_hidden()

    # Leave the shared context on the tab the other tests expect.
    nav_page.locator('[data-testid="tab-library"]').click()


# ---------------------------------------------------------------------------
# (e) — responsive layout: no horizontal overflow, row/menu swap, menu nav
# ---------------------------------------------------------------------------


_ROW_METRICS_JS = """() => {
    const row = document.querySelector('nav .hidden.lg\\\\:flex');
    const acct = document.querySelector('[data-testid=account-menu-button]');
    const de = document.documentElement;
    return {
        scrollW: de.scrollWidth, clientW: de.clientWidth, innerW: window.innerWidth,
        rowRight: row.getBoundingClientRect().right,
        acctLeft: acct.getBoundingClientRect().left,
        acctRight: acct.getBoundingClientRect().right,
    };
}"""


@pytest.mark.parametrize("locale,html_lang", [("de-DE", "de"), ("it-IT", "it")])
def test_nav_row_fits_across_the_lg_seam_in_long_locales(
    live_server, browser, nav_page, setup_admin, locale, html_lang
):
    """The desktop row must fit from the lg seam (1024) to 1440 px in the
    longest locales, with every tab configured and a long display name.

    Measures the document and the element boxes (G43): the row ends before the
    account button, and the button ends inside the viewport. Sub-lg overflow is
    test_responsive.py's job (its VIEWPORTS run up to 1024).
    """
    base = live_server["url"]
    headers = _csrf_headers(nav_page)
    resp = nav_page.request.post(
        f"{base}/api/settings/vision", form={"vision_provider": "ollama"}, headers=headers
    )
    assert resp.status in (200, 303)
    resp = nav_page.request.post(
        f"{base}/api/settings", form={"hardcover_token": "test-hc-token-e2e"}, headers=headers
    )
    assert resp.status in (200, 303)

    nav_page.goto(f"{base}/browse")
    nav_page.wait_for_load_state("networkidle")
    # Confirm the worst case actually landed before measuring anything.
    for key in ALL_NAV_KEYS:
        expect(nav_page.locator(f'[data-nav-tab="{key}"]')).to_have_count(1)
        expect(nav_page.locator(f'[data-nav-menu-tab="{key}"]')).to_have_count(1)

    db_path = str(live_server["data_dir"] / "shelf.db")

    def set_display_name(name):
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("UPDATE users SET display_name = ?", (name,))
            conn.commit()
        finally:
            conn.close()

    failures = []
    set_display_name(LONG_DISPLAY_NAME)
    ctx = browser.new_context(locale=locale)
    try:
        page = attach_page_guard(ctx.new_page())
        page.goto(f"{base}/login")
        page.fill("input[name=username]", setup_admin["username"])
        page.fill("input[name=password]", setup_admin["password"])
        page.click("button[type=submit]")
        page.wait_for_url(f"{base}/", timeout=10_000)

        page.set_viewport_size({"width": 1024, "height": 900})
        page.goto(f"{base}/browse")
        page.wait_for_load_state("networkidle")
        # Guards: the page is translated and carries the long name.
        expect(page.locator("html")).to_have_attribute("lang", html_lang)
        expect(page.get_by_test_id("account-menu-button")).to_contain_text(LONG_DISPLAY_NAME)

        for width in range(1024, 1441, 8):
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(f"{base}/browse")
            expect(page.locator('[data-nav-tab="browse"]')).to_be_visible()
            m = page.evaluate(_ROW_METRICS_JS)
            problems = []
            if m["scrollW"] > m["clientW"]:
                problems.append(f"scrollWidth {m['scrollW']} > clientWidth {m['clientW']}")
            if m["rowRight"] > m["acctLeft"]:
                problems.append(f"row right {m['rowRight']:.0f} > account left {m['acctLeft']:.0f}")
            if m["acctRight"] > m["innerW"]:
                problems.append(f"account right {m['acctRight']:.0f} > innerWidth {m['innerW']}")
            if problems:
                failures.append(f"{locale} {width}px: " + "; ".join(problems))
        assert_page_clean(page)
    finally:
        ctx.close()
        set_display_name(ADMIN_DISPLAY)

    assert not failures, "nav row does not fit:\n  " + "\n  ".join(failures)


def test_desktop_row_and_hamburger_swap_at_1024(live_server, nav_page):
    base = live_server["url"]
    desktop_tab = nav_page.locator('[data-nav-tab="browse"]')
    menu_button = nav_page.locator('[data-testid="nav-menu-button"]')

    nav_page.set_viewport_size({"width": 1024, "height": 900})
    nav_page.goto(f"{base}/browse")
    nav_page.wait_for_load_state("networkidle")
    expect(desktop_tab).to_be_visible()
    expect(menu_button).to_be_hidden()

    nav_page.set_viewport_size({"width": 1023, "height": 900})
    nav_page.goto(f"{base}/browse")
    nav_page.wait_for_load_state("networkidle")
    expect(desktop_tab).to_be_hidden()
    expect(menu_button).to_be_visible()


def test_menu_opens_and_navigates_at_narrow_width(live_server, nav_page):
    base = live_server["url"]
    nav_page.set_viewport_size({"width": 390, "height": 800})
    nav_page.goto(f"{base}/browse")
    nav_page.wait_for_load_state("networkidle")

    panel = nav_page.locator('[data-testid="nav-menu-panel"]')
    expect(panel).to_be_hidden()
    nav_page.locator('[data-testid="nav-menu-button"]').click()
    expect(panel).to_be_visible()

    nav_page.locator('[data-nav-menu-tab="stats"]').click()
    nav_page.wait_for_url(f"{base}/stats")


# ---------------------------------------------------------------------------
# (f) — item-detail back links
# ---------------------------------------------------------------------------


def test_back_link_from_series(live_server, nav_page):
    item_id = insert_item(
        live_server["data_dir"], title="Nav Series Probe", series_name="Nav Probe Series", series_position=1
    )
    nav_page.goto(f"{live_server['url']}/series")
    nav_page.wait_for_load_state("networkidle")
    nav_page.locator('a[title="Nav Series Probe"]').click()
    nav_page.wait_for_url(re.compile(rf"/item/{item_id}\?from=series"))

    back_link = nav_page.get_by_role("link", name=re.compile("Back to series"))
    expect(back_link).to_be_visible()
    back_link.click()
    nav_page.wait_for_url(f"{live_server['url']}/series")


def test_back_link_from_stats(live_server, nav_page):
    item_id = insert_item(live_server["data_dir"], title="Nav Stats Probe")
    nav_page.goto(f"{live_server['url']}/stats")
    nav_page.wait_for_load_state("networkidle")
    nav_page.locator(f'a[href="/item/{item_id}?from=stats"]').click()
    nav_page.wait_for_url(re.compile(rf"/item/{item_id}\?from=stats"))

    back_link = nav_page.get_by_role("link", name=re.compile("Back to stats"))
    expect(back_link).to_be_visible()
    back_link.click()
    nav_page.wait_for_url(f"{live_server['url']}/stats")


def test_back_link_defaults_to_browse(live_server, nav_page):
    item_id = insert_item(live_server["data_dir"], title="Nav Deep Link Probe")
    nav_page.goto(f"{live_server['url']}/item/{item_id}")
    nav_page.wait_for_load_state("networkidle")

    back_link = nav_page.get_by_role("link", name=re.compile("Back to collection"))
    expect(back_link).to_be_visible()
    back_link.click()
    nav_page.wait_for_url(f"{live_server['url']}/browse")


def test_edit_round_trip_preserves_origin(live_server, nav_page):
    item_id = insert_item(
        live_server["data_dir"], title="Nav Edit Roundtrip Probe",
        series_name="Nav Roundtrip Series", series_position=1,
    )
    base = live_server["url"]
    nav_page.goto(f"{base}/item/{item_id}?from=series")
    nav_page.wait_for_load_state("networkidle")

    nav_page.get_by_role("link", name="Edit").click()
    nav_page.wait_for_url(re.compile(rf"/item/{item_id}/edit\?from=series"))
    # The origin survives as a hidden field, not just the URL.
    expect(nav_page.locator('input[name=from]')).to_have_value("series")

    nav_page.locator('[data-testid="save-btn"]').click()
    nav_page.wait_for_url(re.compile(rf"/item/{item_id}\?from=series"))

    back_link = nav_page.get_by_role("link", name=re.compile("Back to series"))
    expect(back_link).to_be_visible()
