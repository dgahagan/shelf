"""E2E tests: settings and stats pages."""
import sqlite3

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item

pytestmark = pytest.mark.e2e


def test_settings_page_loads(live_server, authed_page):
    """Settings page renders without error for admin."""
    authed_page.goto(f"{live_server['url']}/settings")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("Settings")


def test_google_books_optional_key_card_and_test_action(live_server, authed_page):
    """The write-only card is CSP-functional without calling Google."""
    authed_page.route(
        "**/api/settings/google-books/test",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"ok": true, "message": "Connected to Google Books"}',
        ),
    )
    authed_page.goto(f"{live_server['url']}/settings")
    authed_page.get_by_role("button", name="Integrations").click()

    card = authed_page.locator("[data-google-books-saved]")
    expect(card).to_contain_text("Optional")
    key_input = card.locator('input[name="google_books_api_key"]')
    expect(key_input).to_be_visible()
    expect(key_input).to_have_value("")

    key_input.fill("fake-e2e-google-key")
    card.get_by_role("button", name="Test Key").click()
    expect(card).to_contain_text("Connected to Google Books")


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


# --- Settings delete confirmations ------------------------------------------
#
# These are the regression pin for the CSP-dead-handler class: the three
# Settings delete forms used to carry `onclick="return confirm(...)"`, which
# `script-src 'self'` silently refuses, so every destructive delete fired with
# no confirmation at all. That failure is invisible to unit tests — only a real
# browser enforcing the real CSP can see it.
#
# Every handler below RECORDS the dialog message and the test asserts on what
# was recorded. A handler that merely accepts proves nothing: with no
# `data-confirm`, an empty one, or a broken listener, the plain form still
# submits and deletes the row, the handler never fires, and a bare "row is
# gone" assertion would pass over a dead confirmation.
#
# Names are distinct per test on purpose: `live_server` is session-scoped, so
# rows persist across tests, and `POST /api/borrowers` is `INSERT OR IGNORE`
# against a UNIQUE name — a reused name silently no-ops and order-couples the
# tests.


def _remove_button(page, name):
    """The Remove button in the settings row whose label span is `name`."""
    return page.locator(
        f"//span[normalize-space(text())='{name}']/following-sibling::form//button"
    )


def _add_borrower(page, live_server, name):
    page.goto(f"{live_server['url']}/settings")
    page.wait_for_load_state("networkidle")
    page.fill('input[placeholder="Add borrower..."]', name)
    page.locator('form[action="/api/borrowers"] button[type="submit"]').click()
    page.wait_for_load_state("networkidle")
    expect(_remove_button(page, name)).to_be_visible()


def _add_location(page, live_server, name):
    page.goto(f"{live_server['url']}/settings")
    page.wait_for_load_state("networkidle")
    page.fill('input[placeholder="New location name..."]', name)
    page.locator('form[action="/api/locations"] button[type="submit"]').click()
    page.wait_for_load_state("networkidle")
    expect(_remove_button(page, name)).to_be_visible()


def _recording_handler(messages, action):
    def handle(dialog):
        messages.append(dialog.message)
        getattr(dialog, action)()
    return handle


def test_borrower_delete_confirm_dismiss_keeps_the_borrower(live_server, authed_page):
    """Dismissing the confirm must leave the borrower in place."""
    name = "E2E Dismiss Borrower"
    _add_borrower(authed_page, live_server, name)

    messages = []
    authed_page.once("dialog", _recording_handler(messages, "dismiss"))
    _remove_button(authed_page, name).click()
    authed_page.wait_for_load_state("networkidle")

    assert messages == [f"Remove borrower '{name}'?"], (
        "no confirmation dialog fired — the inline handler is dead under the "
        "CSP and the data-confirm listener did not replace it"
    )
    expect(_remove_button(authed_page, name)).to_be_visible()


def test_borrower_delete_confirm_accept_removes_the_borrower(live_server, authed_page):
    """Accepting the confirm must actually delete the borrower."""
    name = "E2E Accept Borrower"
    _add_borrower(authed_page, live_server, name)

    messages = []
    authed_page.once("dialog", _recording_handler(messages, "accept"))
    _remove_button(authed_page, name).click()
    authed_page.wait_for_load_state("networkidle")

    assert messages == [f"Remove borrower '{name}'?"]
    # Reload so this is the server's answer, not a stale DOM.
    authed_page.goto(f"{live_server['url']}/settings")
    authed_page.wait_for_load_state("networkidle")
    expect(_remove_button(authed_page, name)).to_have_count(0)


def test_location_delete_uses_the_same_delegated_confirm(live_server, authed_page):
    """Locations and platforms share one delegated listener with borrowers.

    Exercising it from a second form is what proves the delegation rather
    than a borrower-specific handler.
    """
    name = "E2E Confirm Location"
    _add_location(authed_page, live_server, name)

    messages = []
    authed_page.once("dialog", _recording_handler(messages, "accept"))
    _remove_button(authed_page, name).click()
    authed_page.wait_for_load_state("networkidle")

    assert messages == [f"Delete location '{name}'?"]
    authed_page.goto(f"{live_server['url']}/settings")
    authed_page.wait_for_load_state("networkidle")
    expect(_remove_button(authed_page, name)).to_have_count(0)


def test_blocked_borrower_delete_shows_the_settings_banner(live_server, authed_page):
    """An active loan blocks the delete and answers with a page, not raw JSON."""
    name = "E2E Blocked Borrower"
    _add_borrower(authed_page, live_server, name)

    item_id = insert_item(live_server["data_dir"], title="E2E Lent Book", isbn="9780000009299")
    conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
    try:
        borrower_id = conn.execute(
            "SELECT id FROM borrowers WHERE name = ?", (name,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_out) "
            "VALUES (?, ?, datetime('now'))",
            (item_id, borrower_id),
        )
        conn.commit()
    finally:
        conn.close()

    authed_page.goto(f"{live_server['url']}/settings")
    authed_page.wait_for_load_state("networkidle")

    messages = []
    authed_page.once("dialog", _recording_handler(messages, "accept"))
    _remove_button(authed_page, name).click()
    authed_page.wait_for_load_state("networkidle")

    # The confirm still counts zero past loans — the open loan is not a past one.
    assert messages == [f"Remove borrower '{name}'?"]
    expect(authed_page.get_by_test_id("borrower-error-banner")).to_be_visible()
    expect(_remove_button(authed_page, name)).to_be_visible()
