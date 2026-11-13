"""E2E: the shortcut-help controls work in a translated UI.

The controls used to be found by their English `title` text, so a translated
page lost them. They are bound by id now; this proves it under two locales,
chosen through Accept-Language (the browser context's locale), with no CSP
violation.
"""
import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect

from tests.e2e.conftest import assert_page_clean, attach_page_guard

pytestmark = pytest.mark.e2e

_VIOLATION_PROBE = """
window.__cspViolations = [];
document.addEventListener('securitypolicyviolation', function(e) {
    window.__cspViolations.push(
        e.violatedDirective + ' <- ' + (e.blockedURI || e.sourceFile || 'inline')
    );
});
"""


@pytest.mark.parametrize("locale,html_lang", [("it-IT", "it"), ("zh-CN", "zh-Hans")])
def test_shortcut_help_works_in_translated_ui(
    live_server, browser, setup_admin, locale, html_lang
):
    ctx = browser.new_context(locale=locale)
    try:
        page = attach_page_guard(ctx.new_page())
        page.add_init_script(_VIOLATION_PROBE)
        page.goto(f"{live_server['url']}/login")
        page.fill("input[name=username]", setup_admin["username"])
        page.fill("input[name=password]", setup_admin["password"])
        page.click("button[type=submit]")
        page.wait_for_url(f"{live_server['url']}/", timeout=10_000)

        page.goto(f"{live_server['url']}/browse")
        page.wait_for_load_state("networkidle")

        # Guard: the page really is translated, and the English selector would miss.
        expect(page.locator("html")).to_have_attribute("lang", html_lang)
        trigger = page.locator("#shortcut-help-trigger")
        assert trigger.get_attribute("title") != "Keyboard shortcuts (?)"

        modal = page.locator("#shortcut-modal")
        expect(modal).to_be_hidden()

        trigger.click()
        expect(modal).to_be_visible()
        page.locator("#shortcut-modal-close").click()
        expect(modal).to_be_hidden()
        assert page.evaluate("window.__cspViolations") == []

        trigger.click()
        expect(modal).to_be_visible()
        modal.click(position={"x": 5, "y": 5})
        expect(modal).to_be_hidden()
        assert page.evaluate("window.__cspViolations") == []
        assert_page_clean(page)
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# Modifier combos are left to the browser. Every "did not happen" assertion
# follows a positive control in the same test, so a dead handler cannot pass.
# ---------------------------------------------------------------------------

_MOBILE = {"width": 390, "height": 844}


def _open_browse(page, live_server, query=""):
    page.set_viewport_size(_MOBILE)
    page.goto(f"{live_server['url']}/browse{query}")
    page.wait_for_load_state("networkidle")


def test_browse_f_ignores_modifiers(authed_page, live_server):
    page = authed_page
    _open_browse(page, live_server)
    types = page.locator("#type-filter")
    expect(types).to_be_hidden()  # folded at 390px
    page.keyboard.press("f")
    expect(types).to_be_visible()
    page.keyboard.press("f")
    expect(types).to_be_hidden()
    for combo in ("Control+f", "Meta+f", "Alt+f"):
        page.keyboard.press(combo)
        expect(types).to_be_hidden()
    page.keyboard.press("f")
    expect(types).to_be_visible()


def test_browse_e_ignores_modifiers(authed_page, live_server):
    page = authed_page
    _open_browse(page, live_server)
    select_all = page.get_by_role("button", name="Select All", exact=True)
    expect(select_all).to_have_count(0)
    page.keyboard.press("e")
    expect(select_all).to_be_visible()
    page.keyboard.press("e")
    expect(select_all).to_have_count(0)
    for combo in ("Control+e", "Meta+e", "Alt+e"):
        page.keyboard.press(combo)
        expect(select_all).to_have_count(0)
    page.keyboard.press("e")
    expect(select_all).to_be_visible()


def test_browse_g_ignores_modifiers(authed_page, live_server):
    page = authed_page
    _open_browse(page, live_server)
    before = page.evaluate("localStorage.getItem('shelf-view')") or "grid"
    with page.expect_response(lambda r: "/api/search" in r.url):
        page.keyboard.press("g")
    flipped = page.evaluate("localStorage.getItem('shelf-view')")
    assert flipped in ("grid", "list") and flipped != before

    page.keyboard.press("Control+g")
    page.keyboard.press("Alt+g")
    page.keyboard.press("Meta+g")
    # Positive control: the handler is still alive after the ignored combos.
    select_all = page.get_by_role("button", name="Select All", exact=True)
    page.keyboard.press("e")
    expect(select_all).to_be_visible()
    assert page.evaluate("localStorage.getItem('shelf-view')") == flipped


def test_browse_x_ignores_alt(authed_page, live_server):
    page = authed_page
    search = page.locator("[data-testid='search-input']:visible")

    _open_browse(page, live_server, "?q=zzz")
    expect(search).to_have_value("zzz")
    with page.expect_response(lambda r: "/api/search" in r.url):
        page.keyboard.press("x")
    expect(search).to_have_value("")

    _open_browse(page, live_server, "?q=zzz")
    expect(search).to_have_value("zzz")
    page.keyboard.press("Alt+x")
    # Positive control: the handler is alive, and nothing from Alt+x is pending.
    select_all = page.get_by_role("button", name="Select All", exact=True)
    page.keyboard.press("e")
    expect(select_all).to_be_visible()
    expect(search).to_have_value("zzz")


def test_global_slash_ignores_modifiers(authed_page, live_server):
    page = authed_page
    _open_browse(page, live_server)
    focused = "document.activeElement.tagName"
    assert page.evaluate(focused) == "BODY"
    page.keyboard.press("/")
    assert page.evaluate(focused) == "INPUT"
    page.keyboard.press("Escape")
    assert page.evaluate(focused) == "BODY"
    page.keyboard.press("Control+/")
    assert page.evaluate(focused) == "BODY"
    page.keyboard.press("/")
    assert page.evaluate(focused) == "INPUT"


def test_global_question_mark_ignores_modifiers(authed_page, live_server):
    page = authed_page
    _open_browse(page, live_server)
    modal = page.locator("#shortcut-modal")
    expect(modal).to_be_hidden()
    page.keyboard.press("?")
    expect(modal).to_be_visible()
    page.keyboard.press("Escape")
    expect(modal).to_be_hidden()
    page.keyboard.press("Control+?")
    expect(modal).to_be_hidden()
    page.keyboard.press("?")
    expect(modal).to_be_visible()


@pytest.mark.parametrize("key,path", [("s", "/scan"), ("b", "/browse")])
def test_global_navigation_keys_ignore_modifiers(authed_page, live_server, key, path):
    page = authed_page
    home = f"{live_server['url']}/"
    page.goto(home)
    page.wait_for_load_state("networkidle")
    assert page.evaluate("document.activeElement.tagName") == "BODY"
    for combo in (f"Control+{key}", f"Meta+{key}", f"Alt+{key}"):
        with pytest.raises(PlaywrightTimeoutError):
            with page.expect_navigation(timeout=1500):
                page.keyboard.press(combo)
    with page.expect_navigation():
        page.keyboard.press(key)
    assert page.url.endswith(path)


# ---------------------------------------------------------------------------
# The help card sits centred and inside the viewport. The overlay used to be a
# block (no `flex` once `hidden` was dropped), so the card hung top-left on a
# desktop and ran 16px off a phone's right edge (test-drive Observation 3).
# ---------------------------------------------------------------------------

_CARD_BOX = """() => {
    const r = document.querySelector('#shortcut-modal > div').getBoundingClientRect();
    return {left: r.left, right: r.right, top: r.top, bottom: r.bottom,
            vw: window.innerWidth, vh: window.innerHeight};
}"""


@pytest.mark.parametrize("size", [(1280, 800), (390, 844)], ids=["desktop", "phone"])
def test_shortcut_help_card_is_centred_and_on_screen(authed_page, live_server, size):
    page = authed_page
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(f"{live_server['url']}/browse")
    page.wait_for_load_state("networkidle")
    modal = page.locator("#shortcut-modal")
    expect(modal).to_be_hidden()
    page.locator("#shortcut-help-trigger").click()
    expect(modal).to_be_visible()
    box = page.evaluate(_CARD_BOX)
    assert box["left"] >= 0 and box["right"] <= box["vw"], box
    assert box["top"] >= 0 and box["bottom"] <= box["vh"], box
    # Centred: the gaps either side differ by no more than a rounding pixel.
    assert abs(box["left"] - (box["vw"] - box["right"])) <= 1, box
    assert abs(box["top"] - (box["vh"] - box["bottom"])) <= 1, box
    page.locator("#shortcut-modal-close").click()
    expect(modal).to_be_hidden()
