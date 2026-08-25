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


def _jpeg_dims(buf):
    """(width, height) of the first JPEG in a multipart body, stdlib only.

    Pillow is importable here but undeclared (see `_png_bytes`), so the SOF
    marker is walked by hand. Chromium's `toBlob('image/jpeg')` emits a
    baseline SOF0; C4/C8/CC are DHT/JPG/DAC, not frame headers.
    """
    import struct

    i = buf.find(b"\xff\xd8\xff")
    assert i != -1, "no JPEG SOI marker in the recorded body"
    i += 2
    while i < len(buf) - 1:
        if buf[i] != 0xFF:
            i += 1
            continue
        marker = buf[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seglen = struct.unpack(">H", buf[i + 2:i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", buf[i + 5:i + 9])
            return (w, h)
        i += 2 + seglen
    raise AssertionError("no SOF marker found in the recorded JPEG")


def _jpeg_slice(buf):
    """The first JPEG in a multipart body, SOI through EOI, as bytes."""
    buf = bytes(buf)
    start = buf.find(b"\xff\xd8\xff")
    assert start != -1, "no JPEG SOI marker in the recorded body"
    end = buf.rfind(b"\xff\xd9")
    assert end > start, "no JPEG EOI marker after the SOI"
    return buf[start:end + 2]


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


# --- issue #32: the as-is upload is downscaled to the plan's preview size ----

def _choose_and_plan(live_server, page, name, width, height):
    """Choose a synthesized photo and return the real /plan response for it.

    /plan runs for real against the live server (the `intake_page` fixture's
    contract), so the expected preview dims come from the server, never from a
    constant this test would have to keep in step with `app/config.py`.
    """
    page.goto(f"{live_server['url']}/intake")
    page.wait_for_load_state("networkidle")
    with page.expect_response("**/api/intake/plan") as resp:
        page.locator("[data-testid=intake-choose-input]").set_input_files(
            {"name": name, "mimeType": "image/png",
             "buffer": _png_bytes(width, height)})
    return resp.value.json()


def _centre_pixel_of_recorded_jpeg(page, body):
    """Decode the recorded JPEG back in the page and read its centre pixel.

    `_png_bytes` paints solid 0x80 grey, so a resample of it is still grey —
    which makes this a positive check that the canvas was actually *drawn*
    into. A sized-but-undrawn canvas encodes to black or transparent.
    `img-src blob:` (app/main.py) admits the object URL.
    """
    import base64

    return page.evaluate(
        """async (b64) => {
            const bin = atob(b64);
            const bytes = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
            const url = URL.createObjectURL(new Blob([bytes], {type: 'image/jpeg'}));
            const img = new Image();
            await new Promise((res, rej) => {
                img.onload = res; img.onerror = rej; img.src = url;
            });
            const c = document.createElement('canvas');
            c.width = 1; c.height = 1;
            c.getContext('2d').drawImage(
                img, Math.floor(img.width / 2), Math.floor(img.height / 2), 1, 1, 0, 0, 1, 1);
            URL.revokeObjectURL(url);
            return Array.from(c.getContext('2d').getImageData(0, 0, 1, 1).data);
        }""",
        base64.b64encode(_jpeg_slice(body)).decode(),
    )


def _record_upload_parts(page):
    """Record each `photos` part's name, size and MIME as the page sees it.

    Playwright's `request.post_data_buffer` elides a *file-backed* multipart
    part (`test_latest_photo_plan_wins` only ever asserts on the filename for
    that reason), so "the fixture went up unchanged" is not checkable from the
    recorded body alone. The browser's own FormData is, and it is also the
    more direct statement of the contract. CDP-injected, so the CSP's ban on
    eval does not apply (tests/e2e/test_csp.py has the same precedent).
    """
    page.add_init_script("""
        (() => {
          window.__uploadParts = [];
          const orig = window.fetch;
          window.fetch = function (url, opts) {
            const u = typeof url === 'string' ? url : (url && url.url) || '';
            if (u.indexOf('/api/intake/analyze') !== -1 && opts && opts.body
                && typeof opts.body.getAll === 'function') {
              window.__uploadParts = opts.body.getAll('photos').map(
                p => ({name: p.name, size: p.size, type: p.type}));
            }
            return orig.apply(this, arguments);
          };
        })();
    """)


def test_send_as_is_uploads_the_plan_preview_size(live_server, intake_page):
    """Send as-is must upload the plan's preview pixels, not the original file.

    Before issue #32 the raw 8-12 MB phone still went up untouched and
    Anthropic rejected anything over 8000 px outright.
    """
    page = intake_page
    uploads = []
    page.route("**/api/intake/analyze",
               lambda route: (uploads.append(route.request.post_data_buffer),
                              route.fulfill(json=ANALYZE_RESPONSE)))

    plan = _choose_and_plan(live_server, page, "big.png", 3000, 2000)
    assert plan["needs_choice"] is True, "3000x2000 must reach the tiling card"
    assert plan["preview"]["w"] < 3000, "the plan must actually downscale"
    expect(page.locator("[data-testid=intake-model-preview]")).to_be_visible()

    page.locator("[data-testid=intake-send-as-is]").click()
    expect(page.locator("[data-testid=intake-row]")).to_have_count(2)

    assert uploads, "no /api/intake/analyze request was recorded"
    body = bytes(uploads[-1])
    assert b'filename="photo.jpg"' in body, "the downscaled part was not sent"
    assert b"big.png" not in body, "the original file went up as well"
    assert _jpeg_dims(body) == (plan["preview"]["w"], plan["preview"]["h"])

    r, g, b, a = _centre_pixel_of_recorded_jpeg(page, body)
    assert a == 255
    for channel in (r, g, b):
        assert abs(channel - 128) <= 12, \
            f"uploaded JPEG is not the fixture grey: {(r, g, b, a)}"


def test_preview_canvas_is_the_uploaded_size(live_server, intake_page):
    """The "what the AI will see" canvas is the same resample, same size —
    and it is produced by stepped halving, not one aliasing draw."""
    page = intake_page
    # Record every 5-argument drawImage so the intermediate step is visible.
    # add_init_script is CDP-injected, not eval'd in the page, so the CSP does
    # not block it (tests/e2e/test_csp.py has the same precedent).
    page.add_init_script("""
        (() => {
          window.__draws = [];
          const proto = CanvasRenderingContext2D.prototype;
          const orig = proto.drawImage;
          proto.drawImage = function () {
            if (arguments.length === 5) {
              window.__draws.push([arguments[3], arguments[4]]);
            }
            return orig.apply(this, arguments);
          };
        })();
    """)

    plan = _choose_and_plan(live_server, page, "big.png", 3000, 2000)
    assert plan["needs_choice"] is True

    # G21: Python-side poll, never page.wait_for_function (CSP blocks eval).
    canvas = "() => { const c = document.querySelector('[data-testid=intake-model-preview]'); return c ? {w: c.width, h: c.height} : {w: 0, h: 0}; }"
    deadline = time.time() + 10
    dims = page.evaluate(canvas)
    while dims["w"] == 0 and time.time() < deadline:
        time.sleep(0.1)
        dims = page.evaluate(canvas)
    assert dims == {"w": plan["preview"]["w"], "h": plan["preview"]["h"]}

    pixel = page.evaluate(
        """() => {
            const c = document.querySelector('[data-testid=intake-model-preview]');
            return Array.from(c.getContext('2d').getImageData(
                Math.floor(c.width / 2), Math.floor(c.height / 2), 1, 1).data);
        }"""
    )
    assert pixel[3] == 255
    for channel in pixel[:3]:
        assert abs(channel - 128) <= 12, f"preview canvas was not drawn: {pixel}"

    # 3000 > 2 * 1024, so exactly one halving precedes the final draw.
    draws = page.evaluate("() => window.__draws")
    final = [plan["preview"]["w"], plan["preview"]["h"]]
    assert final in draws, f"no draw at the preview size; saw {draws}"
    assert [1500, 1000] in draws, \
        f"no intermediate halving step — the resampler drew in one pass: {draws}"
    assert draws.index([1500, 1000]) < draws.index(final)


def test_photo_within_cap_uploads_unchanged(live_server, intake_page):
    """A photo already inside the model's ingest size goes up byte-identical.

    The fixture is 770x1022 against the Ollama default cap of 1024 — clear by
    2 px on the long edge. If this test goes red after a cap or fixture
    change, that margin is why.
    """
    page = intake_page
    uploads = []
    page.route("**/api/intake/analyze",
               lambda route: (uploads.append(route.request.post_data_buffer),
                              route.fulfill(json=ANALYZE_RESPONSE)))
    _record_upload_parts(page)

    page.goto(f"{live_server['url']}/intake")
    page.wait_for_load_state("networkidle")
    page.locator("[data-testid=intake-choose-input]").set_input_files(str(FIXTURE_PHOTO))
    expect(page.locator("[data-testid=intake-low-res]")).to_be_visible()
    page.locator("button", has_text="Read Photo").click()
    expect(page.locator("[data-testid=intake-row]")).to_have_count(2)

    assert page.evaluate("() => window.__uploadParts") == [{
        "name": FIXTURE_PHOTO.name,
        "size": FIXTURE_PHOTO.stat().st_size,
        "type": "image/jpeg",
    }], "the fixture was re-encoded rather than sent unchanged"

    assert uploads, "no /api/intake/analyze request was recorded"
    body = bytes(uploads[-1])
    assert b"eleven_books.jpg" in body
    assert b"photo.jpg" not in body
    # A file-backed part is elided from the recorded body; a re-encoded Blob
    # would be inlined, so no inline JPEG is a second read on the same fact.
    assert b"\xff\xd8\xff" not in body


def test_read_photo_downscales_over_cap_below_tiling_threshold(live_server, intake_page):
    """The (1, 1.5) factor band: over the cap, but not enough to offer tiling.

    1400x900 against the 1024 cap gives a factor of about 1.37, under
    `TILING_THRESHOLD`, so there is no tiling card and Read Photo is the click
    target — and it must still downscale.
    """
    page = intake_page
    uploads = []
    page.route("**/api/intake/analyze",
               lambda route: (uploads.append(route.request.post_data_buffer),
                              route.fulfill(json=ANALYZE_RESPONSE)))

    plan = _choose_and_plan(live_server, page, "middle.png", 1400, 900)
    assert plan["needs_choice"] is False, "1400x900 must not reach the tiling card"
    assert plan["preview"]["w"] < 1400, "the plan must still downscale"

    page.locator("button", has_text="Read Photo").click()
    expect(page.locator("[data-testid=intake-row]")).to_have_count(2)

    assert uploads, "no /api/intake/analyze request was recorded"
    body = bytes(uploads[-1])
    assert b'filename="photo.jpg"' in body
    assert b"middle.png" not in body
    assert _jpeg_dims(body) == (plan["preview"]["w"], plan["preview"]["h"])


def test_send_as_is_and_high_res_in_one_tick_fetch_once(live_server, intake_page):
    """Two send buttons clicked in one tick must produce one request.

    The file's ONE bounded timed negative, deliberately: under the mutation
    (no `|| this.analyzing` early return) the second fetch fires only after
    makeTileBlobs()'s toBlob awaits resolve, tens of ms later, and there is no
    positive in-page signal for "a second request would have fired by now".
    """
    page = intake_page
    calls = []
    page.route("**/api/intake/analyze",
               lambda route: (calls.append(1),
                              route.fulfill(json=ANALYZE_RESPONSE)))

    plan = _choose_and_plan(live_server, page, "big.png", 3000, 2000)
    assert plan["needs_choice"] is True
    expect(page.locator("[data-testid=intake-send-as-is]")).to_be_visible()

    page.evaluate("""() => {
        document.querySelector('[data-testid=intake-send-as-is]').click();
        document.querySelector('[data-testid=intake-send-tiled]').click();
    }""")
    expect(page.locator("[data-testid=intake-row]")).to_have_count(2)
    expect(page.locator("text=Reading your photo")).to_be_hidden()

    page.wait_for_timeout(500)
    assert len(calls) == 1, f"analyze() is not single-flight: {len(calls)} requests"


def test_replacing_the_photo_during_analysis_keeps_the_new_photos_rows(
        live_server, intake_page):
    """A stale analysis must never write its rows over the current photo's.

    The chooser buttons are disabled for the duration (G38), so the replace is
    done programmatically through the hidden input — which is exactly why the
    generation guard in analyze() has to exist as well (G39).
    """
    page = intake_page
    # Hold photo A's /analyze response until the test releases it, and count
    # deliveries so the release can be waited on positively rather than slept
    # past (G31: a negative assertion must not be satisfied by the
    # not-yet-happened state).
    page.add_init_script("""
        (() => {
          let n = 0;
          window.__analyzeDelivered = 0;
          const orig = window.fetch;
          window.fetch = function (url, opts) {
            const u = typeof url === 'string' ? url : (url && url.url) || '';
            const p = orig.apply(this, arguments);
            if (u.indexOf('/api/intake/analyze') === -1) return p;
            n += 1;
            if (n === 1) {
              return p.then(r => new Promise(res => {
                window.__releaseA = () => { window.__analyzeDelivered += 1; res(r); };
              }));
            }
            return p.then(r => { window.__analyzeDelivered += 1; return r; });
          };
        })();
    """)

    call = {"n": 0}

    def _analyze_route(route):
        call["n"] += 1
        if call["n"] == 1:
            route.fulfill(json={"ok": True, "books": [
                {"title": "Stale A1", "authors": None, "isbn": None, "source": "read"},
                {"title": "Stale A2", "authors": None, "isbn": None, "source": "read"},
            ]})
        else:
            route.fulfill(json=ANALYZE_RESPONSE)

    page.route("**/api/intake/analyze", _analyze_route)

    page.goto(f"{live_server['url']}/intake")
    page.wait_for_load_state("networkidle")
    chooser = page.locator("[data-testid=intake-choose-input]")
    chooser.set_input_files(str(FIXTURE_PHOTO))
    # 770 < LOW_RES_LONG_EDGE, so the advisory card is up throughout A's
    # analysis (analyze() never clears `lowRes`) and all four chooser buttons
    # are attached; headless Chromium reports getUserMedia, so the x-show on
    # Take photo / Take another photo holds (tests/e2e/conftest.py).
    expect(page.locator("[data-testid=intake-low-res]")).to_be_visible()
    page.locator("button", has_text="Read Photo").click()

    for tid in ("intake-take-photo", "intake-choose-photo",
                "intake-retake", "intake-choose-another"):
        expect(page.locator(f"[data-testid={tid}]")).to_be_disabled()

    # Photo B, through the hidden input — the disabled buttons are bypassed on
    # purpose, which is the case the generation guard defends.
    with page.expect_response("**/api/intake/plan"):
        chooser.set_input_files({"name": "b.png", "mimeType": "image/png",
                                 "buffer": _png_bytes(900, 700)})
    page.locator("button", has_text="Read Photo").click()
    expect(page.locator("[data-testid=intake-row]")).to_have_count(2)
    # Row titles live in input values, not text nodes.
    expect(page.locator("[data-testid=intake-row]").first.locator(
        "input[placeholder=Title]")).to_have_value("E2E Read Book")

    # Now let A's response land, and wait for it positively (G21: a
    # Python-side poll over page.evaluate, never page.wait_for_function).
    page.evaluate("() => window.__releaseA()")
    deadline = time.time() + 10
    while page.evaluate("() => window.__analyzeDelivered") < 2 and time.time() < deadline:
        time.sleep(0.1)
    assert page.evaluate("() => window.__analyzeDelivered") == 2, \
        "photo A's held response never landed — the pin would be vacuous"

    expect(page.locator("[data-testid=intake-row]")).to_have_count(2)
    titles = page.locator("[data-testid=intake-row] input[placeholder=Title]")
    expect(titles.first).to_have_value("E2E Read Book")
    assert "Stale A1" not in page.evaluate(
        "() => Array.from(document.querySelectorAll("
        "'[data-testid=intake-row] input[placeholder=Title]')).map(i => i.value).join('|')")
    expect(page.locator("text=Reading your photo")).to_be_hidden()


# ---------------------------------------------------------------------------
# Issue #33 — the review row's declared layout, its width floors, and the
# clean console. See .devdocs/plan-issue-33-intake-title-width.md.
# ---------------------------------------------------------------------------

# Long, real-shaped titles. The width floors below are only meaningful if the
# title would overflow a cramped box: both of these are 36 characters, longer
# than the 34-character title the issue reported showing 9 characters of.
LONG_ANALYZE_RESPONSE = {
    "ok": True,
    "books": [
        {"title": "The Hitchhiker's Guide to the Galaxy", "authors": "Douglas Adams",
         "isbn": None, "source": "read"},
        {"title": "A Short History of Nearly Everything", "authors": "Bill Bryson",
         "isbn": None, "source": "recognized"},
    ],
}

# What a successful POST /api/intake/confirm returns (app/routers/intake.py:339).
# The round-trip tests *fulfill* rather than continue: `live_server` is
# session-scoped, so letting two parametrized viewport cases write real rows
# would have the second case's dupe check see the first case's inserts.
CONFIRM_OK = {
    "ok": True,
    "added": [{"id": 424242, "title": "Edited Title 33", "matched": False}],
    "skipped": [],
}

# Widest media-type label (app/config.py:15) — the option the mobile select
# must render without clipping.
WIDEST_MEDIA_LABEL = "Comic / Graphic Novel"


def _analyze_long(live_server, page, width):
    """Fill the review rows from the long-title payload at a fixed viewport.

    Mirrors `_analyze` but sets the viewport before navigating and routes its
    own payload, so the geometry assertions measure titles worth measuring.
    """
    page.set_viewport_size({"width": width, "height": 844 if width < 768 else 800})
    page.route("**/api/intake/analyze",
               lambda route: route.fulfill(json=LONG_ANALYZE_RESPONSE))
    page.goto(f"{live_server['url']}/intake")
    page.wait_for_load_state("networkidle")
    page.locator("[data-testid=intake-choose-input]").set_input_files(str(FIXTURE_PHOTO))
    expect(page.locator("[data-testid=intake-low-res]")).to_be_visible()
    page.locator("button", has_text="Read Photo").click()
    expect(page.locator("[data-testid=intake-row]")).to_have_count(2)
    # Counting the rows is not enough to measure them: the review card is
    # `x-show` + `x-cloak`, so the rows are attached (and counted) a tick
    # before the card stops being `display: none` — and every
    # getBoundingClientRect() in that window returns zeros. Wait for the
    # painted state positively (G21: expect(), never page.wait_for_function).
    expect(page.locator("[data-testid=intake-row]").first.locator(
        "input[placeholder=Title]")).to_be_visible()
    expect(page.locator("[data-testid=intake-recognized]:visible")).to_have_count(1)


def _recognized_row(page):
    """The recognized row, found from its visible badge and an *explicit* climb
    to the row container.

    Never the badge's immediate parent: issue #33 wraps checkbox+title+badge in
    a new div, so the parent is that wrapper and holds no Author, ISBN or
    select. Never a positional index either — nothing may couple to child order.
    """
    return page.locator("[data-testid=intake-recognized]:visible").locator(
        "xpath=ancestor::*[@data-testid='intake-row']")


def _plain_row(page):
    """The remaining row: the one whose badge is hidden (`x-show` on
    `book.source === 'recognized'`)."""
    rows = page.locator("[data-testid=intake-row]")
    hidden = [rows.nth(i) for i in range(rows.count())
              if not rows.nth(i).locator("[data-testid=intake-recognized]").is_visible()]
    assert len(hidden) == 1, f"expected exactly one badge-free row, found {len(hidden)}"
    return hidden[0]


def _row_metrics(page):
    """Rendered geometry of every control in every review row, one round trip.

    `badge` is present only when the badge is actually rendered — a hidden
    `x-show` element has no client rects, and a hidden control must not
    participate in the line arithmetic below.
    """
    return page.evaluate(
        """() => {
            const box = el => {
                const r = el.getBoundingClientRect();
                return {top: r.top, bottom: r.bottom, left: r.left,
                        right: r.right, width: r.width};
            };
            return Array.from(
                document.querySelectorAll('[data-testid=intake-row]')
            ).map(row => {
                const badge = row.querySelector('[data-testid=intake-recognized]');
                const visible = !!badge && badge.getClientRects().length > 0;
                const m = {
                    badgeVisible: visible,
                    checkbox: box(row.querySelector('input[type=checkbox]')),
                    title: box(row.querySelector('input[placeholder=Title]')),
                    author: box(row.querySelector('input[placeholder=Author]')),
                    isbn: box(row.querySelector('[data-testid=intake-isbn]')),
                    select: box(row.querySelector('[data-testid=intake-media-type]')),
                    titleValue: row.querySelector('input[placeholder=Title]').value,
                };
                if (visible) m.badge = box(badge);
                return m;
            });
        }"""
    )


def _lines(row, names, tol=4.0):
    """Map each named control to its flex line index, top line first.

    Two controls share a line iff their vertical centres are within `tol` px.
    Counting the *bands* is the point: `author.top >= title.bottom` is equally
    true of a four-line row, so it cannot tell a declared three-line layout
    from an emergent one.
    """
    pairs = sorted(((n, (row[n]["top"] + row[n]["bottom"]) / 2) for n in names),
                   key=lambda kv: kv[1])
    out, idx, anchor = {}, 0, pairs[0][1]
    for name, centre in pairs:
        if centre - anchor > tol:
            idx += 1
            anchor = centre
        out[name] = idx
    return out


def _shapes(page):
    """(plain, recognized) metrics, identified by badge visibility."""
    metrics = _row_metrics(page)
    assert len(metrics) == 2, f"expected 2 review rows, measured {len(metrics)}"
    plain = [m for m in metrics if not m["badgeVisible"]]
    recognized = [m for m in metrics if m["badgeVisible"]]
    assert len(plain) == 1 and len(recognized) == 1, \
        "expected exactly one plain and one recognized row"
    return plain[0], recognized[0]


def test_review_row_mobile_layout_is_three_declared_lines(live_server, intake_page):
    """Below `sm` the six controls land on three *declared* lines, identically
    for both row shapes.

    Issue #33's real defect was that the mobile layout was emergent: whether
    the badge happened to be present decided which controls shared a line, so
    narrowing Author to help plain rows collapsed recognized ones to 78px.
    """
    page = intake_page
    _analyze_long(live_server, page, 390)
    plain, recognized = _shapes(page)

    assert "badge" not in plain, "the plain row's badge must stay hidden"
    lines = _lines(plain, ["checkbox", "title", "author", "isbn", "select"])
    assert max(lines.values()) + 1 == 3, f"plain row is not three lines: {lines}"
    assert lines["checkbox"] == lines["title"] == 0, lines
    assert lines["author"] == lines["isbn"] == 1, lines
    assert lines["select"] == 2, lines

    lines = _lines(recognized,
                   ["checkbox", "title", "badge", "author", "isbn", "select"])
    assert max(lines.values()) + 1 == 3, f"recognized row is not three lines: {lines}"
    assert lines["checkbox"] == lines["title"] == lines["badge"] == 0, \
        f"the recognized marker left its title's line: {lines}"
    assert lines["author"] == lines["isbn"] == 1, lines
    assert lines["select"] == 2, lines


def test_review_row_mobile_title_width_floors(live_server, intake_page):
    """Absolute floors, never a sibling comparison.

    A recognized row necessarily spends ~73px of the same wrapper on the badge,
    so "recognized >= plain" is unsatisfiable by construction. The dropped
    `w-48 -> w-32` branch regressed recognized rows to 78px; these floors
    (predicted ~284 / ~211) catch that by a wide margin. A measured value below
    a floor is a real finding, not a floor to loosen.
    """
    _analyze_long(live_server, intake_page, 390)
    plain, recognized = _shapes(intake_page)

    assert plain["title"]["width"] >= 250, (
        f"plain-row title is {plain['title']['width']:.0f}px at 390px, floor 250px")
    assert recognized["title"]["width"] >= 200, (
        f"recognized-row title is {recognized['title']['width']:.0f}px at 390px, "
        "floor 200px")


def test_review_row_mobile_select_fits_widest_option(live_server, intake_page):
    """The media select owns its own mobile line, so its widest option renders
    without clipping — the failure mode of the rejected two-line layout."""
    page = intake_page
    _analyze_long(live_server, page, 390)
    _plain_row(page).locator("[data-testid=intake-media-type]").select_option("comic")

    fit = page.evaluate(
        """() => {
            const rows = Array.from(
                document.querySelectorAll('[data-testid=intake-row]'));
            const plain = rows.find(r => {
                const b = r.querySelector('[data-testid=intake-recognized]');
                return !b || b.getClientRects().length === 0;
            });
            const s = plain.querySelector('[data-testid=intake-media-type]');
            const cs = getComputedStyle(s);
            const ctx = document.createElement('canvas').getContext('2d');
            ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
            return {
                label: s.options[s.selectedIndex].text,
                textWidth: ctx.measureText(s.options[s.selectedIndex].text).width,
                clientWidth: s.clientWidth,
                boxWidth: s.getBoundingClientRect().width,
            };
        }"""
    )

    assert fit["label"] == WIDEST_MEDIA_LABEL, fit["label"]
    # px-3 padding is 24px; the rest is the native arrow's allowance.
    assert fit["clientWidth"] >= fit["textWidth"] + 28, (
        f"{WIDEST_MEDIA_LABEL!r} needs {fit['textWidth']:.0f}px + 28px chrome "
        f"but the select's content box is {fit['clientWidth']:.0f}px")
    assert fit["boxWidth"] >= 250, (
        f"the select should own its whole mobile line, measured "
        f"{fit['boxWidth']:.0f}px")


def test_review_row_desktop_stays_one_line(live_server, intake_page):
    """At `sm` and up the row is still the single line that shipped before —
    and the recognized title is wider than the 88px it used to get."""
    _analyze_long(live_server, intake_page, 1280)
    plain, recognized = _shapes(intake_page)

    assert "badge" not in plain, "the plain row's badge must stay hidden"
    lines = _lines(plain, ["checkbox", "title", "author", "isbn", "select"])
    assert max(lines.values()) + 1 == 1, f"plain row wrapped on desktop: {lines}"

    lines = _lines(recognized,
                   ["checkbox", "title", "badge", "author", "isbn", "select"])
    assert max(lines.values()) + 1 == 1, f"recognized row wrapped on desktop: {lines}"
    assert recognized["title"]["width"] >= 120, (
        f"recognized-row title is {recognized['title']['width']:.0f}px at 1280px, "
        "floor 120px (it measured 88px on main)")


@pytest.mark.parametrize("width", [680, 768])
def test_review_row_stays_three_lines_up_to_the_lg_seam(live_server, intake_page, width):
    """Below the `lg` seam the row is three declared lines and both titles
    clear their floors.

    680px is inside the band the old `sm` seam used to break; 768px is the old
    `md` seam itself, which is now stacked. The single-line layout needs
    Author 128 + ISBN 128 + select 192 + 24px of gaps = 472px of fixed width.
    At a 640px viewport the row's content box is only ~558px, leaving ~86px for
    checkbox+title+badge; the wrapper is `min-w-0`, so it shrank rather than
    wrapped and the `recognized` title collapsed to 26px — narrower than its own
    padding, rendering zero characters, against 13-19 on `main`.

    768px is here because that seam did not hold across machines: the badge and
    the select are content-derived, so the single-line title measured 104px on
    the dev box and 92px on CI against a 100px floor. Stacked, the title has the
    full row width at both.
    """
    _analyze_long(live_server, intake_page, width)
    plain, recognized = _shapes(intake_page)

    lines = _lines(plain, ["checkbox", "title", "author", "isbn", "select"])
    assert max(lines.values()) + 1 == 3, \
        f"plain row is not three lines at {width}px: {lines}"
    lines = _lines(recognized,
                   ["checkbox", "title", "badge", "author", "isbn", "select"])
    assert max(lines.values()) + 1 == 3, \
        f"recognized row is not three lines at {width}px: {lines}"

    # Both comfortably clear the 390px floors; the regression was 26px.
    assert plain["title"]["width"] >= 250, (
        f"plain-row title is {plain['title']['width']:.0f}px at {width}px, floor 250px")
    assert recognized["title"]["width"] >= 200, (
        f"recognized-row title is {recognized['title']['width']:.0f}px at {width}px, "
        "floor 200px")


def test_review_row_is_one_line_from_the_lg_seam_up(live_server, intake_page):
    """1024px is the first single-line width, and the title is usable there.

    The floor is set for *margin*, not to just clear the local measurement. The
    badge and the select are sized by their content, so the non-title side of
    this row widens under a wider default sans-serif: the old `md` seam measured
    104px here and 92px on the CI runner against a 100px floor, i.e. it fit on
    one machine by 4px and failed on the other by 8px. At `lg` the title
    measures 152px locally, so a 120px floor leaves 32px — about 2.7x the worst
    machine-to-machine drift observed across three environments (dev box,
    playwright:noble container, GitHub runner).

    Note the seam buys less than it looks: the page container caps its width, so
    1024px yields 152px rather than the ~360px a full-bleed row would give.
    """
    _analyze_long(live_server, intake_page, 1024)
    plain, recognized = _shapes(intake_page)

    lines = _lines(plain, ["checkbox", "title", "author", "isbn", "select"])
    assert max(lines.values()) + 1 == 1, f"plain row wrapped at 1024px: {lines}"
    lines = _lines(recognized,
                   ["checkbox", "title", "badge", "author", "isbn", "select"])
    assert max(lines.values()) + 1 == 1, f"recognized row wrapped at 1024px: {lines}"
    assert recognized["title"]["width"] >= 120, (
        f"recognized-row title is {recognized['title']['width']:.0f}px at 1024px; "
        "floor 120px")


@pytest.mark.parametrize("width", [390, 1280])
def test_review_row_edits_round_trip_at_both_viewports(live_server, intake_page, width):
    """Moving the checkbox into a wrapper broke no Alpine handler.

    The handlers are index-based (`toggleInclude(i)`), so no DOM coupling
    exists to break — this is the behavioural proof of that. `include` is
    client-only state: `intake.js:401-404` filters by it and serializes only
    title/authors/isbn/media_type, so an excluded row is *absent* from the
    payload rather than sent with `include: false`.
    """
    page = intake_page
    _analyze_long(live_server, page, width)

    plain, recognized = _plain_row(page), _recognized_row(page)
    plain.locator("input[placeholder=Title]").fill("Edited Title 33")
    plain.locator("input[placeholder=Author]").fill("Edited Author")
    plain.locator("[data-testid=intake-isbn]").fill("123")
    plain.locator("[data-testid=intake-media-type]").select_option("comic")

    recognized.locator("input[type=checkbox]").uncheck()
    # Positive proof the toggle landed before the negative "absent" assertion
    # below — a payload of one book is otherwise satisfied by a page that never
    # registered the click (G31).
    add = page.locator("button", has_text="Add 1 to Library")
    expect(add).to_be_visible()

    sent = {}

    def confirm(route):
        sent["payload"] = route.request.post_data_json
        route.fulfill(json=CONFIRM_OK)

    page.route("**/api/intake/confirm", confirm)
    add.click()
    expect(page.locator("[data-testid=intake-added-row]")).to_have_count(1)

    # G34 n/a: this counts the browser's own request body, not a server-side
    # list that could be capped or sampled.
    books = sent["payload"]["books"]
    assert len(books) == 1, books
    assert set(books[0]) == {"title", "authors", "isbn", "media_type"}, books[0]
    assert books[0]["title"] == "Edited Title 33"
    assert books[0]["authors"] == "Edited Author"
    assert books[0]["isbn"] == "123"
    assert books[0]["media_type"] == "comic"
    assert all(b["title"] != "A Short History of Nearly Everything" for b in books), \
        "the deselected row reached the wire"


def test_intake_page_loads_without_pageerror(live_server, intake_page):
    """`/intake` throws nothing across load -> analyze -> review -> confirm.

    The three `result && result.added.length` guards threw on *every* load: the
    Alpine CSP build parses `&&` as a BinaryExpression and evaluates both
    operands before applying the operator (`case"&&":return f&&_`,
    alpinejs-csp-3.15.9.min.js:7), so `false.added` ran and `undefined.length`
    threw. Three pageerrors per load, masking any real one.
    """
    page = intake_page
    errors = []
    # Attached before the navigation: the throws happen in the first render.
    page.on("pageerror", lambda e: errors.append(str(e)))

    _analyze_long(live_server, page, 1280)
    assert errors == [], f"/intake threw on load: {errors}"

    page.route("**/api/intake/confirm", lambda route: route.fulfill(json=CONFIRM_OK))
    page.locator("button", has_text="Add 2 to Library").click()
    expect(page.locator("[data-testid=intake-added-row]")).to_have_count(1)

    assert errors == [], f"/intake threw during the confirm round trip: {errors}"
