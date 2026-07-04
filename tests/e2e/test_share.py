"""E2E: share links — create in settings, view logged-out, revoke."""
import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item

pytestmark = pytest.mark.e2e


def test_share_link_full_lifecycle(live_server, browser, authed_page):
    insert_item(live_server["data_dir"], title="Share Me", isbn="9780905000019", owned=0)

    # Create a wishlist link on the settings Data tab
    authed_page.goto(f"{live_server['url']}/settings")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("button:has-text('Data')").click()
    share_form = authed_page.locator("form[action='/api/share']")
    share_form.locator("select[name=scope]").select_option("wishlist")
    share_form.locator("input[name=label]").fill("E2E Gift List")
    share_form.locator("button[type=submit]").click()
    authed_page.wait_for_load_state("networkidle")

    authed_page.locator("button:has-text('Data')").click()
    row = authed_page.get_by_test_id("share-link-row").first
    expect(row).to_contain_text("E2E Gift List")
    token = row.locator("button[data-token]").get_attribute("data-token")
    share_url = f"{live_server['url']}/share/{token}"

    # A completely unauthenticated browser context can view it
    ctx = browser.new_context()
    try:
        page = ctx.new_page()
        page.add_init_script(
            "window.__cspViolations = [];"
            "document.addEventListener('securitypolicyviolation', function(e) {"
            "  window.__cspViolations.push(e.violatedDirective);"
            "});"
        )
        resp = page.goto(share_url)
        assert resp.status == 200
        expect(page.locator("body")).to_contain_text("E2E Gift List")
        expect(page.locator("body")).to_contain_text("Share Me")
        assert page.evaluate("window.__cspViolations") == []

        # Revoke in the admin session; public access dies
        authed_page.get_by_test_id("share-link-row").first.locator(
            "button:has-text('Revoke')").click()
        authed_page.wait_for_load_state("networkidle")
        resp = page.goto(share_url)
        assert resp.status == 404
    finally:
        ctx.close()
