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
