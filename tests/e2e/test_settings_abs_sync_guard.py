"""E2E tests: the Audiobookshelf "Sync Now" guard across four configurations
(issue #41).

Sync Now and Test do not share a readiness question. `/api/sync/audiobookshelf/
test` reads the POST body first, falling back to `get_setting` — it gates on
*typed-or-available*. `/api/sync/audiobookshelf/stream` reads only
`get_setting(db, "abs_url")` / `get_setting(db, "abs_token")` and never looks
at the request — it gates on *availability alone*. `absSync`'s `absSyncReady`
getter (static/js/components-settings.js) and `:disabled="syncing ||
!absSyncReady"` (fragments/settings/integrations.html:84) encode that; this
file pins it across DB-saved, env-only, unconfigured, and typed-but-unsaved
credentials.

The `:disabled` attribute and `startSync()`'s early return are two layers
over one property, and a disabled button cannot be clicked — so the two
"reaches sync" cases below don't stop at asserting the button renders
enabled. Each wraps the click in `page.expect_request(...)`, with the stream
route aborted first so the click assertion still exercises `startSync()`
without the test reaching `app/services/audiobookshelf.py`'s real
`httpx.AsyncClient` against a made-up ABS URL.
"""
import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import assert_page_clean, attach_page_guard, _run_setup_wizard

pytestmark = pytest.mark.e2e


def _login(browser, base_url, credentials):
    """New context, logged in via the UI. Mirrors conftest's authed_page,
    but against an arbitrary (throwaway server_factory) base_url rather than
    the session-scoped live_server."""
    ctx = browser.new_context()
    page = attach_page_guard(ctx.new_page())
    page.goto(f"{base_url}/login")
    page.fill("input[name=username]", credentials["username"])
    page.fill("input[name=password]", credentials["password"])
    page.click("button[type=submit]")
    page.wait_for_url(f"{base_url}/browse", timeout=10_000)
    return ctx, page


def _open_integrations(page, base_url):
    page.goto(f"{base_url}/settings")
    page.wait_for_load_state("networkidle")
    page.get_by_test_id("tab-integrations").click()


def _sync_button(page):
    """The Sync Now button: the sole direct <button> child of the ABS card's
    closing border-t/pt-4 footer div. Located structurally, not by its own
    (state-dependent) label — it has no `type` attribute, which the Test
    button (type="button") and the nested "Manage Libraries" button
    (a descendant, not a direct child, of this footer div) don't share."""
    return page.locator('div[x-data="absSync"] > div.border-t.pt-4 > button')


def _test_button(page):
    return page.locator('div[x-data="absSync"] button[type="button"]')


def _sync_label(page):
    """The Sync Now button's currently-visible label span.

    `sync_button.inner_text()` races Alpine's `x-show` toggling on the two
    label spans (!syncing / syncing) right after a tab switch or reload — it
    is a one-shot read with no retry, and it can catch both spans still
    un-hidden. Asserting through `expect(...).to_have_text(...)` on the one
    `span:visible` match instead gets Playwright's auto-retry, which the
    plain inner_text() comparison did not.
    """
    return _sync_button(page).locator("span:visible")


def _save_abs_form(page, base_url, url="", token=""):
    """Fill and submit the ABS URL/token form through the Settings UI.

    Never a raw sqlite insert: abs_token is a SENSITIVE_KEY stored
    Fernet-encrypted, so a raw row would hold plaintext get_setting can't
    decrypt, and the test would pass or fail for a reason unrelated to the
    guard.
    """
    if url:
        page.fill("#abs_url", url)
    if token:
        page.fill("#abs_token", token)
    page.locator(
        'div[x-data="absSync"] form[action="/api/settings"] button[type=submit]'
    ).click()
    page.wait_for_url(f"{base_url}/settings")
    page.wait_for_load_state("networkidle")
    page.get_by_test_id("tab-integrations").click()


def test_sync_now_enabled_for_db_saved_credentials(browser, server_factory):
    """1. DB-saved reaches sync: URL + token saved through Settings makes
    Sync Now enabled, labeled exactly "Sync Now", and clicking it actually
    issues the stream request."""
    server = server_factory()
    creds = _run_setup_wizard(browser, server["url"])
    ctx, page = _login(browser, server["url"], creds)
    _open_integrations(page, server["url"])
    _save_abs_form(
        page, server["url"],
        url="http://abs-db.invalid:13378", token="db-saved-token",
    )

    sync_btn = _sync_button(page)
    expect(sync_btn).to_be_enabled()
    expect(_sync_label(page)).to_have_text("Sync Now")

    page.route("**/api/sync/audiobookshelf/stream", lambda route: route.abort())
    with page.expect_request("**/api/sync/audiobookshelf/stream"):
        sync_btn.click()

    assert_page_clean(page)
    ctx.close()


def test_sync_now_enabled_for_env_only_credentials(browser, server_factory):
    """2. Env-only reaches sync: ABS_URL/ABS_TOKEN supplied as env vars with
    no DB rows at all — the row that fails under any guard that keeps
    absUrl (blank for an env-only install) as a required operand."""
    server = server_factory({
        "ABS_URL": "http://abs-env.invalid:13378",
        "ABS_TOKEN": "env-token",
    })
    creds = _run_setup_wizard(browser, server["url"])
    ctx, page = _login(browser, server["url"], creds)
    _open_integrations(page, server["url"])

    sync_btn = _sync_button(page)
    expect(sync_btn).to_be_enabled()
    expect(_sync_label(page)).to_have_text("Sync Now")

    page.route("**/api/sync/audiobookshelf/stream", lambda route: route.abort())
    with page.expect_request("**/api/sync/audiobookshelf/stream"):
        sync_btn.click()

    assert_page_clean(page)
    ctx.close()


def test_unconfigured_offers_neither_sync_nor_test(browser, server_factory):
    """3. Unconfigured offers neither action: nothing typed, nothing saved,
    nothing in the environment — Sync Now and Test both stay disabled, and
    Sync Now's label reads the "nothing entered" copy."""
    server = server_factory()
    creds = _run_setup_wizard(browser, server["url"])
    ctx, page = _login(browser, server["url"], creds)
    _open_integrations(page, server["url"])

    sync_btn = _sync_button(page)
    test_btn = _test_button(page)
    expect(sync_btn).to_be_disabled()
    expect(test_btn).to_be_disabled()
    expect(_sync_label(page)).to_have_text("Enter URL and token to sync")

    assert_page_clean(page)
    ctx.close()


def test_typed_but_unsaved_distinguishes_test_from_sync(browser, server_factory):
    """4. Typed-but-unsaved is distinguished from both other states: typing a
    URL and a token without submitting makes Test enabled (it POSTs what is
    typed) while Sync Now stays disabled with its own "save first" copy —
    a state the old inline ternary label could not express at all."""
    server = server_factory()
    creds = _run_setup_wizard(browser, server["url"])
    ctx, page = _login(browser, server["url"], creds)
    _open_integrations(page, server["url"])

    page.fill("#abs_url", "http://abs-typed.invalid:13378")
    page.fill("#abs_token", "typed-not-saved-token")

    sync_btn = _sync_button(page)
    test_btn = _test_button(page)
    expect(test_btn).to_be_enabled()
    expect(sync_btn).to_be_disabled()
    expect(_sync_label(page)).to_have_text("Save your settings to sync")

    assert_page_clean(page)
    ctx.close()
