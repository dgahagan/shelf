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


def test_series_rename_regroups(live_server, authed_page):
    """Renaming a series reloads the page with books regrouped under the new
    name; the actions menu is available without Hardcover."""
    insert_item(live_server["data_dir"], title="Alpha Vol 1", isbn="9780902000059",
                series_name="Alpha Saga", series_position=1)
    insert_item(live_server["data_dir"], title="Alpha Vol 2", isbn="9780902000066",
                series_name="Alpha Saga", series_position=2)

    authed_page.goto(f"{live_server['url']}/series")
    authed_page.wait_for_load_state("networkidle")

    card = authed_page.get_by_test_id("series-card").filter(has_text="Alpha Saga")
    card.get_by_test_id("series-actions").click()
    card.get_by_test_id("rename-series").click()
    card.get_by_test_id("rename-input").fill("Omega Saga")
    card.get_by_test_id("rename-submit").click()

    # submitRename() shows a toast then reloads after a 600ms setTimeout —
    # wait it out rather than asserting on the pre-reload DOM.
    authed_page.wait_for_timeout(1200)
    authed_page.wait_for_load_state("networkidle")

    renamed_card = authed_page.get_by_test_id("series-card").filter(has_text="Omega Saga")
    expect(renamed_card).to_have_count(1)
    expect(renamed_card).to_contain_text("Alpha Vol 1")
    expect(renamed_card).to_contain_text("Alpha Vol 2")
    # The old series name must be gone — not merely shadowed by "Omega Saga".
    expect(authed_page.get_by_test_id("series-card").filter(has_text="Alpha Saga")).to_have_count(0)


def test_series_remove_all_disbands(live_server, authed_page):
    """Remove all books disbands the series but keeps the books in the
    library — they should still show up on /browse."""
    insert_item(live_server["data_dir"], title="Disband Vol 1", isbn="9780902000073",
                series_name="Disband Saga", series_position=1)
    insert_item(live_server["data_dir"], title="Disband Vol 2", isbn="9780902000080",
                series_name="Disband Saga", series_position=2)

    authed_page.goto(f"{live_server['url']}/series")
    authed_page.wait_for_load_state("networkidle")

    card = authed_page.get_by_test_id("series-card").filter(has_text="Disband Saga")
    card.get_by_test_id("series-actions").click()
    card.get_by_test_id("remove-all").click()
    card.get_by_test_id("remove-all-confirm").click()

    # submitRemoveAll() shows a toast then reloads after a 600ms setTimeout.
    authed_page.wait_for_timeout(1200)
    authed_page.wait_for_load_state("networkidle")

    expect(authed_page.get_by_test_id("series-card").filter(has_text="Disband Saga")).to_have_count(0)

    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("Disband Vol 1")
    expect(authed_page.locator("body")).to_contain_text("Disband Vol 2")
