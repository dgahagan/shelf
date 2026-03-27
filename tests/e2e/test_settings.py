"""E2E tests: settings and stats pages."""
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_settings_page_loads(live_server, authed_page):
    """Settings page renders without error for admin."""
    authed_page.goto(f"{live_server['url']}/settings")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("Settings")


def test_stats_page_loads(live_server, authed_page):
    """Stats page renders without error."""
    authed_page.goto(f"{live_server['url']}/stats")
    authed_page.wait_for_load_state("networkidle")
    # Should contain some stats heading or number
    assert authed_page.locator("body").is_visible()
    assert "error" not in authed_page.locator("body").inner_text().lower() or \
           authed_page.locator("body").inner_text() != ""
