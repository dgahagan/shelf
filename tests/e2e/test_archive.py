"""E2E tests: portable archive export/import through the Settings UI.

Drives the real "Portable archive" card (Settings -> Data tab): downloads
the export zip via the plain <a href> link, inspects it, then re-imports
that same file through the card's import form in both skip and update
modes. The fresh-instance restore/merge matrix is covered at unit level in
tests/test_archive.py — this only proves the UI wiring.
"""
import json
import re
import sqlite3
import zipfile

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item

pytestmark = pytest.mark.e2e

# Minimal valid JPEG-signature bytes, well above covers.MIN_COVER_SIZE (100
# bytes) and under MAX_COVER_SIZE — same convention as tests/test_archive.py.
_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200


def _seed_item_with_cover(live_server, **kwargs) -> int:
    """Insert an item and plant a matching cover file directly on disk (no
    network fetch), then point cover_path at it — mirrors what
    save_uploaded_cover/download_cover normally do."""
    item_id = insert_item(live_server["data_dir"], **kwargs)

    covers_dir = live_server["data_dir"] / "covers"
    covers_dir.mkdir(exist_ok=True)
    (covers_dir / f"{item_id}.jpg").write_bytes(_JPEG)

    conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
    try:
        conn.execute(
            "UPDATE items SET cover_path = ? WHERE id = ?",
            (f"covers/{item_id}.jpg", item_id),
        )
        conn.commit()
    finally:
        conn.close()
    return item_id


def _open_data_tab(page, live_server):
    page.goto(f"{live_server['url']}/settings")
    page.wait_for_load_state("networkidle")
    page.locator("button:has-text('Data')").click()
    page.wait_for_load_state("networkidle")


def _result_text(page) -> str:
    # Scoped to the archive panel — the CSV import card above it also has a
    # "p:has-text('Imported:')" result line, so an unscoped locator would be
    # ambiguous (Playwright's strict mode rejects multi-element matches).
    return page.locator("[x-data='archivePanel'] p:has-text('Imported:')").inner_text()


def test_archive_export_download_contains_expected_entries(live_server, authed_page):
    """The Export Archive link downloads a zip with manifest.json,
    library.json, and at least one covers/... entry."""
    _seed_item_with_cover(
        live_server,
        title="Archive E2E Book",
        media_type="book",
        isbn="9780000777666",
    )

    _open_data_tab(authed_page, live_server)

    with authed_page.expect_download() as download_info:
        authed_page.get_by_role("link", name="Export Archive").click()
    download = download_info.value
    zip_path = download.path()
    assert zip_path is not None

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert "manifest.json" in names, names
        assert "library.json" in names, names
        cover_entries = [n for n in names if n.startswith("covers/") and n.endswith(".jpg")]
        assert cover_entries, f"expected at least one covers/*.jpg entry, got: {names}"

        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["format"] == "shelf-archive"
        library = json.loads(zf.read("library.json"))
        assert any(i["title"] == "Archive E2E Book" for i in library["items"]), library["items"]


def test_archive_reimport_skip_then_update(live_server, authed_page):
    """Downloading the archive and re-importing it immediately (nothing in
    the library changed in between) must dedupe every item: skip mode
    reports everything skipped and nothing imported; update mode then
    reports an update for the same items."""
    _seed_item_with_cover(
        live_server,
        title="Archive Reimport Book",
        media_type="book",
        isbn="9780000777777",
    )

    _open_data_tab(authed_page, live_server)

    with authed_page.expect_download() as download_info:
        authed_page.get_by_role("link", name="Export Archive").click()
    zip_path = download_info.value.path()
    assert zip_path is not None

    file_input = authed_page.locator("input[type=file][accept='.zip']")
    expect(file_input).to_be_visible()
    form = file_input.locator("xpath=ancestor::form")
    submit = form.locator("button[type=submit], input[type=submit]").first

    # --- skip mode (default) ---------------------------------------------
    file_input.set_input_files(str(zip_path))
    with authed_page.expect_response("**/api/import/archive") as resp_info:
        submit.click()
    skip_result = resp_info.value.json()
    assert skip_result.get("error") is None, skip_result
    assert skip_result["imported"] == 0, skip_result
    assert skip_result["skipped"] >= 1, skip_result

    result_line = _result_text(authed_page)
    m = re.search(r"Skipped:\s*(\d+)", result_line)
    assert m and int(m.group(1)) >= 1, result_line
    m = re.search(r"Imported:\s*(\d+)", result_line)
    assert m and int(m.group(1)) == 0, result_line

    # --- update mode --------------------------------------------------------
    file_input.set_input_files(str(zip_path))
    form.locator("select[name=mode]").select_option("update")
    with authed_page.expect_response("**/api/import/archive") as resp_info:
        submit.click()
    update_result = resp_info.value.json()
    assert update_result.get("error") is None, update_result
    assert update_result["imported"] == 0, update_result
    assert update_result["updated"] >= 1, update_result

    result_line = _result_text(authed_page)
    m = re.search(r"Updated:\s*(\d+)", result_line)
    assert m and int(m.group(1)) >= 1, result_line
