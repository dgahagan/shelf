"""
E2E test fixtures for Shelf.

Uses raw Playwright (not pytest-playwright) so we can control the server
lifecycle and auth state independently.
"""
import contextlib
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

APP_DIR = Path(__file__).parents[2]  # shelf/
ADMIN_USERNAME = "e2eadmin"
ADMIN_PASSWORD = "e2epassword1"
ADMIN_DISPLAY = "E2E Admin"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_SERVER_TIMEOUT = float(os.environ.get("E2E_SERVER_TIMEOUT", "30"))


class _OutputCapture:
    """Drains a subprocess pipe on a background thread into a bounded buffer.

    subprocess.PIPE has a small OS-level buffer (especially on Windows). If
    nobody reads it, the child blocks the moment it fills — which stalls
    uvicorn's own log writes and, since it's single-process, the whole server
    with it. Reading continuously here keeps the pipe drained; the last N
    lines are kept around for diagnostics if startup still fails.
    """

    def __init__(self, stream, max_lines: int = 200):
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._drain, args=(stream, max_lines), daemon=True)
        self._thread.start()

    def _drain(self, stream, max_lines: int) -> None:
        try:
            for raw_line in iter(stream.readline, b""):
                with self._lock:
                    self._lines.append(raw_line.decode(errors="replace").rstrip("\n"))
                    if len(self._lines) > max_lines:
                        del self._lines[0]
        except Exception:
            pass

    def tail(self) -> str:
        with self._lock:
            return "\n".join(self._lines)


def _wait_for_server(url: str, timeout: float = _SERVER_TIMEOUT, output: "_OutputCapture | None" = None) -> None:
    import json
    import urllib.request
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = urllib.request.urlopen(url, timeout=1)
            body = json.loads(resp.read())
            if body.get("status") == "ok":
                return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(
        f"Server at {url} did not start within {timeout}s\n"
        f"Server output:\n{output.tail() if output else '(not captured)'}"
    )


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _boot_server(env_extra: "dict[str, str] | None" = None, *, clear_env=()):
    """Start a uvicorn process against a fresh temp DB; yield its coordinates.

    The body `live_server` used to inline, so there is one implementation
    rather than two. Environment construction order is load-bearing: copy
    `os.environ`, drop every name in `clear_env`, apply the fixed E2E values,
    then apply `env_extra` last — so a caller can always opt back in to
    something `clear_env` removed.
    """
    tmpdir = tempfile.mkdtemp(prefix="shelf_e2e_")
    data_dir = Path(tmpdir) / "data"
    data_dir.mkdir()
    (data_dir / "covers").mkdir()

    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k not in set(clear_env)}
    env.update({
        "DATA_DIR": str(data_dir),
        "SHELF_DISABLE_RATE_LIMIT": "1",
        "SHELF_DEV_INSECURE_COOKIES": "1",
        # Disables the cover-enrichment queue worker and its startup requeue
        # too, so E2E makes no outbound cover fetches. enqueue() still works —
        # jobs simply sit, which is what the cover-poll tests rely on.
        "SHELF_DISABLE_COVER_ENRICH": "1",
    })
    env.update(env_extra or {})

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "app.main:app",
            "--host", "127.0.0.1",
            "--port", str(port),
        ],
        cwd=str(APP_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output = _OutputCapture(proc.stdout)

    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_server(f"{base_url}/health", output=output)
        yield {"url": base_url, "data_dir": data_dir, "port": port}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture(scope="session")
def live_server():
    """Start a uvicorn process with a temp DB; yield the base URL.

    Passes no `clear_env`, which is what preserves the pre-extraction contract
    byte for byte: the whole parent environment, then the fixed E2E values.
    """
    with _boot_server() as server:
        yield server


@pytest.fixture
def server_factory():
    """Boot throwaway servers with caller-supplied env, torn down per test.

    Function-scoped, unlike `live_server`, so a test can drive several
    configurations without imposing any of them on the other 126 E2E tests.
    Call it more than once in a test if you need more than one server.

    Every factory server starts with **no integration overrides**. Copying
    `os.environ` is not an unconfigured baseline: `SECRET_ENV_VARS` values beat
    the DB row, so on a host that exports ABS_URL/ABS_TOKEN a nominally plain
    server renders Audiobookshelf as configured and a configuration-matrix test
    fails for the host's state. A caller opts back in through `env_extra`.

    Iterate `.values()` — SECRET_ENV_VARS is settings-key -> ENV_NAME, and
    `for name in SECRET_ENV_VARS` would yield 'abs_url' and clear nothing, a
    silent no-op. `tests/conftest.py` carries the same trap for the unit suite.

    The import is function-local on purpose: this module drives the app as a
    subprocess and imports nothing from `app` at module level (G14's neighbours
    live here too).
    """
    from app.config import SECRET_ENV_VARS

    with contextlib.ExitStack() as stack:
        def factory(env_extra: "dict[str, str] | None" = None) -> dict:
            return stack.enter_context(
                _boot_server(env_extra, clear_env=SECRET_ENV_VARS.values())
            )
        yield factory


def wait_for_video_ready(page, selector: str, timeout_ms: int = 15_000) -> None:
    """Block until `selector`'s video element reports `readyState >= 2`.

    Polled from Python rather than via `page.wait_for_function`: Playwright
    runs that predicate through `eval()` inside the page, and the app's CSP
    ("script-src \'self\'", no \'unsafe-eval\') refuses it on /store. A bare
    `page.evaluate` expression goes through Runtime.evaluate instead and is
    unaffected.
    """
    expr = (
        f"document.querySelector({selector!r}) && "
        f"document.querySelector({selector!r}).readyState >= 2"
    )
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if page.evaluate(expr):
            return
        page.wait_for_timeout(100)
    raise AssertionError(
        f"{selector} never reached readyState >= 2 within {timeout_ms}ms "
        "- the camera stream did not start"
    )


@pytest.fixture(scope="session")
def playwright_instance():
    with sync_playwright() as pw:
        yield pw


@pytest.fixture(scope="session")
def browser(playwright_instance):
    """Headless Chromium browser, shared across session."""
    b = playwright_instance.chromium.launch(
        headless=True,
        # Grant getUserMedia without a prompt and back it with Chromium's
        # synthetic video device, so the camera-path tests can actually start
        # a stream. Inert for every test that never calls getUserMedia.
        args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
        ],
    )
    yield b
    b.close()


# ---------------------------------------------------------------------------
# Uncaught-page-error guard (issue #34)
# ---------------------------------------------------------------------------
#
# Alpine's CSP build re-throws a failing template expression asynchronously
# (setTimeout), so a broken guard surfaces as an uncaught page error and
# nothing else: no assertion in any test sees it, and the suite stays green
# over a permanently noisy browser. Measured before the fix: 33 such errors
# across 16 tests, every one of them from a single template expression.
#
# Every Page in this suite is guarded. Call attach_page_guard() immediately
# after each ctx.new_page() (before any navigation), and assert_page_clean()
# before the owning context closes. `grep -rn 'new_page(' tests/e2e/` must
# show no unguarded hit. assert_page_clean() settles for the re-throw itself,
# so a call site needs no wait of its own even when the page has just
# navigated.
#
# Alpine also console.warns the failing expression *by name* just before it
# re-throws, so those warnings are collected too and printed with the failure —
# a bare pageerror reports only "Cannot read property of null or undefined"
# and a minified stack, which names nothing.
#
# No opt-out ships and no clearing mechanism is documented. If a future test
# must expect an uncaught error, design an explicit scoped suppression
# contract before adding that test.

_PAGE_ERRORS_ATTR = "_shelf_page_errors"
_ALPINE_WARNINGS_ATTR = "_shelf_alpine_warnings"


def attach_page_guard(pg):
    """Start recording uncaught errors on `pg`; returns `pg`.

    Written to wrap the constructor at the call site:
    `pg = attach_page_guard(ctx.new_page())`.
    """
    errors: list[str] = []
    warnings: list[str] = []

    def _on_console(msg):
        text = msg.text
        if "Alpine Expression Error" in text:
            warnings.append(text)

    pg.on("pageerror", lambda err: errors.append(str(err)))
    pg.on("console", _on_console)
    setattr(pg, _PAGE_ERRORS_ATTR, errors)
    setattr(pg, _ALPINE_WARNINGS_ATTR, warnings)
    return pg


def assert_page_clean(pg):
    """Fail if the page left any uncaught error behind. Safe to call twice."""
    errors = getattr(pg, _PAGE_ERRORS_ATTR, None)
    if errors is None:
        raise AssertionError(
            "assert_page_clean() on an unguarded page — call "
            "attach_page_guard() immediately after ctx.new_page()."
        )
    # Alpine re-throws a failing expression through setTimeout, so a call that
    # follows a bare goto()/wait_for_url() reads an empty list and passes over
    # a page that is throwing. Settling here rather than at the call site is
    # what makes the guard hold for a test whose last act is a navigation.
    pg.wait_for_timeout(250)
    if not errors:
        return
    warnings = getattr(pg, _ALPINE_WARNINGS_ATTR, [])
    detail = "\n".join(f"  - {e}" for e in errors)
    if warnings:
        detail += "\n\nAlpine expression warnings (these name the failing expression):\n"
        detail += "\n".join(f"  - {w}" for w in warnings)
    raise AssertionError(
        f"{len(errors)} uncaught page error(s) on {pg.url}:\n{detail}"
    )


def _run_setup_wizard(browser, base_url: str) -> dict:
    """Run the setup wizard against `base_url`; return the credentials dict.

    The body `setup_admin` used to inline, so a test driving a throwaway
    `server_factory` server can reach it without a session fixture.

    G44: `attach_page_guard(ctx.new_page())` on one line — the lint requires
    both calls on the same line, and this is a Page construction site like any
    other. `assert_page_clean` runs at the end of the body, never in a
    `finally:`, where it would mask the real failure.
    """
    ctx = browser.new_context()
    page = attach_page_guard(ctx.new_page())
    page.goto(f"{base_url}/setup")
    page.fill("input[name=username]", ADMIN_USERNAME)
    page.fill("input[name=display_name]", ADMIN_DISPLAY)
    page.fill("input[name=password]", ADMIN_PASSWORD)
    page.fill("input[name=password_confirm]", ADMIN_PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(f"{base_url}/browse", timeout=10_000)
    assert_page_clean(page)
    ctx.close()
    return {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD, "display_name": ADMIN_DISPLAY}


@pytest.fixture(scope="session")
def setup_admin(live_server, browser):
    """
    Run the setup wizard once per session; return credentials dict.
    Uses a dedicated browser context so cookies don't leak.
    """
    return _run_setup_wizard(browser, live_server["url"])


def _get_auth_cookies(live_server, browser, credentials: dict) -> dict:
    """Log in and return all cookie values as a dict."""
    ctx = browser.new_context()
    page = attach_page_guard(ctx.new_page())
    page.goto(f"{live_server['url']}/login")
    page.fill("input[name=username]", credentials["username"])
    page.fill("input[name=password]", credentials["password"])
    page.click("button[type=submit]")
    page.wait_for_url(f"{live_server['url']}/browse", timeout=10_000)
    cookies = {c["name"]: c["value"] for c in ctx.cookies()}
    assert_page_clean(page)
    ctx.close()
    return cookies


# ---------------------------------------------------------------------------
# Function-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def authed_page(live_server, browser, setup_admin):
    """New page authenticated via real browser login flow.

    Logs in through the UI so the browser picks up all cookies (auth, CSRF,
    etc.) automatically — no manual cookie-setting required.
    """
    ctx = browser.new_context()
    pg = attach_page_guard(ctx.new_page())
    pg.goto(f"{live_server['url']}/login")
    pg.fill("input[name=username]", setup_admin["username"])
    pg.fill("input[name=password]", setup_admin["password"])
    pg.click("button[type=submit]")
    pg.wait_for_url(f"{live_server['url']}/browse", timeout=10_000)
    yield pg
    assert_page_clean(pg)
    ctx.close()


@pytest.fixture
def page(live_server, browser, setup_admin):
    """New unauthenticated page (setup has already run so login page shows)."""
    ctx = browser.new_context()
    pg = attach_page_guard(ctx.new_page())
    yield pg
    assert_page_clean(pg)
    ctx.close()


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------


def insert_reading_log(data_dir: Path, item_id: int, count: int = 1) -> None:
    """Insert `count` completed-read rows for an item in the E2E SQLite DB."""
    db_path = data_dir / "shelf.db"
    conn = sqlite3.connect(str(db_path))
    try:
        for _ in range(count):
            conn.execute(
                "INSERT INTO reading_log (item_id, status, date_started, date_finished) "
                "VALUES (?, 'read', '2026-01-01', '2026-01-15')",
                (item_id,),
            )
        conn.commit()
    finally:
        conn.close()


def insert_item(data_dir: Path, **kwargs) -> int:
    """Insert a test item directly into the E2E SQLite DB; return its id."""
    db_path = data_dir / "shelf.db"
    fields = {
        "title": "Test Book",
        "media_type": "book",
        "source": "test",
        **kwargs,
    }
    cols = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(f"INSERT INTO items ({cols}) VALUES ({placeholders})", list(fields.values()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()
