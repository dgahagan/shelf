"""E2E tests: arriving from another site (#149).

The session cookie is SameSite=Strict, so a link from another *site* arrives
without it. Ports alone do not make a second site, so the link page is served
from 127.0.0.1 while Shelf is addressed as localhost.
"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.e2e.conftest import assert_page_clean, attach_page_guard


pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def link_site(live_server):
    """A page on another host (127.0.0.1) that links into Shelf at localhost."""
    target = f"http://localhost:{live_server['port']}/browse?q=arrival"
    body = (
        '<!doctype html><html><head><link rel="icon" href="data:,"></head>'
        f'<body><a id="go" href="{target}">go</a></body></html>'
    ).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"url": f"http://127.0.0.1:{server.server_address[1]}/"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_cross_site_link_lands_on_page_when_logged_in(
    live_server, link_site, browser, setup_admin
):
    """A link from another site renders the page for a live session."""
    base = f"http://localhost:{live_server['port']}"
    ctx = browser.new_context()
    pg = attach_page_guard(ctx.new_page())
    pg.goto(f"{base}/login")
    pg.fill("input[name=username]", setup_admin["username"])
    pg.fill("input[name=password]", setup_admin["password"])
    pg.click("button[type=submit]")
    pg.wait_for_url(f"{base}/", timeout=10_000)

    responses = []
    pg.on("response", lambda r: responses.append(r))
    pg.goto(link_site["url"])
    pg.click("#go")
    pg.wait_for_url(f"{base}/browse?q=arrival", timeout=10_000)
    assert pg.url == f"{base}/browse?q=arrival"

    # The first leg is the bounce: cookie withheld, answered 200 + Refresh.
    bounces = [
        r for r in responses
        if r.url == f"{base}/browse?q=arrival"
        and r.status == 200
        and "refresh" in r.headers
    ]
    assert bounces, [(r.url, r.status) for r in responses]
    assert_page_clean(pg)
    ctx.close()


def test_cross_site_link_reaches_login_when_logged_out(
    live_server, link_site, browser, setup_admin
):
    """Without a session the same link ends on the login form."""
    base = f"http://localhost:{live_server['port']}"
    ctx = browser.new_context()
    pg = attach_page_guard(ctx.new_page())
    pg.goto(link_site["url"])
    pg.click("#go")
    pg.wait_for_url(f"{base}/login", timeout=10_000)
    assert pg.url == f"{base}/login"
    assert_page_clean(pg)
    ctx.close()
