"""E2E tests: portable archive export/import through the Settings UI.

Drives the real "Portable archive" card (Settings -> Data tab): downloads
the export zip via the plain <a href> link, then re-imports that same file
through the card's two-step preview flow (choose file/mode -> "Preview
import" -> review the plan and toggle what to include -> "Import N items"
-> confirm/apply) in skip mode, update mode, and a fresh-instance restore
with a deselected facet. The fresh-instance restore/merge matrix itself
(verdict classification, drift, cover semantics) is covered at unit level
in tests/test_archive.py — this only proves the UI wiring end to end,
including that /plan writes nothing and /apply honors selection toggles.
"""
import json
import re
import sqlite3
import zipfile

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import assert_page_clean, insert_item

pytestmark = pytest.mark.e2e

# Minimal valid JPEG-signature bytes, well above covers.MIN_COVER_SIZE (100
# bytes) and under MAX_COVER_SIZE — same convention as tests/test_archive.py.
_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200

_PANEL = "[x-data='archivePanel']"


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


def _seed_reading_log(live_server, item_id: int, count: int = 1) -> None:
    """Insert `count` reading_log rows against an already-seeded item."""
    conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
    try:
        for _ in range(count):
            conn.execute(
                "INSERT INTO reading_log (item_id, status, date_started, date_finished) "
                "VALUES (?, 'finished', '2026-01-01', '2026-01-15')",
                (item_id,),
            )
        conn.commit()
    finally:
        conn.close()


def _wipe_library(live_server) -> None:
    """Delete every item (and its dependent reading_log/checkouts rows) to
    put the target instance in the fresh-instance state a restore-style
    import expects — same harness convention as _seed_item_with_cover,
    reaching the DB directly via sqlite3 rather than through the app."""
    conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
    try:
        conn.execute("DELETE FROM reading_log")
        conn.execute("DELETE FROM checkouts")
        conn.execute("DELETE FROM items")
        conn.commit()
    finally:
        conn.close()


def _item_count(live_server) -> int:
    conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
    try:
        return conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    finally:
        conn.close()


def _reading_log_count(live_server) -> int:
    conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
    try:
        return conn.execute("SELECT COUNT(*) FROM reading_log").fetchone()[0]
    finally:
        conn.close()


def _open_data_tab(page, live_server):
    page.goto(f"{live_server['url']}/settings")
    page.wait_for_load_state("networkidle")
    page.locator("button:has-text('Data')").click()
    page.wait_for_load_state("networkidle")


def _result_text(page) -> str:
    # Scoped to the archive panel — the CSV import card above it also has a
    # "p:has-text('Imported:')" result line, so an unscoped locator would be
    # ambiguous (Playwright's strict mode rejects multi-element matches).
    return page.locator(f"{_PANEL} p:has-text('Imported:')").inner_text()


def _download_archive(page) -> str:
    with page.expect_download() as download_info:
        page.get_by_role("link", name="Export Archive").click()
    zip_path = download_info.value.path()
    assert zip_path is not None
    return zip_path


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

    zip_path = _download_archive(authed_page)

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
    the library changed in between) must dedupe every item through the
    two-step preview flow: skip mode previews+confirms with everything
    skipped and nothing imported; "Start over" then a fresh preview under
    update mode reports an update for the same items — and, per the 0.7.0
    cover-default flip, does not replace the item's existing cover since
    "Replace existing covers" is off by default."""
    _seed_item_with_cover(
        live_server,
        title="Archive Reimport Book",
        media_type="book",
        isbn="9780000777777",
    )

    _open_data_tab(authed_page, live_server)

    zip_path = _download_archive(authed_page)

    file_input = authed_page.locator("input[type=file][accept='.zip']")
    expect(file_input).to_be_visible()
    form = file_input.locator("xpath=ancestor::form")
    preview_btn = form.locator("button[type=submit], input[type=submit]").first
    # Regex anchored on "Import " (capital I) rather than a plain substring
    # match: the step-1 submit button's own label, "Preview import", also
    # contains "import" case-insensitively and would otherwise tie a
    # has_text="Import" locator into a false-positive match on step 1,
    # before the plan panel even renders.
    apply_btn = authed_page.locator(_PANEL).get_by_role("button", name=re.compile(r"^Import \d"))
    # "Start over" appears in both the step-2 and step-3 markup (the step-3
    # copy stays in the DOM, just hidden, via x-show rather than x-if) —
    # :visible picks out whichever one is actually on screen.
    start_over_btn = authed_page.locator(f"{_PANEL} button:visible", has_text="Start over")

    # --- skip mode (default): preview -----------------------------------
    file_input.set_input_files(str(zip_path))
    with authed_page.expect_response("**/api/import/archive/plan") as resp_info:
        preview_btn.click()
    plan_result = resp_info.value.json()
    assert plan_result.get("error") is None, plan_result
    assert plan_result["plan"]["mode"] == "skip", plan_result
    assert plan_result["plan"]["summary"]["skip"] >= 1, plan_result

    # step 2 is showing — confirm the skip-mode plan.
    expect(apply_btn).to_be_visible()

    # --- skip mode: confirm ----------------------------------------------
    with authed_page.expect_response("**/api/import/archive/apply") as resp_info:
        apply_btn.click()
    skip_result = resp_info.value.json()
    assert skip_result.get("error") is None, skip_result
    assert skip_result["imported"] == 0, skip_result
    assert skip_result["skipped"] >= 1, skip_result

    result_line = _result_text(authed_page)
    m = re.search(r"Skipped:\s*(\d+)", result_line)
    assert m and int(m.group(1)) >= 1, result_line
    m = re.search(r"Imported:\s*(\d+)", result_line)
    assert m and int(m.group(1)) == 0, result_line

    # --- update mode: start over, re-select the file, preview again ------
    # Each import consumes the staged upload, so update mode needs its own
    # fresh preview rather than reusing the skip-mode one.
    start_over_btn.click()
    expect(file_input).to_be_visible()

    file_input.set_input_files(str(zip_path))
    form.locator("select[name=mode]").select_option("update")
    with authed_page.expect_response("**/api/import/archive/plan") as resp_info:
        preview_btn.click()
    plan_result = resp_info.value.json()
    assert plan_result.get("error") is None, plan_result
    assert plan_result["plan"]["mode"] == "update", plan_result
    assert plan_result["plan"]["summary"]["update"] >= 1, plan_result

    expect(apply_btn).to_be_visible()

    with authed_page.expect_response("**/api/import/archive/apply") as resp_info:
        apply_btn.click()
    update_result = resp_info.value.json()
    assert update_result.get("error") is None, update_result
    assert update_result["imported"] == 0, update_result
    assert update_result["updated"] >= 1, update_result
    # 0.7.0 behavior change: updated items keep their existing cover unless
    # "Replace existing covers" is opted into — it's off by default here.
    assert update_result.get("covers_replaced", 0) == 0, update_result

    result_line = _result_text(authed_page)
    m = re.search(r"Updated:\s*(\d+)", result_line)
    assert m and int(m.group(1)) >= 1, result_line


def test_archive_preview_deselect_reading_log(live_server, authed_page):
    """The design plan's preview scenario: seed items with reading-log rows,
    export, restore into a fresh-instance target, preview the import,
    untick the reading-log checkbox, and confirm. The items land but their
    reading-log rows don't, and both the apply response and the on-screen
    report say so. Also proves /plan itself writes nothing to the DB."""
    item_a = _seed_item_with_cover(
        live_server,
        title="Reading Log Book A",
        media_type="book",
        isbn="9780000777801",
    )
    item_b = _seed_item_with_cover(
        live_server,
        title="Reading Log Book B",
        media_type="book",
        isbn="9780000777802",
    )
    _seed_reading_log(live_server, item_a, count=2)
    _seed_reading_log(live_server, item_b, count=1)
    total_reading_log_rows = 3

    _open_data_tab(authed_page, live_server)
    zip_path = _download_archive(authed_page)

    # live_server is session-scoped (shared across the whole e2e run), so
    # the archive just downloaded also carries whatever earlier tests in
    # this file (and only this file — it runs first, alphabetically) left
    # behind. Read the exact item count out of the zip itself rather than
    # assuming this test's 2 items are the only ones in it.
    with zipfile.ZipFile(zip_path) as zf:
        archive_items = json.loads(zf.read("library.json"))["items"]
    expected_item_count = len(archive_items)
    assert expected_item_count >= 2, archive_items

    # Restore target: a fresh instance with nothing in it. Safe precisely
    # because this is the last test in the first file to run in the e2e
    # session — nothing downstream depends on items seeded before this
    # point still existing.
    _wipe_library(live_server)
    assert _item_count(live_server) == 0

    file_input = authed_page.locator("input[type=file][accept='.zip']")
    expect(file_input).to_be_visible()
    form = file_input.locator("xpath=ancestor::form")
    preview_btn = form.locator("button[type=submit], input[type=submit]").first
    apply_btn = authed_page.locator(_PANEL).get_by_role("button", name=re.compile(r"^Import \d"))

    file_input.set_input_files(str(zip_path))
    with authed_page.expect_response("**/api/import/archive/plan") as resp_info:
        preview_btn.click()
    plan_result = resp_info.value.json()
    assert plan_result.get("error") is None, plan_result
    plan = plan_result["plan"]
    # The target is empty, so every archived item classifies as a create
    # regardless of mode.
    assert plan["summary"]["create"] == expected_item_count, plan
    assert plan["summary"]["reading_log"] == total_reading_log_rows, plan

    # Step 2 is showing (the plan panel rendered) — /plan must not have
    # written anything to the library yet.
    expect(apply_btn).to_be_visible()
    assert _item_count(live_server) == 0

    reading_log_checkbox = authed_page.locator(
        f"{_PANEL} label:has-text('reading-log entries') input[type=checkbox]"
    )
    expect(reading_log_checkbox).to_be_checked()
    reading_log_checkbox.uncheck()

    with authed_page.expect_response("**/api/import/archive/apply") as resp_info:
        apply_btn.click()
    apply_result = resp_info.value.json()
    assert apply_result.get("error") is None, apply_result
    assert apply_result["imported"] == expected_item_count, apply_result
    assert apply_result["deselected"]["reading_log"] == total_reading_log_rows, apply_result

    # Items are present (including specifically the two seeded here), but
    # no reading-log rows came in with them.
    assert _item_count(live_server) == expected_item_count
    assert _reading_log_count(live_server) == 0
    conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
    try:
        titles = {row[0] for row in conn.execute("SELECT title FROM items")}
    finally:
        conn.close()
    assert "Reading Log Book A" in titles, titles
    assert "Reading Log Book B" in titles, titles

    deselected_lines = authed_page.locator(f"{_PANEL} li").all_inner_texts()
    assert any(
        "Reading log" in line and str(total_reading_log_rows) in line
        for line in deselected_lines
    ), deselected_lines


def test_archive_import_failure_reports_without_throwing(live_server, authed_page):
    """A network-level failure of /apply is the one state that reaches
    archivePanel's catch, which sets `importResult = {error: 'Import failed'}`
    — an object with **no** `errors` key — and then shows step 3.

    Step 3's error-list guard dereferences `importResult.errors`, so under the
    CSP build (which evaluates both sides of `&&` and throws on a null object)
    a `&&`-shaped guard, or a ternary rooted at `importResult` itself, would
    evaluate `undefined.length` right here and throw. This is the only path
    that exercises that state, which is why it exists: the visible report is
    half the assertion, and the page-error guard is the other half.
    """
    _seed_item_with_cover(
        live_server,
        title="Archive Failure Book",
        media_type="book",
        isbn="9780000777888",
    )

    _open_data_tab(authed_page, live_server)
    zip_path = _download_archive(authed_page)

    file_input = authed_page.locator("input[type=file][accept='.zip']")
    expect(file_input).to_be_visible()
    form = file_input.locator("xpath=ancestor::form")
    preview_btn = form.locator("button[type=submit], input[type=submit]").first
    apply_btn = authed_page.locator(_PANEL).get_by_role("button", name=re.compile(r"^Import \d"))

    file_input.set_input_files(str(zip_path))
    with authed_page.expect_response("**/api/import/archive/plan") as resp_info:
        preview_btn.click()
    assert resp_info.value.json().get("error") is None, resp_info.value.json()
    expect(apply_btn).to_be_visible()

    # Kill the apply request at the network level so the client-side catch
    # runs — the server's own refusal payloads always carry `errors: []`, so
    # this is the only way to reach the no-`errors`-key shape.
    authed_page.route("**/api/import/archive/apply", lambda route: route.abort())
    apply_btn.click()

    # Anchored on the x-text binding, not the colour class: the panel's
    # generic `errorMessage` paragraph carries text-shelf-error too.
    report = authed_page.locator(f"{_PANEL} p[x-text='importResult.error']")
    expect(report).to_contain_text("Import failed")
    # No "Imported: ..." summary line — this is the failure branch, not a
    # zero-count success.
    expect(authed_page.locator(f"{_PANEL} p:has-text('Imported:')")).to_have_count(0)

    assert_page_clean(authed_page)
