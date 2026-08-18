"""E2E: series page renders grouped series with local gap inference."""
import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item

pytestmark = pytest.mark.e2e


def test_series_page_groups_and_flags_gaps(live_server, authed_page):
    insert_item(live_server["data_dir"], title="Series Vol 1", isbn="9780902000011",
                series_name="E2E Saga", series_position=1)
    insert_item(live_server["data_dir"], title="Series Vol 3", isbn="9780902000028",
                series_name="E2E Saga", series_position=3)

    authed_page.goto(f"{live_server['url']}/series")
    authed_page.wait_for_load_state("networkidle")

    expect(authed_page.locator("body")).to_contain_text("E2E Saga")
    expect(authed_page.locator("body")).to_contain_text("2 owned")
    expect(authed_page.locator("body")).to_contain_text("possibly missing #2")
    # No Hardcover token in the E2E env — check button hidden
    expect(authed_page.get_by_test_id("check-series")).to_have_count(0)


def test_series_synopsis_edit_persists(live_server, authed_page):
    """Issue #6: a synopsis added inline on /series survives a reload."""
    insert_item(live_server["data_dir"], title="Synopsis Vol 1", isbn="9780902000035",
                series_name="Synopsis Saga", series_position=1)

    authed_page.goto(f"{live_server['url']}/series")
    authed_page.wait_for_load_state("networkidle")

    card = authed_page.get_by_test_id("series-card").filter(has_text="Synopsis Saga")
    card.get_by_test_id("edit-synopsis").click()
    card.get_by_test_id("synopsis-input").fill("A saga about writing synopses.")
    card.get_by_test_id("save-synopsis").click()
    expect(card.get_by_test_id("series-synopsis")).to_contain_text("A saga about writing synopses.")

    # Reload — the description came from series_meta, not client state.
    authed_page.reload()
    authed_page.wait_for_load_state("networkidle")
    card = authed_page.get_by_test_id("series-card").filter(has_text="Synopsis Saga")
    expect(card.get_by_test_id("series-synopsis")).to_contain_text("A saga about writing synopses.")


def test_series_synopsis_fetch_button_hidden_without_hardcover(live_server, authed_page):
    """The Hardcover fetch affordance only appears when a token is configured."""
    insert_item(live_server["data_dir"], title="NoHC Vol 1", isbn="9780902000042",
                series_name="NoHC Saga", series_position=1)
    authed_page.goto(f"{live_server['url']}/series")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.get_by_test_id("fetch-synopsis")).to_have_count(0)
