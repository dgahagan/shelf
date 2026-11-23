"""E2E: the htmx control that started a request shows it is busy (#118).

The listener lives in static/js/app.js. Every test here holds the response
with a `page.route` handler that parks the Route and returns, asserts the
in-flight state, then releases the Route itself.
"""
import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item

pytestmark = pytest.mark.e2e

_COVER_FRAGMENT = '<p data-testid="busy-cover-stub">no candidates</p>'


def _hold(pg, pattern):
    """Park every matching request; return (held routes, request post bodies)."""
    held, bodies = [], []

    def handler(route):
        bodies.append(route.request.post_data or "")
        held.append(route)

    pg.route(pattern, handler)
    return held, bodies


def _wait_held(pg, held, n=1):
    for _ in range(100):
        if len(held) >= n:
            return
        pg.wait_for_timeout(50)
    raise AssertionError(f"expected {n} held request(s), saw {len(held)}")


def _open_scan_add(pg, live_server):
    pg.goto(f"{live_server['url']}/scan")
    pg.wait_for_load_state("networkidle")


def _scan_default_button(pg):
    return pg.locator("form[hx-post='/api/scan'] > button.sr-only[type=submit]")


@pytest.mark.parametrize("outcome", ["ok", "server_error", "network_error"])
def test_a_clicked_button_is_busy_until_its_request_ends(live_server, authed_page, outcome):
    item_id = insert_item(live_server["data_dir"], title=f"Busy Cover {outcome}")
    held, _ = _hold(authed_page, f"**/api/items/{item_id}/cover-search*")
    authed_page.goto(f"{live_server['url']}/item/{item_id}")
    btn = authed_page.get_by_role("button", name="Find cover")
    btn.click()
    _wait_held(authed_page, held)

    expect(btn).to_be_disabled()
    expect(btn).to_have_attribute("aria-busy", "true")
    expect(btn).to_have_attribute("data-busy-owned", "")
    spinner = btn.evaluate("b => getComputedStyle(b, '::after').content")
    assert spinner not in ("none", "normal"), spinner

    if outcome == "ok":
        held[0].fulfill(status=200, content_type="text/html", body=_COVER_FRAGMENT)
        expect(authed_page.get_by_test_id("busy-cover-stub")).to_be_visible()
    elif outcome == "server_error":
        with authed_page.expect_response(lambda r: "/cover-search" in r.url):
            held[0].fulfill(status=500, content_type="text/html", body="boom")
    else:
        with authed_page.expect_event("requestfailed"):
            held[0].abort()

    expect(btn).to_be_enabled()
    expect(btn).not_to_have_attribute("aria-busy", "true")
    expect(btn).not_to_have_attribute("data-busy-owned", "")


def test_a_double_click_issues_one_request(live_server, authed_page):
    item_id = insert_item(live_server["data_dir"], title="Busy Double Click")
    held, _ = _hold(authed_page, f"**/api/items/{item_id}/cover-search*")
    authed_page.goto(f"{live_server['url']}/item/{item_id}")
    btn = authed_page.get_by_role("button", name="Find cover")
    btn.dblclick()
    _wait_held(authed_page, held)
    held[0].fulfill(status=200, content_type="text/html", body=_COVER_FRAGMENT)
    expect(authed_page.get_by_test_id("busy-cover-stub")).to_be_visible()
    authed_page.wait_for_timeout(500)
    for r in held[1:]:
        r.fulfill(status=200, content_type="text/html", body=_COVER_FRAGMENT)
    assert len(held) == 1


def test_a_typed_scan_keeps_focus_and_scans_twice(live_server, authed_page):
    held, bodies = _hold(authed_page, "**/api/scan")
    _open_scan_add(authed_page, live_server)
    default_btn = _scan_default_button(authed_page)

    authed_page.fill("#isbn-input", "9780000118001")
    authed_page.press("#isbn-input", "Enter")
    _wait_held(authed_page, held, 1)
    expect(default_btn).to_be_disabled()
    assert authed_page.evaluate("document.activeElement.id") == "isbn-input"
    held[0].fulfill(status=200, content_type="text/html",
                    body='<div class="scan-result" data-testid="busy-scan-1">one</div>')
    expect(authed_page.get_by_test_id("busy-scan-1")).to_be_visible()
    expect(default_btn).to_be_enabled()

    authed_page.fill("#isbn-input", "9780000118002")
    authed_page.press("#isbn-input", "Enter")
    _wait_held(authed_page, held, 2)
    held[1].fulfill(status=200, content_type="text/html",
                    body='<div class="scan-result" data-testid="busy-scan-2">two</div>')
    expect(authed_page.get_by_test_id("busy-scan-2")).to_be_visible()
    assert "9780000118002" in bodies[1]
    assert authed_page.evaluate("document.activeElement.id") == "isbn-input"


def test_a_cancelled_scan_request_marks_nothing(live_server, authed_page):
    """scan.js cancels beforeRequest in Lend mode with no borrower; htmx then
    fires no afterRequest, so marking the button would strand it disabled."""
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")
    lend = authed_page.get_by_role("button", name="Lend", exact=True)
    with authed_page.expect_response(lambda r: "/api/recent-scans" in r.url and r.ok):
        lend.click()
    authed_page.fill("#isbn-input", "9780000118003")
    authed_page.press("#isbn-input", "Enter")
    expect(authed_page.locator("#toast-container > div").first).to_contain_text("Select a borrower first")
    default_btn = _scan_default_button(authed_page)
    expect(default_btn).to_be_enabled()
    expect(default_btn).not_to_have_attribute("aria-busy", "true")
    bar_opacity = authed_page.evaluate("document.getElementById('htmx-indicator').style.opacity")
    assert bar_opacity in ("", "0"), bar_opacity


def test_a_search_as_you_type_input_is_busy_but_never_disabled(live_server, authed_page):
    authed_page.add_init_script(
        "window.__cspViolations = [];"
        "document.addEventListener('securitypolicyviolation', function(e) {"
        " window.__cspViolations.push(e.violatedDirective + ' <- ' + (e.blockedURI || 'inline')); });"
    )
    held, _ = _hold(authed_page, "**/api/title-search*")
    _open_scan_add(authed_page, live_server)
    box = authed_page.locator("#title-search-input")
    box.press_sequentially("dune")
    _wait_held(authed_page, held)

    expect(box).to_have_attribute("aria-busy", "true")
    expect(box).to_be_enabled()
    bg = box.evaluate("el => getComputedStyle(el).backgroundImage")
    assert "data:image/svg+xml" in bg, bg
    box.press("x")
    expect(box).to_have_value("dunex")

    for r in list(held):
        r.fulfill(status=200, content_type="text/html", body="<p>results</p>")
    _wait_held(authed_page, held, 2)
    for r in held[1:]:
        r.fulfill(status=200, content_type="text/html", body="<p>results</p>")
    expect(box).not_to_have_attribute("aria-busy", "true")
    assert authed_page.evaluate("window.__cspViolations") == []


def test_a_control_disabled_by_someone_else_stays_disabled(live_server, authed_page):
    held, _ = _hold(authed_page, "**/api/scan")
    _open_scan_add(authed_page, live_server)
    default_btn = _scan_default_button(authed_page)
    authed_page.fill("#isbn-input", "9780000118004")
    authed_page.evaluate(
        "() => { const f = document.querySelector(\"form[hx-post='/api/scan']\");"
        " f.querySelector('button.sr-only').disabled = true; f.requestSubmit(); }"
    )
    _wait_held(authed_page, held)
    expect(default_btn).to_have_attribute("aria-busy", "true")
    expect(default_btn).not_to_have_attribute("data-busy-owned", "")
    held[0].fulfill(status=200, content_type="text/html", body='<div class="scan-result">x</div>')
    expect(default_btn).not_to_have_attribute("aria-busy", "true")
    expect(default_btn).to_be_disabled()


def test_a_submitters_name_and_value_still_reach_the_server(live_server, authed_page):
    held, bodies = _hold(authed_page, "**/api/scan")
    _open_scan_add(authed_page, live_server)
    authed_page.fill("#isbn-input", "9780000118005")
    authed_page.evaluate(
        "() => { const f = document.querySelector(\"form[hx-post='/api/scan']\");"
        " const b = document.createElement('button'); b.type = 'submit';"
        " b.name = 'busy_probe'; b.value = '1'; b.id = 'busy-probe'; b.textContent = 'probe';"
        " f.appendChild(b); }"
    )
    authed_page.click("#busy-probe")
    _wait_held(authed_page, held)
    expect(authed_page.locator("#busy-probe")).to_be_disabled()
    assert "busy_probe" in bodies[0], bodies[0]
    held[0].fulfill(status=200, content_type="text/html", body='<div class="scan-result">x</div>')
    expect(authed_page.locator("#busy-probe")).to_be_enabled()


def test_a_control_that_swaps_itself_out_arrives_unbusy(live_server, authed_page):
    item_id = insert_item(live_server["data_dir"], title="Busy Reading Status")
    authed_page.goto(f"{live_server['url']}/item/{item_id}")
    section = authed_page.locator("#reading-status-section")
    with authed_page.expect_response(lambda r: "/reading-status" in r.url and r.ok):
        section.get_by_role("button", name="Reading", exact=True).click()
    buttons = authed_page.locator("#reading-status-section button")
    expect(buttons.first).to_be_visible()
    for i in range(buttons.count()):
        expect(buttons.nth(i)).to_be_enabled()
        expect(buttons.nth(i)).not_to_have_attribute("aria-busy", "true")


def test_an_hx_disabled_elt_button_is_guarded_alongside_htmx(live_server, authed_page):
    """item_edit's Use URL carries hx-disabled-elt="this, #edit-cover-url";
    htmx 2.0.4 disables only the input there, so the listener guards the
    button, and the two mechanisms release cleanly together."""
    item_id = insert_item(live_server["data_dir"], title="Busy Cover URL")
    held, _ = _hold(authed_page, f"**/api/items/{item_id}/cover-url")
    authed_page.goto(f"{live_server['url']}/item/{item_id}/edit")
    url_input = authed_page.locator("#edit-cover-url")
    url_input.fill("https://example.com/c.jpg")
    btn = authed_page.get_by_test_id("edit-cover-url-submit")
    btn.click()
    _wait_held(authed_page, held)
    expect(btn).to_be_disabled()
    expect(btn).to_have_attribute("aria-busy", "true")
    expect(url_input).to_be_disabled()
    held[0].fulfill(status=204, body="")
    expect(btn).to_be_enabled()
    expect(btn).not_to_have_attribute("aria-busy", "true")
    expect(url_input).to_be_enabled()


def test_the_visible_scan_button_is_busy_and_cannot_double_submit(live_server, authed_page):
    held, _ = _hold(authed_page, "**/api/scan")
    _open_scan_add(authed_page, live_server)
    btn = authed_page.get_by_test_id("scan-submit")

    authed_page.fill("#isbn-input", "9780000194039")
    btn.click()
    _wait_held(authed_page, held, 1)
    expect(btn).to_be_disabled()
    expect(btn).to_have_attribute("aria-busy", "true")
    btn.click(force=True)
    authed_page.wait_for_timeout(300)
    assert len(held) == 1
    held[0].fulfill(status=200, content_type="text/html",
                    body='<div class="scan-result" data-testid="busy-visible-scan">done</div>')
    expect(authed_page.get_by_test_id("busy-visible-scan")).to_be_visible()
    expect(btn).to_be_enabled()
