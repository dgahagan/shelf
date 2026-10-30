"""E2E: API tokens — create, use from a cookieless client, list, revoke."""
import re
import sqlite3

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import assert_page_clean

pytestmark = pytest.mark.e2e

RAW_STAMP = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d")

VIEWPORTS = {
    "desktop": {"width": 1280, "height": 800},
    "mobile": {"width": 390, "height": 844},
}


def _delete_tokens(data_dir, name: str) -> None:
    """Delete by name on a raw connection (G149: no cascade fires here)."""
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        conn.execute("DELETE FROM api_tokens WHERE name = ?", (name,))
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("viewport", VIEWPORTS.values(), ids=VIEWPORTS.keys())
def test_token_create_use_list_revoke(live_server, authed_page, browser, setup_admin, viewport):
    page = authed_page
    page.set_viewport_size(viewport)
    base = live_server["url"]
    name = f"homepage-{viewport['width']}"
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base)

    anon_ctx = browser.new_context(ignore_https_errors=True)
    try:
        page.goto(f"{base}/browse")
        page.get_by_test_id("account-menu-button").click()
        page.get_by_test_id("account-menu-profile").click()
        page.get_by_test_id("account-tab-tokens").click()

        pane = page.get_by_test_id("api-tokens-pane")
        expect(pane).to_be_visible()
        pane.get_by_test_id("api-token-name-input").fill(name)
        pane.get_by_test_id("api-token-create").click()

        created = pane.get_by_test_id("api-token-created")
        expect(created).to_be_visible()
        expect(created).to_contain_text("This is the only time the token is shown.")
        plaintext = pane.get_by_test_id("api-token-plaintext")
        expect(plaintext).to_have_value(re.compile(r"^shelf_"))
        token = plaintext.input_value()

        copy_btn = pane.get_by_test_id("api-token-copy")
        copy_btn.click()
        expect(copy_btn).to_have_text("Copied")

        row = pane.get_by_test_id("api-token-row").filter(has_text=name)
        expect(row).to_have_count(1)
        # Stamps are shown in local time, never as the raw UTC SQLite string.
        expect(row).to_contain_text("last used Never")
        expect(row).not_to_contain_text(RAW_STAMP)

        # Cookieless client: the bearer works, no credential does not.
        stats_url = f"{base}/api/v1/stats"
        ok = anon_ctx.request.get(stats_url, headers={"Authorization": f"Bearer {token}"})
        assert ok.status == 200
        assert isinstance(ok.json()["items"]["total"], int)
        assert anon_ctx.request.get(stats_url).status == 401

        # Settings -> Users shows the token under the admin's username.
        page.goto(f"{base}/settings")
        page.get_by_role("button", name="Users").click()
        card = page.get_by_test_id("admin-tokens-card")
        admin_row = card.get_by_test_id("admin-token-row").filter(has_text=name)
        expect(admin_row).to_have_count(1)
        expect(admin_row.get_by_test_id("admin-token-user")).to_have_text(f"@{setup_admin['username']}")
        expect(admin_row).not_to_contain_text(RAW_STAMP)

        # Revoke from the account panel; the confirm text is recorded (G28).
        page.goto(f"{base}/browse")
        page.get_by_test_id("account-menu-button").click()
        page.get_by_test_id("account-menu-profile").click()
        page.get_by_test_id("account-tab-tokens").click()
        pane = page.get_by_test_id("api-tokens-pane")
        row = pane.get_by_test_id("api-token-row").filter(has_text=name)
        expect(row).to_have_count(1)

        messages = []

        def _accept(dialog):
            messages.append(dialog.message)
            dialog.accept()

        page.once("dialog", _accept)
        with page.expect_response(
            lambda r: r.request.method == "DELETE" and "/api/tokens/" in r.url
        ):
            row.get_by_test_id("api-token-revoke").click()
        expect(row).to_have_count(0)
        assert messages == [f"Revoke token {name}?"]

        revoked = anon_ctx.request.get(stats_url, headers={"Authorization": f"Bearer {token}"})
        assert revoked.status == 401
        assert_page_clean(page)
    finally:
        anon_ctx.close()
        _delete_tokens(live_server["data_dir"], name)


def _open_tokens_pane(page, base):
    page.goto(f"{base}/browse")
    page.get_by_test_id("account-menu-button").click()
    page.get_by_test_id("account-menu-profile").click()
    page.get_by_test_id("account-tab-tokens").click()
    pane = page.get_by_test_id("api-tokens-pane")
    expect(pane).to_be_visible()
    return pane


@pytest.mark.parametrize("viewport", VIEWPORTS.values(), ids=VIEWPORTS.keys())
def test_a_long_token_list_scrolls_inside_the_modal(live_server, authed_page, setup_admin, viewport):
    """The card is viewport-bound, so the last row's Revoke stays reachable."""
    page = authed_page
    page.set_viewport_size(viewport)
    stem = f"scroll{viewport['width']}-"
    conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
    try:
        uid = conn.execute("SELECT id FROM users WHERE username = ?",
                           (setup_admin["username"],)).fetchone()[0]
        conn.executemany(
            "INSERT INTO api_tokens (user_id, name, token_hash, prefix) VALUES (?, ?, ?, ?)",
            [(uid, (stem + str(i)).ljust(64, "x"), f"{stem}{i:02d}".ljust(64, "0"), "shelf_seeded")
             for i in range(12)],
        )
        conn.commit()
    finally:
        conn.close()
    try:
        pane = _open_tokens_pane(page, live_server["url"])
        rows = pane.get_by_test_id("api-token-row").filter(has_text=stem)
        expect(rows).to_have_count(12)
        last = rows.last.get_by_test_id("api-token-revoke")
        last.scroll_into_view_if_needed()
        expect(last).to_be_in_viewport()
        assert_page_clean(page)
    finally:
        conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
        try:
            conn.execute("DELETE FROM api_tokens WHERE name LIKE ?", (stem + "%",))
            conn.commit()
        finally:
            conn.close()


def test_copy_without_a_clipboard_selects_the_token(live_server, authed_page):
    """Off a secure context navigator.clipboard is absent: fall back to selecting."""
    page = authed_page
    name = "no-clipboard"
    page.context.add_init_script("delete Navigator.prototype.clipboard;")
    try:
        pane = _open_tokens_pane(page, live_server["url"])
        assert page.evaluate("navigator.clipboard === undefined")
        pane.get_by_test_id("api-token-name-input").fill(name)
        pane.get_by_test_id("api-token-create").click()
        plaintext = pane.get_by_test_id("api-token-plaintext")
        expect(plaintext).to_have_value(re.compile(r"^shelf_"))
        copy_btn = pane.get_by_test_id("api-token-copy")
        copy_btn.click()
        expect(copy_btn).not_to_have_text("Copied")
        length = len(plaintext.input_value())
        assert plaintext.evaluate("el => [el.selectionStart, el.selectionEnd]") == [0, length]
        assert_page_clean(page)
    finally:
        _delete_tokens(live_server["data_dir"], name)
