"""E2E: picking a UI language in the Account dialog re-renders the page in it."""
import sqlite3

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
    finally:
        # The session's admin is shared by every E2E test; leave it on Automatic
        # so later tests still find the English text they click.
        conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
        try:
            conn.execute("UPDATE users SET language = NULL")
            conn.commit()
        finally:
            conn.close()
