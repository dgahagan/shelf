"""E2E tests: the component-load guard (static/js/component-load-guard.js).

T2 of the alpine-component-load-failure plan. T1 (commit f736be0) added the
guard itself; this file drives every one of the four contracts it names
against a *real* broken load — never a hand-simulated one:

  - one `console.error` per distinct lost script, however many names it owns;
  - one toast per page, however many scripts or swaps;
  - the message never contains "Alpine Expression Error" (the string the
    E2E page guard below already filters on, see `attach_page_guard`);
  - `typeof window[name]` is reported only for the four page-scoped names
    (browsePage, scanPage, intakePage, coverDrop) — the other 25 are
    anonymous Alpine.data factories and never globals.

Four groups, matching the plan:
  (a) the four page-scoped scripts, each on its own page;
  (b) the former host, components.js — once on a base.html shell (/browse,
      with a toast), once on a fresh, unconfigured /setup (no toast: that
      shell loads neither app.js nor #toast-container, G50);
  (c) the two swap-only components (hcResultCard, manualAddForm), reached
      through a *real* HTMX swap, not injected markup;
  (d) one control: the guard is silent on a healthy page, including after a
      real swap, proving the htmx:afterSwap listener itself is inert when
      nothing is actually lost.

Reaching the two swap-only components without live third-party credentials
-----------------------------------------------------------------------
`hcResultCard` only ever arrives via `#hc-results`' `hx-get
/api/hardcover/search`, gated on `has_hardcover` (`app/routers/pages.py:128`)
— true only once a `hardcover_token` setting exists — and even then
`search_hardcover` (`app/routers/hardcover.py:46`) calls the real Hardcover
GraphQL API, which this offline suite cannot reach deterministically. Rather
than fake the DOM swap (forbidden by the task, and indefensible under G31 —
a stub that authors its own markup asserts nothing about the real template),
these tests set a *fake* token (enough to satisfy `has_hardcover` and render
the search box) and intercept the browser-to-server `/api/hardcover/search`
request itself with `page.route`, fulfilling it with `fragments/
hardcover_search_results.html` rendered by the real Jinja template offline
— the same discipline `tests/e2e/test_scan.py`'s `_render_card`/
`_render_status_card` already use for `fragments/scan_result.html`, and the
same *mechanism* (`page.route` fulfilling a real HTMX request) that
`test_scan.py::test_camera_overlay_reads_the_right_fields_through_a_real_scan_with_a_notice`
already uses for `/api/scan`. The request that swaps the card is real; only
the far side (a live Hardcover account) is substituted.

`manualAddForm` needs no such substitution: `test_scan.py`'s own
`test_manual_add_copy_from_picker` already established a network-independent,
deterministic route to a `not_found` card — `media_type=dvd` with the
12-digit code "999999999999", which fails UPC Item DB's own format
validation (a real HTTP 400, not a network dependency) — reused verbatim
here.

Toast timing
------------
`app.js`'s `showToast` removes its own element after 3000ms
(`setTimeout(function() { el.remove(); }, 3000)`), so a DOM-presence check
run any time after that races a real timer and is not what "at most one
toast across repeated swaps" needs to prove — a page that fires two toasts a
tick apart and later has zero in the DOM would look identical to a page that
never fired one. Instead, `_TOAST_WATCH` (installed via `page.add_init_script`
— immune to the CSP, exactly like `test_csp.py`'s own violation probe)
installs a MutationObserver on `document` before anything else on the page
has run, counting every append of the guard's own toast text specifically
(app.js fires its own unrelated toasts for real scan/intake outcomes),
independent of when the browser later removes it.

Where this suite's own dirt goes
---------------------------------
Every test here deliberately drives a page through a broken-script shape:
losing a script that a live `x-data="name"` root depends on makes Alpine's
CSP build throw an uncaught (async, via setTimeout) error for every
expression in that root's scope that touches the component's state — dozens
per page. That is exactly the failure `assert_page_clean`
(`tests/e2e/conftest.py`) exists to catch, so no test here may use the
shared `page`/`authed_page` fixtures (their teardown calls
`assert_page_clean` unconditionally). Each test builds its own context and
page inline via `attach_page_guard(ctx.new_page())` (G44 — the lint at
`scripts/check_test_conventions.py` requires that exact same-line form),
asserts on the fallout it just caused, then clears both recorder lists
(`_finish`, below) before calling `assert_page_clean` — proving nothing
*unexpected* rode along, not gaming the guard.
"""
import json

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import expect
import pytest

from tests.e2e.conftest import (
    _ALPINE_WARNINGS_ATTR,
    _PAGE_ERRORS_ATTR,
    _REQUEST_FAILURES_ATTR,
    _RESPONSE_ERRORS_ATTR,
    assert_page_clean,
    attach_page_guard,
    insert_item,
)

pytestmark = pytest.mark.e2e

_GUARD_SUBSTRING = "[shelf] component load failure"

# Installed via add_init_script (runs before any page script — including
# Alpine's own deferred one — and survives every later navigation on the
# same Page). Two things this cannot be, both learned by running it:
#
#  - A `requestAnimationFrame` poll for `#toast-container` before attaching
#    the MutationObserver: on a light page (/browse) the guard's own
#    notify() call can beat the *first* animation frame, so the observer
#    attaches after the toast already landed and reports 0. Observing
#    `document` itself (guaranteed to exist the instant this script runs,
#    before the parser has even reached <html>) with `subtree: true`
#    removes the race entirely — recording starts before Alpine, or
#    anything else, has run a single line.
#  - A raw append count on #toast-container: app.js's own scan/intake flows
#    fire their own toasts for real outcomes (e.g. a scan result), so a
#    page that both breaks a script *and* drives one of those actions sees
#    more appends than the guard alone produced. Filtering added nodes by
#    the guard's exact copy (component-load-guard.js's `notify()`) isolates
#    just its own toast.
_GUARD_TOAST_TEXT = "This page did not load fully — reload it."
_TOAST_WATCH = """
window.__guardToastAppends = 0;
new MutationObserver(function (muts) {
    muts.forEach(function (m) {
        if (!m.target || m.target.id !== 'toast-container') return;
        for (var i = 0; i < m.addedNodes.length; i++) {
            if (m.addedNodes[i].textContent === %s) {
                window.__guardToastAppends++;
            }
        }
    });
}).observe(document, { childList: true, subtree: true });
""" % json.dumps(_GUARD_TOAST_TEXT)


def _login(browser, base_url, credentials):
    """New context, logged in via the real UI flow (mirrors conftest's
    `authed_page`/`_run_setup_wizard`, and test_settings_abs_sync_guard's
    own `_login`), plus the toast-append watch installed before first
    navigation.
    """
    ctx = browser.new_context()
    pg = attach_page_guard(ctx.new_page())
    pg.add_init_script(_TOAST_WATCH)
    pg.goto(f"{base_url}/login")
    pg.fill("input[name=username]", credentials["username"])
    pg.fill("input[name=password]", credentials["password"])
    pg.click("button[type=submit]")
    pg.wait_for_url(f"{base_url}/browse", timeout=10_000)
    return ctx, pg


def _settle(pg):
    """Let alpine:initialized/htmx:afterSwap reconciliation, and any Alpine
    expression re-throw (fires via setTimeout — see assert_page_clean),
    finish before reading page state. G21: polled from Python, no
    wait_for_function."""
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(300)


def _guard_messages(pg):
    """Console listener collecting the guard's own diagnostics.

    Distinct from attach_page_guard's collectors: the guard emits
    console.error text containing `_GUARD_SUBSTRING`, which is neither an
    uncaught `pageerror` nor an "Alpine Expression Error" warning, so it is
    otherwise invisible to this suite.
    """
    msgs = []
    pg.on(
        "console",
        lambda msg: msgs.append(msg.text) if _GUARD_SUBSTRING in msg.text else None,
    )
    return msgs


def _toast_appends(pg):
    return pg.evaluate("() => window.__guardToastAppends || 0")


def _only(guard_msgs):
    assert len(guard_msgs) == 1, guard_msgs
    return guard_msgs[0]


def _finish(pg, guard_msgs):
    """Clear this test's own expected dirt, then prove nothing else was left.

    The uncaught page errors and Alpine warnings are the *subject* of the
    assertion just made above each call site (a broken script really did
    leave Alpine throwing) — asserting `page_errors` is non-empty first is
    "the expected error signature" G44 asks for; an empty list here would
    mean the abort route never actually took effect, and clearing+asserting
    clean right after would silently pass over that. Clearing afterwards is
    not gaming `assert_page_clean`: it still proves nothing *unexpected*
    rode along.

    Deliberately does not close `ctx` — a caller with its own cleanup (e.g.
    resetting the Hardcover token) needs the page still open to make it, so
    the context is closed at each call site instead, after that cleanup.
    """
    page_errors = getattr(pg, _PAGE_ERRORS_ATTR)
    assert page_errors, (
        "expected the broken script to leave uncaught Alpine expression "
        "errors behind — got none, so the abort route may not have taken "
        "effect"
    )
    page_errors.clear()
    getattr(pg, _ALPINE_WARNINGS_ATTR).clear()
    guard_msgs.clear()
    assert_page_clean(pg)


def _csrf_headers(pg):
    return {"X-CSRF-Token": pg.evaluate("() => window.csrfToken()")}


def _set_hardcover_token(pg, base_url, token):
    resp = pg.request.post(
        f"{base_url}/api/settings", form={"hardcover_token": token}, headers=_csrf_headers(pg)
    )
    assert resp.status in (200, 303), resp.status


def _clear_hardcover_token(pg, base_url):
    resp = pg.request.post(
        f"{base_url}/api/settings",
        form={"hardcover_token": "", "clear_hardcover_token": "on"},
        headers=_csrf_headers(pg),
    )
    assert resp.status in (200, 303), resp.status


# The card HTML under test is rendered from the **real** template with fake
# *data* — never hand-written here (G31), same discipline as
# tests/e2e/test_scan.py's _render_card/_render_status_card.
_HC_BOOK = {
    "hardcover_book_id": 4242,
    "title": "Guard Probe",
    "authors": "Test Author",
    "cover_url": None,
    "year": None,
    "pages": None,
    "series_name": None,
    "series_position": None,
    "rating": None,
    "description": None,
    "in_shelf": False,
}


def _render_hc_results(**overrides):
    ctx = {"results": [], "query": "", "user": None}
    ctx.update(overrides)
    env = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
    return env.get_template("fragments/hardcover_search_results.html").render(**ctx)


def _type_hc_query(pg, text):
    """Real keystrokes into the Discover search box.

    `hx-trigger="keyup changed delay:400ms"` listens for the native
    `keyup` event specifically — `locator.fill()` does not dispatch one, so
    only `press_sequentially` (real per-character key events) actually
    drives the request. The rapid keyups it fires coalesce into a single
    request 400ms after the last one, carrying the final value.
    """
    box = pg.locator('input[name="q"]')
    box.click()
    box.press_sequentially(text, delay=20)
    pg.wait_for_timeout(600)


# ---------------------------------------------------------------------------
# (a) The four page-scoped scripts
# ---------------------------------------------------------------------------


def test_browse_js_loss_reports_browse_page_with_typeof(live_server, browser, setup_admin):
    base = live_server["url"]
    ctx, pg = _login(browser, base, setup_admin)
    guard_msgs = _guard_messages(pg)

    pg.route("**/static/js/browse.js", lambda route: route.abort())
    pg.goto(f"{base}/browse")
    _settle(pg)

    msg = _only(guard_msgs)
    assert "/static/js/browse.js did not register browsePage" in msg
    assert 'typeof window.browsePage is "undefined"' in msg
    assert "the script did not execute" in msg
    assert "Alpine Expression Error" not in msg
    assert _toast_appends(pg) == 1

    _finish(pg, guard_msgs)
    ctx.close()


def test_scan_js_loss_reports_scan_page_with_typeof(live_server, browser, setup_admin):
    base = live_server["url"]
    ctx, pg = _login(browser, base, setup_admin)
    guard_msgs = _guard_messages(pg)

    pg.route("**/static/js/scan.js", lambda route: route.abort())
    pg.goto(f"{base}/scan")
    _settle(pg)

    msg = _only(guard_msgs)
    assert "/static/js/scan.js did not register scanPage" in msg
    assert 'typeof window.scanPage is "undefined"' in msg
    assert "Alpine Expression Error" not in msg
    assert _toast_appends(pg) == 1

    _finish(pg, guard_msgs)
    ctx.close()


def test_intake_js_loss_reports_intake_page_with_typeof(live_server, browser, setup_admin):
    base = live_server["url"]
    ctx, pg = _login(browser, base, setup_admin)
    guard_msgs = _guard_messages(pg)

    pg.route("**/static/js/intake.js", lambda route: route.abort())
    pg.goto(f"{base}/intake")
    _settle(pg)

    msg = _only(guard_msgs)
    assert "/static/js/intake.js did not register intakePage" in msg
    assert 'typeof window.intakePage is "undefined"' in msg
    assert "Alpine Expression Error" not in msg
    assert _toast_appends(pg) == 1

    _finish(pg, guard_msgs)
    ctx.close()


def test_item_edit_js_loss_reports_cover_drop_with_typeof(live_server, browser, setup_admin):
    base = live_server["url"]
    item_id = insert_item(live_server["data_dir"], title="Guard Probe Item", isbn="9780000006001")
    ctx, pg = _login(browser, base, setup_admin)
    guard_msgs = _guard_messages(pg)

    pg.route("**/static/js/item_edit.js", lambda route: route.abort())
    pg.goto(f"{base}/item/{item_id}/edit")
    _settle(pg)

    msg = _only(guard_msgs)
    assert "/static/js/item_edit.js did not register coverDrop" in msg
    assert 'typeof window.coverDrop is "undefined"' in msg
    assert "Alpine Expression Error" not in msg
    assert _toast_appends(pg) == 1

    _finish(pg, guard_msgs)
    ctx.close()


# ---------------------------------------------------------------------------
# (b) The former host, components.js
# ---------------------------------------------------------------------------


def test_components_js_loss_on_base_shell_reports_and_toasts(live_server, browser, setup_admin):
    """/browse: navMenu and accountModal are both live at alpine:initialized
    (the disclosure menu is CSS-hidden above lg, not removed from the DOM;
    the account modal sits inside `{% if user %}`) — one message naming
    both, in DOM order, plus the base shell's toast."""
    base = live_server["url"]
    ctx, pg = _login(browser, base, setup_admin)
    guard_msgs = _guard_messages(pg)

    pg.route("**/static/js/components.js", lambda route: route.abort())
    pg.goto(f"{base}/browse")
    _settle(pg)

    msg = _only(guard_msgs)
    assert "/static/js/components.js did not register navMenu, accountModal" in msg
    assert "Alpine Expression Error" not in msg

    toast = pg.locator("#toast-container > div").first
    expect(toast).to_be_visible()
    expect(toast).to_contain_text("reload it")
    assert _toast_appends(pg) == 1

    _finish(pg, guard_msgs)
    ctx.close()


def test_components_js_loss_on_unconfigured_setup_reports_without_a_toast(
    browser, server_factory
):
    """/setup, freshly booted and never configured (G50: `server_factory`,
    not a copy of `os.environ` — a host exporting integration env vars must
    not leak into this "unconfigured" baseline). That shell loads neither
    app.js nor #toast-container, so the console message is the whole of
    what it can say — no toast is possible, let alone expected."""
    srv = server_factory()
    base = srv["url"]

    ctx = browser.new_context()
    pg = attach_page_guard(ctx.new_page())
    pg.add_init_script(_TOAST_WATCH)
    guard_msgs = _guard_messages(pg)

    pg.route("**/static/js/components.js", lambda route: route.abort())
    pg.goto(f"{base}/setup")
    _settle(pg)

    msg = _only(guard_msgs)
    assert "/static/js/components.js did not register setupForm" in msg
    assert "Alpine Expression Error" not in msg
    assert pg.locator("#toast-container").count() == 0
    assert _toast_appends(pg) == 0

    _finish(pg, guard_msgs)
    ctx.close()


# ---------------------------------------------------------------------------
# (c) The two swap-only components
# ---------------------------------------------------------------------------


def test_hc_result_card_swap_reports_and_toasts_once(live_server, browser, setup_admin):
    """/discover: hcResultCard has no root at alpine:initialized on any
    page — it arrives only via a real #hc-results swap. Two searches (two
    swaps) must still produce exactly one message and one toast."""
    base = live_server["url"]
    ctx, pg = _login(browser, base, setup_admin)
    guard_msgs = _guard_messages(pg)

    try:
        _set_hardcover_token(pg, base, "e2e-fake-hardcover-token")
        # The far side (a live Hardcover search) is substituted; the request
        # that swaps #hc-results is real — see module docstring.
        pg.route(
            "**/api/hardcover/search*",
            lambda route: route.fulfill(
                content_type="text/html",
                body=_render_hc_results(query="probe", results=[_HC_BOOK]),
            ),
        )
        pg.route("**/static/js/components-item.js", lambda route: route.abort())
        pg.goto(f"{base}/discover")
        pg.wait_for_load_state("networkidle")

        _type_hc_query(pg, "first query")
        expect(pg.locator('#hc-results [x-data="hcResultCard"]')).to_have_count(1)

        _type_hc_query(pg, "second query")
        expect(pg.locator('#hc-results [x-data="hcResultCard"]')).to_have_count(1)

        msg = _only(guard_msgs)
        assert "/static/js/components-item.js did not register hcResultCard" in msg
        assert "Alpine Expression Error" not in msg
        assert _toast_appends(pg) == 1

        _finish(pg, guard_msgs)
    finally:
        _clear_hardcover_token(pg, base)
        ctx.close()


def test_manual_add_form_swap_reports_and_toasts_once(live_server, browser, setup_admin):
    """/scan: manualAddForm, like hcResultCard, has no root at
    alpine:initialized — only a not_found card carries it. Two scans of the
    same offline-deterministic non-match (media_type=dvd, "999999999999" —
    see test_scan.py::test_manual_add_copy_from_picker) swap two cards in
    (hx-swap="afterbegin"); still exactly one message and one toast."""
    base = live_server["url"]
    ctx, pg = _login(browser, base, setup_admin)
    guard_msgs = _guard_messages(pg)

    pg.route("**/static/js/components-item.js", lambda route: route.abort())
    pg.goto(f"{base}/scan")
    pg.wait_for_load_state("networkidle")

    pg.select_option("#media-type", "dvd")
    pg.fill("#isbn-input", "999999999999")
    pg.press("#isbn-input", "Enter")
    expect(pg.locator(".scan-result")).to_have_count(1, timeout=20_000)
    expect(pg.locator(".scan-result").first).to_contain_text("not found")

    pg.fill("#isbn-input", "999999999999")
    pg.press("#isbn-input", "Enter")
    expect(pg.locator(".scan-result")).to_have_count(2, timeout=20_000)

    msg = _only(guard_msgs)
    assert "/static/js/components-item.js did not register manualAddForm" in msg
    assert "Alpine Expression Error" not in msg
    assert _toast_appends(pg) == 1

    _finish(pg, guard_msgs)
    ctx.close()


# ---------------------------------------------------------------------------
# (d) Control: silent on a healthy page, including after a real swap
# ---------------------------------------------------------------------------


def _clear_guard_lists(pg):
    """Clear all four recorder lists conftest's page guard stashes on `pg`.

    Distinct from `_finish`: these two tests call `assert_page_clean`
    directly (to inspect the AssertionError it raises), rather than through
    the guard's own console-message channel, so they need to clear the two
    new T4 recorders too — otherwise noise from setup (a login redirect,
    an incidental non-2xx response) would leak into the diagnostics-block
    assertions below.
    """
    getattr(pg, _PAGE_ERRORS_ATTR).clear()
    getattr(pg, _ALPINE_WARNINGS_ATTR).clear()
    getattr(pg, _REQUEST_FAILURES_ATTR).clear()
    getattr(pg, _RESPONSE_ERRORS_ATTR).clear()


# ---------------------------------------------------------------------------
# (e) T4: assert_page_clean()'s own diagnostics block
# ---------------------------------------------------------------------------


def test_assert_page_clean_names_the_lost_script_and_its_request_outcome(
    live_server, browser, setup_admin
):
    """A lost script's assertion message names the script and its request
    outcome — the diagnostics block conftest.assert_page_clean() appends only
    on the path where it is about to raise, built from a real aborted
    request (never a hand-simulated one) exactly like the rest of this
    file."""
    base = live_server["url"]
    ctx, pg = _login(browser, base, setup_admin)

    pg.route("**/static/js/browse.js", lambda route: route.abort())
    pg.goto(f"{base}/browse")
    _settle(pg)

    with pytest.raises(AssertionError) as exc_info:
        assert_page_clean(pg)
    message = str(exc_info.value)
    assert "static/js/browse.js" in message
    assert "ERR_FAILED" in message or "failed" in message.lower()

    _clear_guard_lists(pg)
    assert_page_clean(pg)
    ctx.close()


def test_assert_page_clean_unrelated_error_has_no_diagnostics_block(
    live_server, browser, setup_admin
):
    """G31 pin for the "only when it has something to say" criterion: an
    ordinary uncaught error with no lost script, no failed request, and no
    bad response must leave assert_page_clean()'s message byte-identical to
    before this block existed. An earlier revision of this plan probed all
    29 declared component names on `window` unconditionally, which made the
    block's "something to say" test true on *every* healthy page — this test
    is what would have caught that."""
    base = live_server["url"]
    ctx, pg = _login(browser, base, setup_admin)
    _settle(pg)

    # Drop the login flow's own dirt (its 303 redirect, e.g.) so this test's
    # baseline really is "nothing to say" rather than passing by accident.
    _clear_guard_lists(pg)

    pg.evaluate(
        "() => { setTimeout(function () { throw new Error('unrelated boom'); }, 0); }"
    )
    pg.wait_for_timeout(500)

    # G31: a negative assertion over an empty list can pass because nothing
    # happened yet, not because the thing under test is absent. Prove the
    # scheduled throw actually landed before asserting on its shape.
    assert getattr(pg, _PAGE_ERRORS_ATTR), (
        "expected the scheduled throw to leave an uncaught page error "
        "behind — got none, so this pin would pass on the not-yet-happened "
        "state rather than the thing it claims to test"
    )

    with pytest.raises(AssertionError) as exc_info:
        assert_page_clean(pg)
    message = str(exc_info.value)
    assert "unrelated boom" in message
    for marker in (
        "Diagnostics:",
        "Failed requests:",
        "Non-2xx/304 responses:",
        "did not register",
        "component load guard globals are absent",
        "document.scripts:",
        ".js resource timing:",
        "diagnostics unavailable:",
    ):
        assert marker not in message, message

    _clear_guard_lists(pg)
    assert_page_clean(pg)
    ctx.close()


def test_guard_is_silent_on_a_healthy_page_including_after_a_real_swap(
    live_server, browser, setup_admin
):
    """No script aborted anywhere. Visits every page group (a) covers, then
    proves the htmx:afterSwap listener itself is inert — not merely that
    initial paint was clean — by driving a real #hc-results swap through
    the same interception technique group (c) uses, minus the abort."""
    base = live_server["url"]
    item_id = insert_item(live_server["data_dir"], title="Guard Control Item", isbn="9780000006018")
    ctx, pg = _login(browser, base, setup_admin)
    guard_msgs = _guard_messages(pg)

    for path in ("/browse", "/scan", "/intake", f"/item/{item_id}/edit"):
        pg.goto(f"{base}{path}")
        _settle(pg)

    try:
        _set_hardcover_token(pg, base, "e2e-fake-hardcover-token")
        pg.route(
            "**/api/hardcover/search*",
            lambda route: route.fulfill(
                content_type="text/html",
                body=_render_hc_results(query="probe", results=[_HC_BOOK]),
            ),
        )
        pg.goto(f"{base}/discover")
        pg.wait_for_load_state("networkidle")
        _type_hc_query(pg, "probe")
        expect(pg.locator('#hc-results [x-data="hcResultCard"]')).to_have_count(1)

        assert guard_msgs == []
        assert _toast_appends(pg) == 0
        assert pg.locator("#toast-container > div").count() == 0
        assert_page_clean(pg)
    finally:
        _clear_hardcover_token(pg, base)
        ctx.close()


def test_assert_page_clean_ignores_a_non_js_404_from_an_unrelated_failure(
    live_server, browser, setup_admin
):
    """The diagnostics block triggers on a lost SCRIPT, not on any non-2xx at
    all. A page that took a 404 cover and then failed for an unrelated reason
    must get the byte-identical message it got before the block existed.

    Deliberately does NOT clear the recorders before asserting — the negative
    pin above does, which is exactly why it cannot catch an over-broad
    trigger. Filed as a major on this branch's diff review."""
    base = live_server["url"]
    ctx, pg = _login(browser, base, setup_admin)
    _settle(pg)
    _clear_guard_lists(pg)

    # A real 404 for a non-script resource, the way a missing cover arrives.
    pg.goto(f"{base}/static/covers/does-not-exist.jpg")
    pg.goto(f"{base}/browse")
    _settle(pg)

    # G31: prove the 404 is actually on the recorder, or this pin passes on
    # the not-yet-happened state rather than on the narrowed trigger.
    recorded = getattr(pg, _RESPONSE_ERRORS_ATTR)
    assert any("does-not-exist.jpg" in line for _, line in recorded), recorded

    pg.evaluate(
        "() => { setTimeout(function () { throw new Error('unrelated boom'); }, 0); }"
    )
    pg.wait_for_timeout(500)

    with pytest.raises(AssertionError) as exc_info:
        assert_page_clean(pg)
    message = str(exc_info.value)
    assert "unrelated boom" in message
    for marker in (
        "Diagnostics:",
        "Failed requests:",
        "Non-2xx/304 responses:",
        "does-not-exist.jpg",
        "document.scripts:",
        ".js resource timing:",
    ):
        assert marker not in message, message

    _clear_guard_lists(pg)
    assert_page_clean(pg)
    ctx.close()
