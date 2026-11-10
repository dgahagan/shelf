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

    # CSV import file input
    file_input = authed_page.locator("input[type=file][accept*='.csv']")
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

    file_input = authed_page.locator("input[type=file][accept*='.csv']")
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

    # Issue #125 T11: a `read`, zero-owned Goodreads row lands neither owned
    # nor wishlisted — not the pre-#125 rule that owned=0 meant wishlisted.
    # Browse's owned=none filter is the third arm's own surface, scoped by
    # title so the assertion holds regardless of what else the session-scoped
    # server has accumulated (G34).
    authed_page.goto(f"{live_server['url']}/browse?owned=none&q=GR+Read+Book")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("GR Read Book")

    # Scoped to #item-grid (the swap target — present whether or not there
    # are results), not the whole body: the search chip above it echoes the
    # query text ("Search: GR Read Book"), which would otherwise make this
    # assertion pass vacuously. A no-match result renders "No items match
    # your filters" inside the same element, which is exactly what this
    # expects to find here.
    authed_page.goto(f"{live_server['url']}/browse?owned=1&q=GR+Read+Book")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("#item-grid")).not_to_contain_text("GR Read Book")


def test_librarything_import_shows_beta_and_unimported(live_server, authed_page):
    """A LibraryThing TSV export uploads through the settings UI, is detected
    as `librarything`, and the result card shows the literal "LibraryThing"
    name, the "Beta" label, and a "Not imported" line naming the unmapped
    Rating column.

    Synthetic fixture, built from the documented LibraryThing column list
    (design plan §"LibraryThing mapping"), not a real export.
    """
    header = [
        "Book Id", "Title", "Primary Author", "Author (First, Last)",
        "Secondary Author", "Other Authors", "ISBN", "ISBNs",
        "Publication Date", "Date", "Page Count", "Series", "Media",
        "Collections", "Date Read", "Date Ended", "Tags", "Your Tags",
        "Acquired", "Date Acquired", "From Where", "Purchase Price",
        "Condition", "Rating",
    ]
    rows = [
        ["1", "LT Test Book One", "Author One", "", "", "", "", "", "", "",
         "", "", "", "Your Library", "", "", "", "", "", "", "", "", "", "5"],
        ["2", "LT Test Book Two", "Author Two", "", "", "", "", "", "", "",
         "", "", "", "Your Library", "", "", "", "", "", "", "", "", "", "4"],
    ]
    tsv_content = "\n".join("\t".join(r) for r in [header] + rows) + "\n"

    authed_page.goto(f"{live_server['url']}/settings")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("button:has-text('Data')").click()

    file_input = authed_page.locator("input[type=file][accept*='.csv']")
    expect(file_input).to_be_visible()
    file_input.set_input_files({
        "name": "librarything_export.tsv",
        "mimeType": "text/tab-separated-values",
        "buffer": tsv_content.encode(),
    })

    form = file_input.locator("xpath=ancestor::form")
    with authed_page.expect_response("**/api/import/csv") as resp_info:
        form.locator("button[type=submit]").first.click()
    result = resp_info.value.json()
    assert result["format"] == "librarything", result
    assert result["imported"] == 2, result

    # Scoped to the result card so these never match a hidden x-show twin
    # elsewhere on the Settings page (G51, G70).
    result_block = authed_page.locator("[data-testid='csv-import-result']")
    expect(result_block).to_be_visible()
    expect(result_block.get_by_text("LibraryThing", exact=True)).to_be_visible()
    expect(result_block.get_by_text("Beta", exact=True)).to_be_visible()

    dropped = result_block.locator("[data-testid='csv-import-dropped']")
    expect(dropped).to_contain_text("rating")


def test_import_card_lists_created_locations(live_server, authed_page):
    """A generic CSV whose `location` cell names no existing location creates
    it, and the import card says so (#145)."""
    import sqlite3
    import uuid

    tag = uuid.uuid4().hex[:8]
    loc_name = f"E2E Attic {tag}"
    title = f"E2E Located Book {tag}"
    csv_content = f"title,location\n{title},{loc_name}\n"

    authed_page.goto(f"{live_server['url']}/settings")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("button:has-text('Data')").click()

    file_input = authed_page.locator("input[type=file][accept*='.csv']")
    expect(file_input).to_be_visible()
    file_input.set_input_files({
        "name": "located.csv",
        "mimeType": "text/csv",
        "buffer": csv_content.encode(),
    })

    try:
        form = file_input.locator("xpath=ancestor::form")
        with authed_page.expect_response(
                lambda r: "/api/import/csv" in r.url and r.request.method == "POST") as resp_info:
            form.locator("button[type=submit]").first.click()
        result = resp_info.value.json()
        assert result["imported"] == 1, result
        assert result["locations_created"] == [loc_name], result

        created = authed_page.locator(
            "[data-testid='csv-import-result'] [data-testid='csv-import-locations-created']")
        expect(created).to_contain_text(f"Created locations: {loc_name}")
    finally:
        # The session server's DB is shared with later tests (G126).
        conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
        try:
            # The location gave the item a primary copy; remove it first.
            conn.execute("DELETE FROM item_copies WHERE item_id IN "
                         "(SELECT id FROM items WHERE title = ?)", (title,))
            conn.execute("DELETE FROM items WHERE title = ?", (title,))
            conn.execute("DELETE FROM locations WHERE name = ?", (loc_name,))
            conn.commit()
        finally:
            conn.close()


def test_audible_cli_import_names_format_and_matched(live_server, authed_page):
    """An audible-cli TSV is named on the import card, and a row whose title
    and author match an existing ASIN-less audiobook is counted as matched
    rather than imported. Titles are unique: the server is shared (G34)."""
    seed_title = "Zephyrine Chronicles Seed Audiobook"
    new_title = "Zephyrine Chronicles Brand New Audiobook"
    insert_item(
        live_server["data_dir"], title=seed_title, authors="Quillon Marsh",
        media_type="audiobook", isbn="9789100000017",
    )
    header = [
        "asin", "title", "subtitle", "extended_product_description", "authors",
        "narrators", "series_title", "series_sequence", "genres",
        "runtime_length_min", "is_finished", "percent_complete", "rating",
        "num_ratings", "date_added", "release_date", "cover_url", "purchase_date",
    ]
    def row(asin, title):
        r = dict.fromkeys(header, "")
        r.update(asin=asin, title=title, authors="Quillon Marsh",
                 narrators="Some Narrator", runtime_length_min="600",
                 is_finished="False", percent_complete="0")
        return "\t".join(r[c] for c in header)
    content = "\n".join(
        ["\t".join(header), row("B0ZEPHYR01", seed_title), row("B0ZEPHYR02", new_title)]
    ) + "\n"

    errors = []
    authed_page.on("pageerror", lambda e: errors.append(str(e)))
    authed_page.goto(f"{live_server['url']}/settings")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("button:has-text('Data')").click()

    file_input = authed_page.locator("input[type=file][accept*='.csv']")
    expect(file_input).to_be_visible()
    file_input.set_input_files({
        "name": "audible_library.tsv",
        "mimeType": "text/tab-separated-values",
        "buffer": content.encode(),
    })
    form = file_input.locator("xpath=ancestor::form")
    with authed_page.expect_response("**/api/import/csv") as resp_info:
        form.locator("button[type=submit]").first.click()
    result = resp_info.value.json()
    assert result["format"] == "audible_cli", result
    assert result["matched"] == 1, result
    assert result["imported"] == 1, result

    summary = authed_page.get_by_test_id("csv-import-result")
    expect(summary).to_contain_text("Detected audible-cli export")
    expect(summary).not_to_contain_text("Audible_cli")
    expect(authed_page.get_by_test_id("csv-import-matched")).to_have_text("1")
    expect(summary).to_contain_text("Imported: 1")
    assert errors == [], errors
