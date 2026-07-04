"""E2E: the hardened CSP (no unsafe-inline, no CDN hosts) must not break pages.

Collects securitypolicyviolation events across key pages and asserts none fire,
and that the JS stack (htmx, Alpine, app helpers) actually booted — catching
both "policy too loose" regressions (inline script sneaks in) and "policy too
strict" ones (a required asset gets blocked and the page silently degrades).
"""
import pytest

from tests.e2e.conftest import insert_item

pytestmark = pytest.mark.e2e

_VIOLATION_PROBE = """
window.__cspViolations = [];
document.addEventListener('securitypolicyviolation', function(e) {
    window.__cspViolations.push(
        e.violatedDirective + ' <- ' + (e.blockedURI || e.sourceFile || 'inline')
    );
});
"""


def test_no_csp_violations_on_key_pages(live_server, browser, setup_admin):
    item_id = insert_item(live_server["data_dir"], title="CSP Probe Book", isbn="9780000000301")

    ctx = browser.new_context()
    page = ctx.new_page()
    page.add_init_script(_VIOLATION_PROBE)

    violations = {}

    # Login page first (standalone template, pre-auth)
    page.goto(f"{live_server['url']}/login")
    page.wait_for_load_state("networkidle")
    violations["/login"] = page.evaluate("window.__cspViolations")

    page.fill("input[name=username]", setup_admin["username"])
    page.fill("input[name=password]", setup_admin["password"])
    page.click("button[type=submit]")
    page.wait_for_url(f"{live_server['url']}/browse", timeout=10_000)

    for path in ["/browse", "/scan", "/settings", f"/item/{item_id}"]:
        page.goto(f"{live_server['url']}{path}")
        page.wait_for_load_state("networkidle")
        violations[path] = page.evaluate("window.__cspViolations")

    ctx.close()
    flat = {k: v for k, v in violations.items() if v}
    assert not flat, f"CSP violations: {flat}"


def test_js_stack_boots_under_csp(live_server, browser, setup_admin):
    """htmx, Alpine, and the app helpers must all be live — not silently blocked."""
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(f"{live_server['url']}/login")
    page.fill("input[name=username]", setup_admin["username"])
    page.fill("input[name=password]", setup_admin["password"])
    page.click("button[type=submit]")
    page.wait_for_url(f"{live_server['url']}/browse", timeout=10_000)

    assert page.evaluate("typeof window.htmx") == "object"
    assert page.evaluate("typeof window.Alpine") == "object"
    assert page.evaluate("typeof window.csrfToken") == "function"
    assert page.evaluate("typeof window.showToast") == "function"
    assert page.evaluate("typeof window.browsePage") == "function"
    ctx.close()
