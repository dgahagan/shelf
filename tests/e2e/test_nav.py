"""E2E tests: nav tabs — visibility rules, responsive layout, back links.

`live_server` is session-scoped and shared with the rest of the e2e suite,
and nav settings are cached in the server process. Every test that touches
a nav-relevant setting goes through `nav_page`, which resets the baseline
(nothing hidden, no vision provider, no Hardcover token) both before and
after the test so state never leaks into other files.
"""
import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item

pytestmark = pytest.mark.e2e

# Mirrors app.nav.HIDEABLE_KEYS — kept literal here so a change to that set
# is a visible diff in this test rather than a silent behavior change.
HIDEABLE_KEYS = ("scan", "intake", "store", "series", "discover", "stats", "logs")
ALL_NAV_KEYS = ("browse", "scan", "intake", "store", "series", "discover", "stats", "settings", "logs")


def _csrf_headers(page):
    return {"X-CSRF-Token": page.evaluate("() => window.csrfToken()")}


def _reset_nav_baseline(live_server, page):
    """Known-clean nav state: no vision provider, no Hardcover token, nothing
    manually hidden. Goes through the real settings endpoints so the
    server-side nav cache is invalidated, not the sqlite file directly."""
    base = live_server["url"]
    headers = _csrf_headers(page)
    resp = page.request.post(
        f"{base}/api/settings",
        form={"hardcover_token": "", "clear_hardcover_token": "on"},
        headers=headers,
    )
    assert resp.status in (200, 303)
    resp = page.request.post(
        f"{base}/api/settings/vision", form={"vision_provider": ""}, headers=headers
    )
    assert resp.status in (200, 303)
    # Every hideable key present+checked => nav_hidden_tabs ends up empty.
    resp = page.request.post(
        f"{base}/api/settings/nav", form={k: "on" for k in HIDEABLE_KEYS}, headers=headers
    )
    assert resp.status in (200, 303)


@pytest.fixture
def nav_page(live_server, authed_page):
    """authed_page with nav-relevant settings forced to a known baseline
    before the test, and restored to that same baseline after — regardless
    of what the test did in between."""
    _reset_nav_baseline(live_server, authed_page)
    yield authed_page
    _reset_nav_baseline(live_server, authed_page)


# ---------------------------------------------------------------------------
# (a) / (b) — integration-gated visibility
# ---------------------------------------------------------------------------


def test_unconfigured_instance_hides_intake_and_discover(live_server, nav_page):
    nav_page.goto(f"{live_server['url']}/browse")
    nav_page.wait_for_load_state("networkidle")

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
    form.locator('button[type=submit]').click()
    nav_page.wait_for_load_state("networkidle")

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
    form.locator('button[type=submit]').click()
    nav_page.wait_for_load_state("networkidle")

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
    form.locator('button[type=submit]').click()
    nav_page.wait_for_load_state("networkidle")

    nav_page.goto(f"{base}/browse")
    nav_page.wait_for_load_state("networkidle")
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


def test_no_horizontal_overflow_with_all_tabs_configured(live_server, nav_page):
    """Width sweep with all 9 tabs configured/visible — the worst case that
    motivated the nav redesign. No width in the sweep should overflow."""
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

    for width in (360, 768, 1024, 1440, 1920):
        nav_page.set_viewport_size({"width": width, "height": 900})
        nav_page.goto(f"{base}/browse")
        nav_page.wait_for_load_state("networkidle")
        metrics = nav_page.evaluate(
            "() => ({scrollW: document.documentElement.scrollWidth, "
            "clientW: document.documentElement.clientWidth})"
        )
        assert metrics["scrollW"] <= metrics["clientW"], (
            f"horizontal overflow at {width}px: scrollWidth={metrics['scrollW']} "
            f"clientWidth={metrics['clientW']}"
        )


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
