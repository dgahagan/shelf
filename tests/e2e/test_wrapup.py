"""E2E coverage for the wrap-up preview and its browser-drawn PNG export.

Drives `/stats/wrapup` at desktop and mobile viewports, proves the canvas
actually reaches `data-ready`/`data-font="inter"`, and that clicking the
download link (static/js/wrapup.js) yields a real PNG under a predictable
name. Also proves a null cover and a cover file missing on disk both still
produce a tile and a working export (wrapup.js's `loadCover` resolves those
to a placeholder rather than aborting), and that an empty period shows the
empty state with no canvas/download control.

Server per test via `server_factory` (test_stats.py's shape) rather than the
session-scoped `live_server` — no G126 cleanup is needed because nothing here
is shared with the other ~130 E2E tests.
"""
import os
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import _run_setup_wizard, assert_page_clean, attach_page_guard, insert_item

pytestmark = pytest.mark.e2e

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_VIOLATION_PROBE = """
window.__cspViolations = [];
document.addEventListener('securitypolicyviolation', function(e) {
    window.__cspViolations.push(
        e.violatedDirective + ' <- ' + (e.blockedURI || e.sourceFile || 'inline')
    );
});
"""

_VIEWPORTS = {
    "desktop": {"viewport": {"width": 1280, "height": 800}},
    "mobile": {
        "viewport": {"width": 390, "height": 844},
        "is_mobile": True,
        "has_touch": True,
    },
}


def _seed_finished_period(server, period: str) -> None:
    """Two items finished in `period` (YYYY-MM) — one with no cover, one whose
    cover_path points at a file that was never written to disk. Both must
    still produce a tile and a working export (wrapup.js's loadCover
    resolves a missing/failed image to a placeholder, never a failure)."""
    insert_item(
        server["data_dir"],
        title="Wrap-up No Cover",
        media_type="book",
        isbn="9780000200016",
        authors="Wrap Author",
        owned=1,
        reading_status="read",
        date_finished=f"{period}-05",
        cover_path=None,
    )
    insert_item(
        server["data_dir"],
        title="Wrap-up Missing Cover File",
        media_type="book",
        isbn="9780000200023",
        authors="Wrap Author",
        owned=1,
        reading_status="read",
        date_finished=f"{period}-10",
        cover_path="covers/999999.jpg",
    )


def _login(browser, server, credentials, context_kwargs=None):
    """New context + guarded page, logged in — the shape test_stats.py and
    test_scan.py's `_login_page` both use, inlined here since it also needs
    to accept per-viewport context kwargs (test_scan.py:2062)."""
    ctx = browser.new_context(**(context_kwargs or {}))
    page = attach_page_guard(ctx.new_page())
    page.add_init_script(_VIOLATION_PROBE)
    page.goto(f"{server['url']}/login")
    page.fill("input[name=username]", credentials["username"])
    page.fill("input[name=password]", credentials["password"])
    page.click("button[type=submit]")
    page.wait_for_url(f"{server['url']}/", timeout=10_000)
    return ctx, page


def _wait_for_canvas_ready(page, timeout_s: float = 15.0) -> None:
    """Poll from Python (G21 — wait_for_function needs eval(), CSP refuses it)."""
    deadline = time.monotonic() + timeout_s
    expr = (
        "(function () { var c = document.getElementById('wrapup-canvas'); "
        "return c && c.getAttribute('data-ready'); })()"
    )
    while time.monotonic() < deadline:
        if page.evaluate(expr) == "true":
            return
        page.wait_for_timeout(100)
    raise AssertionError("wrapup-canvas never reached data-ready=\"true\" within "
                          f"{timeout_s}s")


@pytest.mark.parametrize("context_kwargs,label", [
    pytest.param(_VIEWPORTS["desktop"], "desktop", id="desktop"),
    pytest.param(_VIEWPORTS["mobile"], "mobile", id="mobile"),
])
def test_wrapup_preview_draws_clean_and_downloads_png(
    server_factory, browser, context_kwargs, label
):
    """The preview loads with no console/page errors and no CSP violations,
    the canvas draws with the bundled Inter font, and the download link
    yields a real PNG named for the period — for a period containing only a
    no-cover item and a missing-cover-file item."""
    server = server_factory()
    credentials = _run_setup_wizard(browser, server["url"])
    _seed_finished_period(server, "2025-06")

    ctx, page = _login(browser, server, credentials, context_kwargs)
    try:
        page.goto(f"{server['url']}/stats/wrapup?period=2025-06")
        page.wait_for_load_state("networkidle")

        data_block = page.locator("#wrapup-data")
        expect(data_block).to_have_attribute("data-showing", "finished")

        _wait_for_canvas_ready(page)
        canvas = page.locator("#wrapup-canvas")
        expect(canvas).to_have_attribute("data-ready", "true")
        expect(canvas).to_have_attribute("data-font", "inter")

        download_link = page.locator("#wrapup-download")
        expect(download_link).to_be_visible()

        with page.expect_download() as download_info:
            download_link.click()
        download = download_info.value

        assert download.suggested_filename == "shelf-wrapup-2025-06.png", (
            download.suggested_filename
        )
        saved_path = download.path()
        assert saved_path is not None
        with open(saved_path, "rb") as f:
            header = f.read(8)
        assert header == _PNG_SIGNATURE, header

        sample_out = os.environ.get("WRAPUP_SAMPLE_OUT")
        if sample_out and label == "desktop":
            out_path = Path(sample_out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(Path(saved_path).read_bytes())

        assert page.evaluate("window.__cspViolations") == []
        assert_page_clean(page)
    finally:
        ctx.close()


def test_wrapup_empty_period_shows_empty_state_and_no_download(server_factory, browser):
    """A period with nothing finished or added renders the empty state —
    no #wrapup-data, no canvas, no download control — and stays clean."""
    server = server_factory()
    credentials = _run_setup_wizard(browser, server["url"])
    # No items seeded on this server at all, so every period is empty.

    ctx, page = _login(browser, server, credentials, _VIEWPORTS["desktop"])
    try:
        page.goto(f"{server['url']}/stats/wrapup?period=2019-01")
        page.wait_for_load_state("networkidle")

        expect(page.locator('[data-testid="wrapup-empty"]')).to_be_visible()
        expect(page.locator("#wrapup-data")).to_have_count(0)
        expect(page.locator("#wrapup-canvas")).to_have_count(0)
        expect(page.locator("#wrapup-download")).to_have_count(0)

        assert page.evaluate("window.__cspViolations") == []
        assert_page_clean(page)
    finally:
        ctx.close()
