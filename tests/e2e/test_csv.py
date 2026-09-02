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
    insert_item(live_server["data_dir"], title="Export Test Book", media_type="book", isbn="9780001112223")

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
    csv_content = "title,media_type,isbn\nImported Book,book,9780002223331\n"

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

    # The form is `@submit.prevent="doImport"` — an Alpine fetch to
    # /api/import/csv, not a navigation. So there is nothing for a
    # `wait_for_load_state("networkidle")` after the click to wait *for*: the
    # page is frequently already idle, the wait returns immediately, and the
    # goto below then races the still-in-flight import, so Browse renders
    # before the row exists. Wait on the POST's response instead — once it has
    # returned, the server has committed the rows.
    #
    # Measured as an intermittent CI failure on #76, a branch that touches
    # nothing on the CSV path. Same class as the flake #79 fixed in
    # tests/e2e/test_bulk_actions.py, but *not* the same fix: that form really
    # does navigate, this one does not, and `expect_navigation` here times out.
    with authed_page.expect_response(
            lambda r: "/api/import/csv" in r.url and r.request.method == "POST"):
        submit.click()

    # Navigate to browse and verify the imported item appears
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("Imported Book")


def test_goodreads_import_via_ui(live_server, authed_page):
    """A Goodreads-shaped export uploads through the settings UI, is auto-detected,
    and lands with reading status + wishlist mapping applied."""
    header = (
        "Book Id,Title,Author,Author l-f,Additional Authors,ISBN,ISBN13,My Rating,"
        "Average Rating,Publisher,Binding,Number of Pages,Year Published,"
        "Original Publication Year,Date Read,Date Added,Bookshelves,"
        "Bookshelves with positions,Exclusive Shelf,My Review,Spoiler,"
        "Private Notes,Read Count,Owned Copies"
    )
    rows = [
        '1,GR Read Book,Ann Author,"Author, Ann",,"=""""","=""9789000000111""",'
        '5,4.2,Ace,Paperback,300,2005,1999,2023/08/15,2023/01/02,,,read,,,,1,0',
        '2,GR Wishlist Book,Bob Writer,"Writer, Bob",,"=""""","=""9789000000289""",'
        '0,4.0,Bantam,Hardcover,500,1990,1989,,2023/01/02,,,to-read,,,,0,0',
    ]
    csv_content = header + "\n" + "\n".join(rows) + "\n"

    authed_page.goto(f"{live_server['url']}/settings")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("button:has-text('Data')").click()

    file_input = authed_page.locator("input[type=file][accept='.csv']")
    expect(file_input).to_be_visible()
    file_input.set_input_files({
        "name": "goodreads_library_export.csv",
        "mimeType": "text/csv",
        "buffer": csv_content.encode(),
    })

    form = file_input.locator("xpath=ancestor::form")
    form.locator("input[name=to_read_wishlist]").check()

    with authed_page.expect_response("**/api/import/csv") as resp_info:
        form.locator("button[type=submit]").first.click()
    result = resp_info.value.json()
    assert result["format"] == "goodreads", result
    assert result["imported"] == 2, result

    # Detection + counts surface in the UI
    expect(authed_page.locator("text=Detected").first).to_be_visible()

    # Both items appear on browse; the to-read one carries the Wishlist badge
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("GR Read Book")
    expect(authed_page.locator("body")).to_contain_text("GR Wishlist Book")
    expect(authed_page.get_by_text("Wishlist", exact=True).first).to_be_visible()
