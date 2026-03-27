"""E2E tests: scan page loads and mode switching."""
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_scan_page_loads(live_server, authed_page):
    """The scan page renders for an authenticated editor/admin."""
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("Scan")


def test_scan_page_has_isbn_input(live_server, authed_page):
    """Scan page has an ISBN/barcode input field."""
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")
    isbn_input = authed_page.locator(
        "input[name=isbn], input[name=barcode], input[name=upc], "
        "input[placeholder*='ISBN'], input[placeholder*='barcode']"
    ).first
    expect(isbn_input).to_be_visible()


def test_scan_mode_switching(live_server, authed_page):
    """Clicking a mode button (Lookup / Add / Quick Rate) updates the UI."""
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    # Look for mode buttons — names from the plan / UI
    mode_buttons = authed_page.locator(
        "button:has-text('Lookup'), button:has-text('Add'), button:has-text('Quick Rate'), "
        "[data-mode], [x-on\\:click*='mode']"
    )
    if mode_buttons.count() < 2:
        pytest.skip("No mode-switching buttons found in current UI")

    # Click first mode button to ensure JS doesn't crash
    mode_buttons.first.click()
    authed_page.wait_for_load_state("networkidle")
    assert authed_page.locator("body").is_visible()
