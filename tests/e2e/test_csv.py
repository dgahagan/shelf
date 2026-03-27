"""E2E tests: CSV export/import round-trip."""
import csv
import io
import urllib.request

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item, ADMIN_USERNAME, ADMIN_PASSWORD

pytestmark = pytest.mark.e2e


def _get_token_cookie(live_server, browser, setup_admin) -> str:
    """Retrieve auth token from browser session."""
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.goto(f"{live_server['url']}/login")
    pg.fill("input[name=username]", setup_admin["username"])
    pg.fill("input[name=password]", setup_admin["password"])
    pg.click("button[type=submit]")
    pg.wait_for_url(f"{live_server['url']}/browse", timeout=10_000)
    cookies = ctx.cookies()
    token = next(c["value"] for c in cookies if c["name"] == "access_token")
    ctx.close()
    return token


def test_csv_export(live_server, browser, setup_admin):
    """GET /api/export/csv returns a CSV file containing seeded items."""
    insert_item(live_server["data_dir"], title="Export Test Book", media_type="book", isbn="9780000111222")

    token = _get_token_cookie(live_server, browser, setup_admin)
    req = urllib.request.Request(
        f"{live_server['url']}/api/export/csv",
        headers={"Cookie": f"access_token={token}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 200
        content_disp = resp.headers.get("Content-Disposition", "")
        assert "csv" in content_disp or "attachment" in content_disp
        body = resp.read().decode("utf-8")

    reader = csv.DictReader(io.StringIO(body))
    titles = [row.get("title", "") for row in reader]
    assert any("Export Test Book" in t for t in titles), f"Expected book not in export; got: {titles}"


def test_csv_import(live_server, authed_page):
    """Uploading a CSV file via the settings page imports items."""
    # Build a minimal CSV
    csv_content = "title,media_type,isbn\nImported Book,book,9780000222333\n"

    authed_page.goto(f"{live_server['url']}/settings")
    authed_page.wait_for_load_state("networkidle")

    # Look for CSV import file input
    file_input = authed_page.locator("input[type=file][accept*='csv'], input[type=file][name*='csv'], input[type=file]").first
    if file_input.count() == 0:
        pytest.skip("No CSV file import input found on settings page")

    file_input.set_input_files({
        "name": "import.csv",
        "mimeType": "text/csv",
        "buffer": csv_content.encode(),
    })

    submit = authed_page.locator(
        "button:has-text('Import'), input[type=submit][value*='Import']"
    ).first
    if submit.count() == 0:
        # Try generic submit near the file input
        submit = file_input.locator("xpath=ancestor::form").locator("button[type=submit], input[type=submit]").first

    submit.click()
    authed_page.wait_for_load_state("networkidle")

    # Navigate to browse and verify the imported item appears
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("Imported Book")
