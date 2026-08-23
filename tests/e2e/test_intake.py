"""E2E tests: photo-intake review row and a full offline confirm round-trip.

`live_server` is session-scoped, so this file sets the vision provider it
needs and restores the empty baseline in a teardown that runs regardless of
outcome — `test_nav.py`'s `nav_page` assumes "no vision provider" as its
baseline and would fail if this file leaked one.

Offline by construction, the same discipline `tests/e2e/test_scan.py:74-80`
records: nothing here reaches Open Library. `/api/intake/analyze` is
intercepted in the browser, row 1 is seeded as a title+authors duplicate so
confirm skips it before any lookup, and row 2 is set to a non-book media
type (no book search) with a checksum-invalid ISBN (no cascade).
"""
import sqlite3
import time

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import APP_DIR, insert_item, wait_for_video_ready

pytestmark = pytest.mark.e2e

FIXTURE_PHOTO = APP_DIR / "tests/fixtures/intake/eleven_books.jpg"

# What the intercepted provider "sees": one transcribed row, one recognized.
ANALYZE_RESPONSE = {
    "ok": True,
    "books": [
        {"title": "E2E Read Book", "authors": "Someone", "isbn": None, "source": "read"},
        {"title": "E2E Recognized Disc", "authors": None, "isbn": None,
         "source": "recognized"},
    ],
}

# Copied, not imported, from tests/e2e/test_scan.py — the two camera paths
# share the house error wording, nothing else.
CAMERA_ERRORS = ("Camera access denied", "Camera requires HTTPS", "No camera found")

# The exact shape POST /api/intake/plan returns (app/routers/intake.py). It is
# intercepted rather than run for real in the camera tests because Chromium's
# fake device's track size is not a contract.
PLAN_OK = {
    "ok": True,
    "factor": 1.0,
    "needs_choice": False,
    "low_res": False,
    "low_res_long_edge": 2400,
    "preview": {"w": 640, "h": 480},
    "tiles": [{"x": 0, "y": 0, "w": 640, "h": 480}],
    "grid": {"rows": 1, "cols": 1},
    "cost_as_is_usd": None,
    "cost_tiled_usd": None,
}
PLAN_LOW = {**PLAN_OK, "low_res": True}


def _png_bytes(width, height):
    """A valid grayscale PNG of arbitrary size, built from the stdlib.

    Pillow happens to be importable in this environment but is not a declared
    dependency of Shelf or its test suite, so the second photo these tests
    need is synthesized here instead. Solid colour, so it compresses to a few
    KB whatever the dimensions.
    """
    import struct
    import zlib

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _csrf_headers(page):
    return {"X-CSRF-Token": page.evaluate("() => window.csrfToken()")}


def _set_vision_provider(live_server, page, value):
    resp = page.request.post(
        f"{live_server['url']}/api/settings/vision",
        form={"vision_provider": value},
        headers=_csrf_headers(page),
    )
    assert resp.status in (200, 303)


@pytest.fixture
def intake_page(live_server, authed_page):
    """authed_page with Ollama selected as the vision provider.

    Ollama needs no API key (`test_nav.py:186` sets it the same way), and
    nothing ever calls it — `/api/intake/analyze` is routed in the browser.

    `/api/intake/plan` is deliberately NOT intercepted for the upload
    round-trip: it runs for real against the local server, and
    `eleven_books.jpg` is 770x1022, below `OLLAMA_DEFAULT_INGEST_LONG_EDGE =
    1024` (`app/config.py`), so the plan comes back `needs_choice: false` and
    the plain "Read Photo" button stays the click target rather than the
    tiling card. 770 is also below `LOW_RES_LONG_EDGE = 2400`, so the plan now
    returns `low_res: true` as well and the advisory card renders — Read Photo
    stays the click target regardless, because the advisory is non-blocking.
    """
    _set_vision_provider(live_server, authed_page, "ollama")
    try:
        yield authed_page
    finally:
        _set_vision_provider(live_server, authed_page, "")


def _analyze(live_server, page):
    """Upload the fixture photo and let the routed analyze call fill the rows."""
    page.route("**/api/intake/analyze",
               lambda route: route.fulfill(json=ANALYZE_RESPONSE))
    page.goto(f"{live_server['url']}/intake")
    page.wait_for_load_state("networkidle")
    page.locator("[data-testid=intake-choose-input]").set_input_files(str(FIXTURE_PHOTO))
    # The real /plan returns low_res for this 770x1022 fixture, so the advisory
    # is on screen — and Read Photo still works. That is the whole contract.
    expect(page.locator("[data-testid=intake-low-res]")).to_be_visible()
    page.locator("button", has_text="Read Photo").click()


def _open_viewfinder(page, live_server):
    """Desktop Chromium reports `'capture' in input` false, so Take photo
    opens the in-page viewfinder rather than a camera app."""
    page.goto(f"{live_server['url']}/intake")
    page.wait_for_load_state("networkidle")
    page.locator("[data-testid=intake-take-photo]").click()


def test_take_photo_opens_viewfinder_and_stream_goes_live(live_server, intake_page):
    page = intake_page
    page.goto(f"{live_server['url']}/intake")
    page.wait_for_load_state("networkidle")

    # Visible at all only because cameraAvailable resolved true.
    take = page.locator("[data-testid=intake-take-photo]")
    expect(take).to_be_visible()
    take.click()

    expect(page.locator("[data-testid=intake-viewfinder]")).to_be_visible()
    # Liveness, polled from Python: page.wait_for_function would be refused by
    # the CSP (G21).
    wait_for_video_ready(page, "#intake-video")

    body = page.locator("body")
    for message in CAMERA_ERRORS:
        expect(body).not_to_contain_text(message)


def test_capture_closes_viewfinder_plans_and_enables_read_photo(live_server, intake_page):
    page = intake_page
    payloads = []

    def _plan(route):
        payloads.append(route.request.post_data_json)
        route.fulfill(json=PLAN_OK)

    page.route("**/api/intake/plan", _plan)
    _open_viewfinder(page, live_server)
    wait_for_video_ready(page, "#intake-video")

    page.locator("[data-testid=intake-capture-btn]").click()

    expect(page.locator("[data-testid=intake-viewfinder]")).to_be_hidden()
    expect(page.locator('img[alt="Shelf photo preview"]')).to_be_visible()
    expect(page.locator("[data-testid=intake-low-res]")).to_have_count(0)
    expect(page.locator("button", has_text="Read Photo")).to_be_enabled()

    # The grabbed frame re-entered the ordinary plan path with real dimensions.
    assert payloads, "no /api/intake/plan request was recorded"
    assert isinstance(payloads[-1]["width"], int) and payloads[-1]["width"] > 0
    assert isinstance(payloads[-1]["height"], int) and payloads[-1]["height"] > 0


def test_low_res_advisory_is_non_blocking(live_server, intake_page):
    """The advisory warns; it never gates. Read Photo still analyzes."""
    page = intake_page
    page.route("**/api/intake/plan", lambda route: route.fulfill(json=PLAN_LOW))
    page.route("**/api/intake/analyze",
               lambda route: route.fulfill(json=ANALYZE_RESPONSE))
    _open_viewfinder(page, live_server)
    wait_for_video_ready(page, "#intake-video")
    page.locator("[data-testid=intake-capture-btn]").click()

    advisory = page.locator("[data-testid=intake-low-res]")
    expect(advisory).to_be_visible()
    expect(advisory).to_contain_text("too small")

    page.locator("button", has_text="Read Photo").click()
    expect(page.locator("[data-testid=intake-row]")).to_have_count(2)


def test_retake_reopens_viewfinder(live_server, intake_page):
    """Retake restarts a stream that grab() stopped, and the second photo's
    verdict replaces the first's rather than accumulating."""
    page = intake_page
    calls = {"n": 0}

    def _plan(route):
        calls["n"] += 1
        route.fulfill(json=PLAN_LOW if calls["n"] == 1 else PLAN_OK)

    page.route("**/api/intake/plan", _plan)
    _open_viewfinder(page, live_server)
    wait_for_video_ready(page, "#intake-video")
    page.locator("[data-testid=intake-capture-btn]").click()
    expect(page.locator("[data-testid=intake-low-res]")).to_be_visible()

    page.locator("[data-testid=intake-retake]").click()
    expect(page.locator("[data-testid=intake-viewfinder]")).to_be_visible()
    # The stream really restarted after grab() stopped it.
    wait_for_video_ready(page, "#intake-video")

    page.locator("[data-testid=intake-capture-btn]").click()
    # Wait for the second grab's plan to actually land before judging the
    # advisory: openViewfinder() clears lowRes on entry, so "count 0" would
    # also hold if the second capture had silently done nothing.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and calls["n"] < 2:
        page.wait_for_timeout(100)
    assert calls["n"] == 2, "the retaken photo was never planned"
    expect(page.locator("[data-testid=intake-low-res]")).to_have_count(0)


def test_read_photo_unavailable_while_viewfinder_open(live_server, intake_page):
    """Framing a new photo must not leave the *previous* file analyzable, and
    must not leave its advisory on screen. Cancel releases the track."""
    page = intake_page
    page.route("**/api/intake/plan", lambda route: route.fulfill(json=PLAN_LOW))
    page.goto(f"{live_server['url']}/intake")
    page.wait_for_load_state("networkidle")
    page.locator("[data-testid=intake-choose-input]").set_input_files(str(FIXTURE_PHOTO))
    expect(page.locator("[data-testid=intake-low-res]")).to_be_visible()

    page.locator("[data-testid=intake-retake]").click()
    expect(page.locator("[data-testid=intake-viewfinder]")).to_be_visible()
    wait_for_video_ready(page, "#intake-video")

    expect(page.locator("[data-testid=intake-low-res]")).to_have_count(0)
    expect(page.locator("button", has_text="Read Photo")).to_be_disabled()

    page.locator("[data-testid=intake-capture-cancel]").click()
    expect(page.locator("[data-testid=intake-viewfinder]")).to_be_hidden()
    assert page.evaluate(
        "document.getElementById('intake-video').srcObject === null"), \
        "cancelling the viewfinder left the stream attached"


def test_repeated_take_photo_uses_one_stream(live_server, intake_page):
    """Two clicks in one task (before Alpine can apply :disabled) must still
    acquire at most one stream, and Cancel must end every track it got."""
    page = intake_page
    page.add_init_script("""
        (() => {
          const orig = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
          window.__gumCalls = 0;
          window.__tracks = [];
          navigator.mediaDevices.getUserMedia = function (c) {
            window.__gumCalls++;
            return orig(c).then(s => {
              s.getTracks().forEach(t => window.__tracks.push(t));
              return s;
            });
          };
        })();
    """)
    page.goto(f"{live_server['url']}/intake")
    page.wait_for_load_state("networkidle")

    # Both clicks dispatch in the same task, so the :disabled binding has not
    # been applied yet — this reaches openViewfinder() twice.
    page.evaluate("""
        () => {
          const b = document.querySelector('[data-testid=intake-take-photo]');
          b.click();
          b.click();
        }
    """)
    expect(page.locator("[data-testid=intake-viewfinder]")).to_be_visible()
    wait_for_video_ready(page, "#intake-video")

    assert page.evaluate("window.__gumCalls") == 1, \
        "a second Take photo click acquired a second camera stream"

    page.locator("[data-testid=intake-capture-cancel]").click()
    expect(page.locator("[data-testid=intake-viewfinder]")).to_be_hidden()
    assert page.evaluate(
        "window.__tracks.every(t => t.readyState === 'ended')"), \
        "a camera track outlived the viewfinder"


def test_latest_photo_plan_wins(live_server, intake_page):
    """A slow plan for photo A must never land on photo B.

    /plan runs for real here; only the *response delivery* for the large photo
    is delayed in the page, so A's verdict arrives after B has been chosen.
    Without the generation guard, A's tiling decision and tile rectangles
    replace B's — and analyze() then crops and sends A's pixels.
    """
    page = intake_page
    page.add_init_script("""
        (() => {
          const orig = window.fetch;
          window.fetch = function (url, opts) {
            const p = orig.apply(this, arguments);
            const u = typeof url === 'string' ? url : (url && url.url) || '';
            if (u.indexOf('/api/intake/plan') !== -1 && opts && opts.body) {
              let body = {};
              try { body = JSON.parse(opts.body); } catch (e) {}
              if (body.width === 3000) {
                return p.then(r => new Promise(res => setTimeout(() => res(r), 2000)));
              }
            }
            return p;
          };
        })();
    """)
    uploads = []

    def _analyze_route(route):
        uploads.append(route.request.post_data_buffer)
        route.fulfill(json=ANALYZE_RESPONSE)

    page.route("**/api/intake/analyze", _analyze_route)

    page.goto(f"{live_server['url']}/intake")
    page.wait_for_load_state("networkidle")

    chooser = page.locator("[data-testid=intake-choose-input]")
    # Photo A: 3000x2000 against the Ollama 1024 cap -> tiling card, no advisory.
    chooser.set_input_files({"name": "big.png", "mimeType": "image/png",
                             "buffer": _png_bytes(3000, 2000)})
    # Photo B, straight after: 770x1022 -> advisory, no tiling card.
    chooser.set_input_files(str(FIXTURE_PHOTO))

    # B's verdict, and it must survive A's late response.
    expect(page.locator("[data-testid=intake-low-res]")).to_be_visible()
    page.wait_for_timeout(3000)
    expect(page.locator("[data-testid=intake-low-res]")).to_be_visible()
    expect(page.locator("body")).not_to_contain_text("This is what the AI will see")

    page.locator("button", has_text="Read Photo").click()
    expect(page.locator("[data-testid=intake-row]")).to_have_count(2)
    assert uploads, "no /api/intake/analyze request was recorded"
    assert b"eleven_books.jpg" in uploads[-1], "the wrong photo was analyzed"
    assert b"big.png" not in uploads[-1], "photo A's bytes were sent for photo B"


def test_denied_camera_leaves_chooser_usable(live_server, intake_page):
    """A refused getUserMedia is a dead end for the camera, not for intake."""
    page = intake_page
    # add_init_script is CDP-injected, not eval'd in the page, so the CSP does
    # not block it (tests/e2e/test_csp.py has the same precedent).
    page.add_init_script("""
        navigator.mediaDevices.getUserMedia = () => Promise.reject(
            Object.assign(new Error('denied'), {name: 'NotAllowedError'}));
    """)
    page.goto(f"{live_server['url']}/intake")
    page.wait_for_load_state("networkidle")

    # getUserMedia still *exists*, so the button is offered.
    take = page.locator("[data-testid=intake-take-photo]")
    expect(take).to_be_visible()
    take.click()

    expect(page.locator("#toast-container")).to_contain_text("Camera access denied")
    expect(page.locator("[data-testid=intake-viewfinder]")).to_be_hidden()
    expect(page.locator("[data-testid=intake-low-res]")).to_have_count(0)

    # The chooser is untouched by the camera failure.
    page.locator("[data-testid=intake-choose-input]").set_input_files(str(FIXTURE_PHOTO))
    expect(page.locator('img[alt="Shelf photo preview"]')).to_be_visible()


def test_play_failure_closes_viewfinder_and_releases_stream(live_server, intake_page):
    """getUserMedia succeeding and play() then failing must not read as
    success: the acquired tracks are stopped and start() rejects."""
    page = intake_page
    page.add_init_script("""
        (() => {
          const orig = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
          window.__tracks = [];
          navigator.mediaDevices.getUserMedia = function (c) {
            return orig(c).then(s => {
              s.getTracks().forEach(t => window.__tracks.push(t));
              return s;
            });
          };
          HTMLMediaElement.prototype.play = function () {
            return Promise.reject(
                Object.assign(new Error('blocked'), {name: 'NotAllowedError'}));
          };
        })();
    """)
    page.goto(f"{live_server['url']}/intake")
    page.wait_for_load_state("networkidle")
    page.locator("[data-testid=intake-take-photo]").click()

    expect(page.locator("#toast-container")).to_contain_text("Camera access denied")
    expect(page.locator("[data-testid=intake-viewfinder]")).to_be_hidden()
    assert page.evaluate(
        "document.getElementById('intake-video').srcObject === null"), \
        "a failed start() left the stream attached to the video element"
    assert page.evaluate("window.__tracks.length") >= 1, \
        "the test did not actually reach getUserMedia"
    assert page.evaluate(
        "window.__tracks.every(t => t.readyState === 'ended')"), \
        "a failed start() left the camera running"

    page.locator("[data-testid=intake-choose-input]").set_input_files(str(FIXTURE_PHOTO))
    expect(page.locator('img[alt="Shelf photo preview"]')).to_be_visible()


def test_review_row_renders_isbn_media_type_and_recognized_marker(live_server, intake_page):
    _analyze(live_server, intake_page)

    rows = intake_page.locator("[data-testid=intake-row]")
    expect(rows).to_have_count(2)
    for i in range(2):
        expect(rows.nth(i).locator("[data-testid=intake-isbn]")).to_be_visible()
        expect(rows.nth(i).locator("[data-testid=intake-media-type]")).to_be_visible()

    # The marker is per-row and sits on the recognized one only.
    expect(intake_page.locator("[data-testid=intake-recognized]:visible")).to_have_count(1)
    expect(rows.nth(0).locator("[data-testid=intake-recognized]")).to_be_hidden()
    recognized_row = rows.nth(1)
    expect(recognized_row.locator("[data-testid=intake-recognized]")).to_be_visible()
    expect(recognized_row.locator("input[placeholder=Title]")).to_have_value(
        "E2E Recognized Disc")


def test_confirm_round_trip_sends_edits_and_persists_them(live_server, intake_page):
    # Row 1 is already in the library, so confirm skips it before any lookup.
    insert_item(live_server["data_dir"], title="E2E Read Book",
                authors="Someone", isbn="9780000009901")

    _analyze(live_server, intake_page)
    rows = intake_page.locator("[data-testid=intake-row]")
    expect(rows).to_have_count(2)

    disc = rows.nth(1)
    disc.locator("[data-testid=intake-media-type]").select_option("dvd")
    # Invalid on purpose: clean_isbn drops it, so no cascade call is made —
    # and it still proves the field's edit reaches the payload.
    disc.locator("[data-testid=intake-isbn]").fill("123")

    sent = {}

    def capture(route):
        sent["payload"] = route.request.post_data_json
        route.continue_()

    intake_page.route("**/api/intake/confirm", capture)
    intake_page.locator("button", has_text="Add 2 to Library").click()

    expect(intake_page.locator("[data-testid=intake-added-row]")).to_have_count(1)

    # The wire shape: exactly four keys per book, and `source` is not one.
    books = sent["payload"]["books"]
    assert len(books) == 2
    for book in books:
        assert set(book) == {"title", "authors", "isbn", "media_type"}
    assert books[1]["media_type"] == "dvd"
    assert books[1]["isbn"] == "123"

    panel = intake_page.locator("text=Done").locator("xpath=..")
    expect(panel).to_contain_text("Added 1")
    expect(panel).to_contain_text("skipped 1")
    expect(panel).to_contain_text("already in library")

    added = intake_page.locator("[data-testid=intake-added-row]")
    expect(added).to_contain_text("E2E Recognized Disc")
    expect(added.locator("[data-testid=intake-no-metadata]")).to_be_visible()

    conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM items WHERE title = 'E2E Recognized Disc'").fetchone()
    finally:
        conn.close()
    assert row["media_type"] == "dvd"
    assert row["isbn"] is None
    assert row["source"] == "photo_intake"
