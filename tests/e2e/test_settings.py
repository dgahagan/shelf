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


def _open_users_tab(page, live_server):
    page.goto(f"{live_server['url']}/settings")
    page.get_by_role("button", name="Users").click()
    page.wait_for_load_state("networkidle")


def test_add_user_succeeds(live_server, authed_page):
    """Filling in the Add User form creates the user and lists it."""
    _open_users_tab(authed_page, live_server)

    authed_page.fill('input[placeholder="Username"]', "e2enewuser")
    authed_page.fill('input[placeholder="Password (min 8 chars)"]', "password123")
    authed_page.get_by_role("button", name="Add User").click()

    expect(authed_page.locator("span.text-shelf-success")).to_contain_text("created")
    expect(authed_page.locator("text=@e2enewuser")).to_be_visible()


def test_add_user_surfaces_server_rejection(live_server, authed_page):
    """A non-JSON error response (CSRF/auth rejection) must show a visible
    error instead of silently doing nothing.

    Regression test for the bug where usersPanel.addUser() called
    `await r.json()` unconditionally: a plain-text 403 from the CSRF/auth
    layer made that throw, the exception went uncaught, and the Add User
    button appeared to do nothing at all.
    """
    authed_page.route(
        "**/api/users",
        lambda route: route.fulfill(status=403, content_type="text/plain", body="CSRF validation failed")
        if route.request.method == "POST" else route.continue_(),
    )

    _open_users_tab(authed_page, live_server)
    authed_page.fill('input[placeholder="Username"]', "e2erejecteduser")
    authed_page.fill('input[placeholder="Password (min 8 chars)"]', "password123")
    authed_page.get_by_role("button", name="Add User").click()

    expect(authed_page.locator("text=Request failed (403)")).to_be_visible()
    expect(authed_page.locator("text=@e2erejecteduser")).not_to_be_visible()
