"""Fill details from Audible, end to end against the stub's /audnexus paths."""
import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import assert_page_clean, insert_item

pytestmark = pytest.mark.e2e


def _audnexus_paths(upc_stub):
    return [p for p, _ in list(upc_stub["requests"]) if p.startswith("/audnexus/")]


def test_fill_from_audible_fills_narrator(live_server, authed_page, upc_stub):
    item_id = insert_item(
        live_server["data_dir"], title="Project Hail Mary", media_type="audiobook",
        asin="B08G9PRS1K",
    )
    authed_page.goto(f"{live_server['url']}/item/{item_id}")
    authed_page.wait_for_load_state("networkidle")

    authed_page.get_by_test_id("fill-from-audible").click()
    result = authed_page.get_by_test_id("audnexus-fill-result")
    expect(result).to_contain_text("Filled")

    authed_page.reload()
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("Ray Porter")
    assert "/audnexus/books/B08G9PRS1K" in _audnexus_paths(upc_stub)
    assert_page_clean(authed_page)


def test_fill_from_audible_unknown_asin_is_not_in_catalogue(live_server, authed_page, upc_stub):
    item_id = insert_item(
        live_server["data_dir"], title="Obscure Audiobook", media_type="audiobook",
        asin="B000000000",
    )
    authed_page.goto(f"{live_server['url']}/item/{item_id}")
    authed_page.wait_for_load_state("networkidle")

    authed_page.get_by_test_id("fill-from-audible").click()
    result = authed_page.get_by_test_id("audnexus-fill-result")
    expect(result).to_have_attribute("data-outcome", "no_match")

    assert "/audnexus/books/B000000000" in _audnexus_paths(upc_stub)
    assert_page_clean(authed_page)
