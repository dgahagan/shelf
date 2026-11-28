"""E2E: picking a UI language in the Account dialog re-renders the page in it."""
import sqlite3

import httpx
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_picking_italian_translates_the_page(live_server, authed_page):
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    try:
        authed_page.get_by_test_id("account-menu-button").click()
        authed_page.get_by_test_id("account-menu-profile").click()
        picker = authed_page.get_by_test_id("account-language")
        expect(picker).to_be_visible()
        # saveLanguage() reloads on success: the whole page re-renders server-side.
        with authed_page.expect_navigation():
            picker.select_option("it")

        expect(authed_page.locator("html")).to_have_attribute("lang", "it")
        expect(authed_page.locator("[data-nav-tab='browse']")).to_have_text("Sfoglia")
        # Alpine reads this one through a t() getter, under the CSP build.
        authed_page.get_by_role("button", name="Seleziona", exact=True).click()
        expect(authed_page.get_by_role("button", name="Fatto", exact=True)).to_be_visible()
    finally:
        # The session's admin is shared by every E2E test; leave it on Automatic
        # so later tests still find the English text they click.
        conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
        try:
            conn.execute("UPDATE users SET language = NULL")
            conn.commit()
        finally:
            conn.close()


def test_factory_server_ignores_a_shell_turning_the_ui_off(server_factory, monkeypatch):
    """`server_factory` clears SHELF_I18N like the other boot paths, so a
    fresh server is on by default whatever the shell says (codex M1)."""
    monkeypatch.setenv("SHELF_I18N", "off")
    srv = server_factory()
    resp = httpx.get(f"{srv['url']}/setup", headers={"Accept-Language": "it"})
    assert '<html lang="it"' in resp.text
