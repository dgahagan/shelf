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
    from tests.e2e.conftest import _get_auth_cookies
    cookies = _get_auth_cookies(live_server, browser, setup_admin)
    return cookies.get("access_token", "")


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

    # CSV import is on the "Data" tab — click it first
    authed_page.locator("button:has-text('Data')").click()
    authed_page.wait_for_load_state("networkidle")

    # CSV import file input
    file_input = authed_page.locator("input[type=file][accept='.csv']")
    expect(file_input).to_be_visible()

    file_input.set_input_files({
        "name": "import.csv",
        "mimeType": "text/csv",
        "buffer": csv_content.encode(),
    })

    # Scope to the CSV import form to avoid matching the Hardcover import button
    submit = file_input.locator("xpath=ancestor::form").locator("button[type=submit], input[type=submit]").first

    submit.click()
    authed_page.wait_for_load_state("networkidle")

    # Navigate to browse and verify the imported item appears
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("Imported Book")
