"""
E2E test fixtures for Shelf.

Uses raw Playwright (not pytest-playwright) so we can control the server
lifecycle and auth state independently.
"""
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

APP_DIR = Path(__file__).parents[3]  # shelf/
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


def _wait_for_server(url: str, timeout: float = 15.0) -> None:
    import urllib.request
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"Server at {url} did not start within {timeout}s")


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def live_server():
    """Start a uvicorn process with a temp DB; yield the base URL."""
    tmpdir = tempfile.mkdtemp(prefix="shelf_e2e_")
    data_dir = Path(tmpdir) / "data"
    data_dir.mkdir()
    (data_dir / "covers").mkdir()

    port = _free_port()
    env = {
        **os.environ,
        "DATA_DIR": str(data_dir),
        "SHELF_DISABLE_RATE_LIMIT": "1",
    }

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
        stderr=subprocess.PIPE,
    )

    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_server(f"{base_url}/health")
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
def playwright_instance():
    with sync_playwright() as pw:
        yield pw


@pytest.fixture(scope="session")
def browser(playwright_instance):
    """Headless Chromium browser, shared across session."""
    b = playwright_instance.chromium.launch(headless=True)
    yield b
    b.close()


@pytest.fixture(scope="session")
def setup_admin(live_server, browser):
    """
    Run the setup wizard once per session; return credentials dict.
    Uses a dedicated browser context so cookies don't leak.
    """
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(f"{live_server['url']}/setup")
    page.fill("input[name=username]", ADMIN_USERNAME)
    page.fill("input[name=display_name]", ADMIN_DISPLAY)
    page.fill("input[name=password]", ADMIN_PASSWORD)
    page.fill("input[name=password_confirm]", ADMIN_PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(f"{live_server['url']}/browse", timeout=10_000)
    ctx.close()
    return {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD, "display_name": ADMIN_DISPLAY}


def _get_auth_cookie(live_server, browser, credentials: dict) -> str:
    """Log in and return the access_token cookie value."""
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(f"{live_server['url']}/login")
    page.fill("input[name=username]", credentials["username"])
    page.fill("input[name=password]", credentials["password"])
    page.click("button[type=submit]")
    page.wait_for_url(f"{live_server['url']}/browse", timeout=10_000)
    cookies = ctx.cookies()
    token = next(c["value"] for c in cookies if c["name"] == "access_token")
    ctx.close()
    return token


# ---------------------------------------------------------------------------
# Function-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def authed_page(live_server, browser, setup_admin):
    """New page pre-authenticated as admin."""
    token = _get_auth_cookie(live_server, browser, setup_admin)
    ctx = browser.new_context()
    ctx.add_cookies([{
        "name": "access_token",
        "value": token,
        "domain": "127.0.0.1",
        "path": "/",
    }])
    pg = ctx.new_page()
    yield pg
    ctx.close()


@pytest.fixture
def page(live_server, browser, setup_admin):
    """New unauthenticated page (setup has already run so login page shows)."""
    ctx = browser.new_context()
    pg = ctx.new_page()
    yield pg
    ctx.close()


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------


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
