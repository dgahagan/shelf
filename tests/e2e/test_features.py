"""E2E: Settings → Features round trips — the disabled page, a probe-gated
confirm, and turning a feature back on from its own disabled page.

**Scope note (updated with plan scan-977-periodicals).** The T11 third
scenario - a 977 barcode scanned from /scan with every feature off - is
`test_a_977_scan_from_scan_with_every_feature_off`: the scan dispatch now
routes any 977 barcode in Add or Wishlist to the periodical confirm card,
asks the ISSN portal (stubbed, 404 -> no_match) rather than the UPC Item DB,
and the confirm form saves a `periodical_issues` row even though the
Periodicals feature is off (the redirect lands on its disabled page).
"""
import sqlite3
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import _run_setup_wizard, assert_page_clean, attach_page_guard

pytestmark = pytest.mark.e2e


def _insert_share_link(data_dir: Path, token: str) -> None:
    """Insert one share-link row directly into the E2E SQLite DB.

    Mirrors conftest.insert_item / test_scan.py's _insert_borrower — there is
    no shared share-links helper.
    """
    db_path = data_dir / "shelf.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO share_links (token, scope) VALUES (?, 'collection')",
            (token,),
        )
        conn.commit()
    finally:
        conn.close()


def _delete_share_link(data_dir: Path, token: str) -> None:
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        conn.execute("DELETE FROM share_links WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def one_share_link(live_server):
    """One share link for the length of a test, then gone again.

    The live_server DB is session-scoped. A leftover link renders the Sharing
    card's "Copied!" span on /settings, a second `span.text-shelf-success`
    that later tests' strict locators trip over (G126).
    """
    token = "e2e-share-token"
    _insert_share_link(live_server["data_dir"], token)
    yield token
    _delete_share_link(live_server["data_dir"], token)


def _open_features_tab(page, base_url: str) -> None:
    page.goto(f"{base_url}/settings")
    page.click('[data-testid="tab-features"]')
    expect(page.locator('[data-testid="feature-row-series"]')).to_be_visible()


def _feature_state(page, key: str) -> str:
    """"on" or "off", read from the row's state badge without a reload."""
    row = page.locator(f'[data-testid="feature-row-{key}"]')
    if row.locator('[data-feature-state="on"]').count():
        return "on"
    assert row.locator('[data-feature-state="off"]').count()
    return "off"


def test_disabled_series_page_round_trips_through_enable(live_server, authed_page):
    """Turn Series off in place, see it gone from the nav and gated at /series,
    then turn it back on from the disabled page itself."""
    base_url = live_server["url"]

    dialogs: list[str] = []

    def _fail_on_dialog(dialog):
        dialogs.append(dialog.message)
        dialog.dismiss()

    authed_page.on("dialog", _fail_on_dialog)

    _open_features_tab(authed_page, base_url)
    assert _feature_state(authed_page, "series") == "on"

    toggle = authed_page.locator('[data-testid="feature-toggle-series"]')
    toggle.scroll_into_view_if_needed()
    scroll_before = authed_page.evaluate("window.scrollY")
    authed_page.evaluate("window.__settingsDocumentMarker = 'same-document'")
    toggle.click()

    expect(authed_page.locator('[data-testid="feature-row-series"] [data-feature-state="off"]')).to_be_visible()
    assert authed_page.url == f"{base_url}/settings"
    assert authed_page.evaluate("window.__settingsDocumentMarker") == "same-document"
    assert abs(authed_page.evaluate("window.scrollY") - scroll_before) <= 4
    assert dialogs == []  # series has no probes — turning it off never asks

    # The nav no longer offers Series, on the desktop bar or the mobile menu.
    expect(
        authed_page.locator('[data-nav-tab="series"], [data-nav-menu-tab="series"]')
    ).to_have_count(0)

    authed_page.goto(f"{base_url}/series")
    expect(authed_page.locator('[data-testid="feature-disabled"]')).to_be_visible()
    expect(authed_page.locator('[data-testid="feature-enable-form"]')).to_be_visible()

    with authed_page.expect_navigation():
        authed_page.click('[data-testid="feature-enable-form"] button[type=submit]')
    assert authed_page.url == f"{base_url}/series"
    expect(authed_page.locator('[data-testid="feature-disabled"]')).to_have_count(0)
    expect(authed_page.locator("h1")).to_contain_text("Series")

    assert dialogs == []


def test_turning_off_share_with_a_link_confirms_first(live_server, authed_page, one_share_link):
    """A nonzero probe raises a confirm; dismissing it changes nothing, and
    accepting it turns the flag off. Restores the flag afterwards."""
    base_url = live_server["url"]

    _open_features_tab(authed_page, base_url)
    assert _feature_state(authed_page, "share") == "on"

    dismissed: list[str] = []

    def _dismiss(dialog):
        dismissed.append(dialog.message)
        dialog.dismiss()

    authed_page.once("dialog", _dismiss)
    authed_page.click('[data-testid="feature-toggle-share"]')
    assert len(dismissed) == 1
    assert "1 active share link" in dismissed[0]
    # Dismissed: the form never submitted, so the flag is unchanged.
    assert _feature_state(authed_page, "share") == "on"

    accepted: list[str] = []

    def _accept(dialog):
        accepted.append(dialog.message)
        dialog.accept()

    authed_page.once("dialog", _accept)
    authed_page.click('[data-testid="feature-toggle-share"]')
    assert len(accepted) == 1
    assert "1 active share link" in accepted[0]
    expect(authed_page.locator('[data-testid="feature-row-share"] [data-feature-state="off"]')).to_be_visible()
    assert _feature_state(authed_page, "share") == "off"

    # Restore: the live_server DB is session-scoped and shared with every
    # other E2E test.
    authed_page.click('[data-testid="feature-toggle-share"]')
    expect(authed_page.locator('[data-testid="feature-row-share"] [data-feature-state="on"]')).to_be_visible()
    assert _feature_state(authed_page, "share") == "on"


def test_minimal_install_then_apply_standard_from_settings(server_factory, browser):
    """A Minimal install hides Series and Stats everywhere; applying Standard
    from Settings → Features turns them back on with no restart and no
    confirm — turning features on never asks (G28, G50, G83)."""
    server = server_factory()
    base_url = server["url"]
    credentials = _run_setup_wizard(browser, base_url, profile="minimal")

    ctx = browser.new_context()
    page = attach_page_guard(ctx.new_page())
    try:
        dialogs: list[str] = []

        def _record(dialog):
            dialogs.append(dialog.message)
            dialog.dismiss()

        page.on("dialog", _record)

        page.goto(f"{base_url}/login")
        page.fill("input[name=username]", credentials["username"])
        page.fill("input[name=password]", credentials["password"])
        page.click("button[type=submit]")
        page.wait_for_url(f"{base_url}/", timeout=10_000)

        # Minimal: Series and Stats are gone from both nav surfaces, and
        # /series is gated.
        expect(
            page.locator('[data-nav-tab="series"], [data-nav-menu-tab="series"]')
        ).to_have_count(0)
        expect(
            page.locator('[data-nav-tab="stats"], [data-nav-menu-tab="stats"]')
        ).to_have_count(0)

        page.goto(f"{base_url}/series")
        expect(page.locator('[data-testid="feature-disabled"]')).to_be_visible()

        _open_features_tab(page, base_url)
        profiles = page.locator('[data-testid="feature-profiles"]')
        expect(profiles).to_have_attribute("data-profile-current", "minimal")

        page.click('[data-testid="profile-apply-standard"]')

        # Applying a profile refreshes the panel in place.
        profiles = page.locator('[data-testid="feature-profiles"]')
        expect(profiles).to_have_attribute("data-profile-current", "standard")

        # Standard, live, no restart: the Series nav tab and /series both
        # come back.
        page.locator('[data-nav-group-button="explore"]').click()
        expect(page.locator('[data-nav-tab="series"]')).to_be_visible()
        page.goto(f"{base_url}/series")
        expect(page.locator('[data-testid="feature-disabled"]')).to_have_count(0)

        assert dialogs == []
        assert_page_clean(page)
    finally:
        ctx.close()


def test_profiles_show_what_they_turn_on_without_hover(live_server, browser, setup_admin):
    """A touch screen has no hover, so what each profile changes must be
    visible text in Settings, not only a title tooltip (diff review M1)."""
    base_url = live_server["url"]
    ctx = browser.new_context(viewport={"width": 390, "height": 844},
                              is_mobile=True, has_touch=True)
    page = attach_page_guard(ctx.new_page())
    try:
        page.goto(f"{base_url}/login")
        page.fill("input[name=username]", setup_admin["username"])
        page.fill("input[name=password]", setup_admin["password"])
        page.click("button[type=submit]")
        page.wait_for_url(f"{base_url}/", timeout=10_000)

        _open_features_tab(page, base_url)
        profiles = page.locator('[data-testid="feature-profiles"]')
        expect(profiles).to_be_visible()
        text = profiles.inner_text()
        assert "Turns off: Lending" in text
        assert "Turns off: Sharing" in text
        assert "Nothing optional." in text
        assert_page_clean(page)
    finally:
        ctx.close()


def test_a_977_scan_from_scan_with_every_feature_off(server_factory, browser, upc_stub):
    """A 977 scan reaches the periodical confirm card and saves an issue even
    on a Minimal install, without calling the UPC Item DB (G83, G126)."""
    server = server_factory()
    base_url = server["url"]
    credentials = _run_setup_wizard(browser, base_url, profile="minimal")

    ctx = browser.new_context()
    page = attach_page_guard(ctx.new_page())
    try:
        page.goto(f"{base_url}/login")
        page.fill("input[name=username]", credentials["username"])
        page.fill("input[name=password]", credentials["password"])
        page.click("button[type=submit]")
        page.wait_for_url(f"{base_url}/", timeout=10_000)

        page.goto(f"{base_url}/scan")
        page.wait_for_load_state("networkidle")

        before = len(upc_stub["requests"])
        lookups_before = [r for r in upc_stub["requests"] if r[0] == "/lookup"]

        page.fill("#isbn-input", "977228014790460066")
        page.press("#isbn-input", "Enter")

        card = page.locator('[data-scan-status="periodical_confirm"]')
        expect(card).to_be_visible(timeout=20_000)
        expect(card).to_contain_text(
            "No publication metadata was returned for this ISSN"
        )
        title_input = card.locator('input[name="publication_title"]')
        expect(title_input).to_be_visible()

        new_requests = upc_stub["requests"][before:]
        lookups_after = [r for r in upc_stub["requests"] if r[0] == "/lookup"]
        assert len(lookups_after) == len(lookups_before), "UPC Item DB was called"
        assert "/issn/2280-1472" in [r[0] for r in new_requests], new_requests

        title_input.fill("Test Weekly")
        card.locator('input[name="issue_number"]').fill("12")
        with page.expect_navigation() as nav:
            card.locator('button[type="submit"]:has-text("Add confirmed issue")').click()
        assert nav.value.status == 200

        conn = sqlite3.connect(str(server["data_dir"] / "shelf.db"))
        try:
            rows = conn.execute(
                "SELECT barcode_ean, barcode_supplement FROM periodical_issues"
            ).fetchall()
        finally:
            conn.close()
        assert rows == [("9772280147904", "60066")]

        assert_page_clean(page)
    finally:
        ctx.close()
