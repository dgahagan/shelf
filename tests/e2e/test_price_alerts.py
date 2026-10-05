"""E2E: price alerts — the Features row, the Integrations block, the item-page
list-price line, and the feature toggle that removes both surfaces."""
import sqlite3
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item

pytestmark = pytest.mark.e2e

ISBN = "9780306406157"


def _seed_history(data_dir: Path, item_id: int) -> None:
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        conn.executemany(
            "INSERT INTO price_history (item_id, price, source, observed_at) VALUES (?, ?, 'isbndb', ?)",
            [(item_id, 12.99, "2026-09-02 03:00:00"), (item_id, 8.49, "2026-09-20 03:00:00")],
        )
        conn.commit()
    finally:
        conn.close()


def _cleanup(data_dir: Path, item_id: int) -> None:
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        conn.execute("DELETE FROM price_history WHERE item_id = ?", (item_id,))
        conn.execute("DELETE FROM list_items WHERE item_id = ?", (item_id,))
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def priced_wishlist_item(live_server):
    """A wishlisted book with two priced observations, removed after the test
    (the live_server DB is session-scoped and shared)."""
    data_dir = live_server["data_dir"]
    item_id = insert_item(data_dir, wishlisted=True, isbn=ISBN, title="Price Alert Book")
    _seed_history(data_dir, item_id)
    yield item_id
    _cleanup(data_dir, item_id)


def _open_features_tab(page, base_url: str) -> None:
    page.goto(f"{base_url}/settings")
    page.click('[data-testid="tab-features"]')
    expect(page.locator('[data-testid="feature-row-price_alerts"]')).to_be_visible()


def _feature_state(page, key: str) -> str:
    row = page.locator(f'[data-testid="feature-row-{key}"]')
    if row.locator('[data-feature-state="on"]').count():
        return "on"
    assert row.locator('[data-feature-state="off"]').count()
    return "off"


def test_features_row_needs_setup_and_on(live_server, authed_page):
    _open_features_tab(authed_page, live_server["url"])
    row = authed_page.locator('[data-testid="feature-row-price_alerts"]')
    expect(row.locator("[data-feature-needs-setup]")).to_be_visible()
    assert _feature_state(authed_page, "price_alerts") == "on"


def test_integrations_block_saves_threshold(live_server, authed_page):
    base_url = live_server["url"]
    authed_page.goto(f"{base_url}/settings")
    authed_page.click('[data-testid="tab-integrations"]')
    block = authed_page.locator('[data-testid="price-alerts-block"]')
    expect(block).to_be_visible()
    expect(block.locator('[data-testid="price-alert-honesty"]')).to_have_text(
        "Tracks the publisher's list price. Good for spotting reprints and price cuts, not used-market deals."
    )
    expect(block.locator('[data-testid="feature-off-note"]')).to_have_count(0)

    try:
        block.locator('[data-testid="price-alert-threshold"]').fill("20")
        with authed_page.expect_navigation():
            block.locator('[data-testid="price-alert-save"]').click()
        authed_page.click('[data-testid="tab-integrations"]')
        expect(
            authed_page.locator('[data-testid="price-alert-threshold"]')
        ).to_have_value("20")
    finally:
        # Restore the default so no other test file sees a saved value.
        authed_page.goto(f"{base_url}/settings")
        authed_page.click('[data-testid="tab-integrations"]')
        authed_page.locator('[data-testid="price-alert-threshold"]').fill("15")
        with authed_page.expect_navigation():
            authed_page.locator('[data-testid="price-alert-save"]').click()


@pytest.mark.parametrize(
    "viewport",
    [{"width": 1280, "height": 800}, {"width": 390, "height": 844}],
    ids=["desktop", "mobile"],
)
def test_item_page_list_price_line(live_server, authed_page, priced_wishlist_item, viewport):
    authed_page.set_viewport_size(viewport)
    authed_page.goto(f"{live_server['url']}/item/{priced_wishlist_item}")
    line = authed_page.locator('[data-testid="list-price-line"]')
    expect(line).to_be_visible()
    expect(line).to_contain_text("List price $8.49")
    expect(line).to_contain_text("was $12.99 on 2026-09-02")


@pytest.mark.parametrize(
    "viewport",
    [{"width": 1280, "height": 800}, {"width": 390, "height": 844}],
    ids=["desktop", "mobile"],
)
def test_notify_link_lands_on_the_notify_field(live_server, authed_page, viewport):
    """test-drive Obs 4: the link switches to Library and brings the notify
    URL field into view with focus, rather than keeping the Integrations
    scroll offset. No E2E test sets a notify URL, so the link is shown."""
    authed_page.set_viewport_size(viewport)
    authed_page.goto(f"{live_server['url']}/settings")
    authed_page.click('[data-testid="tab-integrations"]')
    link = authed_page.locator('[data-testid="price-alert-notify-link"]')
    link.scroll_into_view_if_needed()
    link.click()
    field = authed_page.locator("#notify-url-input")
    expect(field).to_be_in_viewport()
    expect(field).to_be_focused()


def test_turning_price_alerts_off_removes_line_and_notes_block(
    live_server, authed_page, priced_wishlist_item
):
    base_url = live_server["url"]
    item_url = f"{base_url}/item/{priced_wishlist_item}"
    authed_page.on("dialog", lambda d: d.accept())

    authed_page.goto(item_url)
    expect(authed_page.locator('[data-testid="list-price-line"]')).to_be_visible()

    _open_features_tab(authed_page, base_url)
    assert _feature_state(authed_page, "price_alerts") == "on"
    try:
        authed_page.click('[data-testid="feature-toggle-price_alerts"]')
        expect(authed_page.locator('[data-testid="feature-row-price_alerts"] [data-feature-state="off"]')).to_be_visible()
        assert _feature_state(authed_page, "price_alerts") == "off"

        authed_page.goto(item_url)
        expect(authed_page.locator("h1")).to_contain_text("Price Alert Book")
        expect(authed_page.locator('[data-testid="list-price-line"]')).to_have_count(0)

        authed_page.goto(f"{base_url}/settings")
        authed_page.click('[data-testid="tab-integrations"]')
        expect(
            authed_page.locator(
                '[data-testid="price-alerts-block"] [data-testid="feature-off-note"][data-feature="price_alerts"]'
            )
        ).to_be_visible()
    finally:
        # Restore: the live_server DB is shared with every other E2E test.
        _open_features_tab(authed_page, base_url)
        if _feature_state(authed_page, "price_alerts") == "off":
            authed_page.click('[data-testid="feature-toggle-price_alerts"]')
            expect(authed_page.locator('[data-testid="feature-row-price_alerts"] [data-feature-state="on"]')).to_be_visible()
    assert _feature_state(authed_page, "price_alerts") == "on"
    authed_page.goto(item_url)
    expect(authed_page.locator('[data-testid="list-price-line"]')).to_be_visible()
