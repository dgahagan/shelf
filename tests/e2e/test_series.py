"""E2E: series page renders grouped series with local gap inference."""
import re
import sqlite3
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item

pytestmark = pytest.mark.e2e


def _insert_series_meta(data_dir: Path, name: str, **kwargs) -> None:
    """Insert a series_meta row directly (mirrors conftest.insert_item —
    there's no shared series_meta helper). Used to seed a stored Hardcover
    check result without a live Hardcover call (#15)."""
    db_path = data_dir / "shelf.db"
    conn = sqlite3.connect(str(db_path))
    try:
        cols = ", ".join(["name", *kwargs.keys()])
        placeholders = ", ".join("?" for _ in range(len(kwargs) + 1))
        conn.execute(
            f"INSERT INTO series_meta ({cols}) VALUES ({placeholders})",
            [name, *kwargs.values()],
        )
        conn.commit()
    finally:
        conn.close()


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


def test_series_mark_complete_badge_and_filter(live_server, authed_page):
    """#15: marking a series complete via the ⋮ menu shows the green
    Complete badge on that card only, and the All/Complete/Incomplete
    filter chips then show and hide the right cards.

    Proves the fix for the $el/$root bug in setFilter() —
    static/js/components-item.js — where the filter chips were a complete
    no-op because the handler read this.$el (the clicked chip button)
    instead of this.$root (the seriesFilter root containing the cards).
    """
    insert_item(live_server["data_dir"], title="Finished Vol 1", isbn="9780902000097",
                series_name="Finished Trilogy", series_position=1)
    insert_item(live_server["data_dir"], title="Ongoing Vol 1", isbn="9780902000103",
                series_name="Ongoing Trilogy", series_position=1)

    authed_page.goto(f"{live_server['url']}/series")
    authed_page.wait_for_load_state("networkidle")

    card = authed_page.get_by_test_id("series-card").filter(has_text="Finished Trilogy")
    other_card = authed_page.get_by_test_id("series-card").filter(has_text="Ongoing Trilogy")
    expect(card.get_by_test_id("series-complete-badge")).to_be_hidden()

    card.get_by_test_id("series-actions").click()
    expect(card.get_by_test_id("toggle-complete")).to_contain_text("Mark complete")
    card.get_by_test_id("toggle-complete").click()

    # toggleComplete() shows a toast then reloads after a 600ms setTimeout —
    # wait it out rather than asserting on the pre-reload DOM.
    authed_page.wait_for_timeout(1200)
    authed_page.wait_for_load_state("networkidle")

    card = authed_page.get_by_test_id("series-card").filter(has_text="Finished Trilogy")
    other_card = authed_page.get_by_test_id("series-card").filter(has_text="Ongoing Trilogy")
    expect(card.get_by_test_id("series-complete-badge")).to_be_visible()
    expect(card.get_by_test_id("series-complete-badge")).to_contain_text("Complete")
    expect(other_card.get_by_test_id("series-complete-badge")).to_be_hidden()

    # Filter chips: All | Complete | Incomplete over the already-rendered cards.
    authed_page.get_by_test_id("filter-complete").click()
    expect(card).to_be_visible()
    expect(other_card).to_be_hidden()

    authed_page.get_by_test_id("filter-incomplete").click()
    expect(card).to_be_hidden()
    expect(other_card).to_be_visible()

    authed_page.get_by_test_id("filter-all").click()
    expect(card).to_be_visible()
    expect(other_card).to_be_visible()


def test_series_stored_hardcover_check_survives_reload(live_server, authed_page):
    """#15: a stored Hardcover check (hc_total/hc_missing/hc_checked_at in
    series_meta) renders the "N missing" badge from server-rendered data on
    every load — not just right after the check button runs — which is the
    reload-survival property the feature added. Seeded directly since a
    live Hardcover call isn't available in this offline suite."""
    insert_item(live_server["data_dir"], title="Checked Vol 1", isbn="9780902000110",
                series_name="Checked Saga", series_position=1)
    _insert_series_meta(live_server["data_dir"], "Checked Saga",
                         hc_total=5, hc_missing=2, hc_checked_at="2026-08-01T12:00:00")

    authed_page.goto(f"{live_server['url']}/series")
    authed_page.wait_for_load_state("networkidle")

    card = authed_page.get_by_test_id("series-card").filter(has_text="Checked Saga")
    badge = card.get_by_test_id("series-missing-badge")
    expect(badge).to_be_visible()
    expect(badge).to_contain_text("2 missing")
    expect(badge).to_contain_text("2026-08-01")

    authed_page.reload()
    authed_page.wait_for_load_state("networkidle")

    card = authed_page.get_by_test_id("series-card").filter(has_text="Checked Saga")
    badge = card.get_by_test_id("series-missing-badge")
    expect(badge).to_be_visible()
    expect(badge).to_contain_text("2 missing")
    expect(badge).to_contain_text("2026-08-01")


def test_series_unassigned_block_lists_seriesless_books(live_server, authed_page):
    """#31: books with no series show in the Unassigned pseudo-block; non-book
    media does not, and the block carries no series affordances.

    The strip is capped at UNASSIGNED_STRIP_CAP and ordered by
    `title COLLATE NOCASE`, and `live_server` is session-scoped — by the time
    this file runs, earlier e2e files have left dozens of seriesless books
    behind. Seed a title that sorts first under NOCASE ('0' precedes every
    digit/letter title the suite seeds) so membership is cap-independent.
    """
    insert_item(live_server["data_dir"], title="000 E2E Unassigned Book",
                isbn="9780902000127")
    insert_item(live_server["data_dir"], title="E2E Unassigned Disc",
                isbn="9780902000134", media_type="dvd")

    authed_page.goto(f"{live_server['url']}/series")
    authed_page.wait_for_load_state("networkidle")

    block = authed_page.get_by_test_id("unassigned-card")
    expect(block).to_be_visible()
    expect(block).to_contain_text("000 E2E Unassigned Book")
    expect(block).not_to_contain_text("E2E Unassigned Disc")
    # Non-book media is out of scope entirely, not merely past the cap.
    expect(authed_page.locator("body")).not_to_contain_text("E2E Unassigned Disc")

    # Cap- and order-independent: only the shape of the count line is pinned.
    expect(block.get_by_test_id("unassigned-count")).to_contain_text(
        re.compile(r"\d+ books? with no series")
    )

    # The seriesless book is not inside any real series card...
    expect(authed_page.get_by_test_id("series-card")
           .filter(has_text="000 E2E Unassigned Book")).to_have_count(0)
    # ...and the block renders none of the series affordances.
    expect(block.get_by_test_id("series-actions")).to_have_count(0)
    expect(block.get_by_test_id("edit-synopsis")).to_have_count(0)
    # The pseudo-entry must never reach the rename/merge datalist.
    expect(authed_page.locator(
        'datalist#series-names option[value="Unassigned"]')).to_have_count(0)


def test_series_unassigned_block_hidden_by_completeness_chips(live_server, authed_page):
    """#31: the Unassigned block makes no completeness claim, so it shows
    under All only — the unknown-counts-as-incomplete rule must not file it
    under Incomplete. The real series card keeps its existing behaviour."""
    insert_item(live_server["data_dir"], title="000 E2E Chip Loose Book",
                isbn="9780902000141")
    # Chips only render when series_list is non-empty.
    insert_item(live_server["data_dir"], title="Chip Saga Vol 1",
                isbn="9780902000158", series_name="E2E Chip Saga", series_position=1)

    authed_page.goto(f"{live_server['url']}/series")
    authed_page.wait_for_load_state("networkidle")

    block = authed_page.get_by_test_id("unassigned-card")
    real_card = authed_page.get_by_test_id("series-card").filter(has_text="E2E Chip Saga")
    expect(block).to_be_visible()

    authed_page.get_by_test_id("filter-complete").click()
    expect(block).to_be_hidden()

    authed_page.get_by_test_id("filter-incomplete").click()
    expect(block).to_be_hidden()
    # Unknown completeness still counts as incomplete for a real series.
    expect(real_card).to_be_visible()

    authed_page.get_by_test_id("filter-all").click()
    expect(block).to_be_visible()
    expect(real_card).to_be_visible()
