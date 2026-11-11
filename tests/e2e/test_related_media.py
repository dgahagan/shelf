"""Related Media updates the separate format block through real HTMX swaps."""

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("origin", ["", "?from=stats"])
def test_first_format_link_and_last_unlink_need_no_reload(live_server, authed_page, origin):
    page = authed_page
    item_id = insert_item(live_server["data_dir"], title="Format refresh novel")
    audio_id = insert_item(
        live_server["data_dir"], title="Format refresh audio", media_type="audiobook",
    )
    base = f"/api/related-media/items/{item_id}"
    page.goto(f"{live_server['url']}/item/{item_id}{origin}")
    panel = page.locator("#related-media-panel")
    expect(panel).to_be_visible()
    formats = page.locator("#also-available-as")
    expect(formats).to_have_count(1)
    expect(formats).to_have_text("")

    navigations = []
    page.on("request", lambda r: navigations.append(r.url) if r.is_navigation_request() else None)
    panel.get_by_text("Link another catalogue item", exact=True).click()
    search = panel.locator('input[name="q"]')
    with page.expect_response(lambda r: f"{base}/search" in r.url and r.ok):
        search.fill("Format refresh audio")
        search.press("End")  # The search listens for keyup, not just input.

    form = panel.locator(f'form:has(input[name="other_item_id"][value="{audio_id}"])')
    form.locator('select[name="link_type"]').select_option("format")
    with page.expect_response(
        lambda r: r.request.method == "POST" and f"{base}/links" in r.url and r.ok
    ):
        form.get_by_role("button", name="Link", exact=True).click()

    expect(formats).to_contain_text("Also available as:")
    expect(formats.locator("a")).to_have_count(1)
    expect(formats.locator("a")).to_have_attribute("href", f"/item/{audio_id}{origin}")
    expect(panel).to_have_count(1)
    expect(panel.locator("#also-available-as")).to_have_count(0)

    with page.expect_response(
        lambda r: r.request.method == "DELETE" and f"{base}/links/{audio_id}" in r.url and r.ok
    ):
        panel.get_by_role("button", name="Unlink", exact=True).click()

    expect(formats).to_have_count(1)
    expect(formats.locator("a")).to_have_count(0)
    expect(formats).to_have_text("")
    expect(panel.get_by_role("button", name="Unlink", exact=True)).to_have_count(0)
    assert navigations == []
