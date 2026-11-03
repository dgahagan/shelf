"""E2E: buy links — the Settings card, the wishlisted item page, the Series card.

No Hardcover request is made: the Series test fulfils GET /api/series/check in
the browser with a canned body (there is no E2E Hardcover stub), so it proves
the card renders the links; the server-side attach is pinned in the unit suite.
Every row seeded here is removed in a finally block — the server is shared.
"""
import json
import sqlite3

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import assert_page_clean, insert_item
from tests.e2e.test_features import _feature_state, _open_features_tab
from tests.e2e.test_release_radar import _clear_token, _set_token

pytestmark = pytest.mark.e2e

BOOKSHOP_ID = "424242"
AMAZON_TAG = "e2etag-20"
ISBN13 = "9780306406157"   # ISBN-10 0306406152
TITLE = "E2E Buy Links Wishlisted"
OWNED_TITLE = "E2E Buy Links Owned"


def _delete_items(data_dir, titles):
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        marks = ",".join("?" for _ in titles)
        ids = "SELECT id FROM items WHERE title IN (%s)" % marks
        # item_copies first: raw sqlite runs with foreign keys off (G126).
        conn.execute("DELETE FROM item_copies WHERE item_id IN (%s)" % ids, list(titles))
        conn.execute("DELETE FROM list_items WHERE item_id IN (%s)" % ids, list(titles))
        conn.execute("DELETE FROM items WHERE title IN (%s)" % marks, list(titles))
        conn.commit()
    finally:
        conn.close()


def _save_tags(pg, base, bookshop_id, amazon_tag):
    pg.goto(f"{base}/settings")
    pg.click('[data-testid="tab-library"]')
    block = pg.locator('[data-testid="buy-links-block"]')
    expect(block).to_be_visible()
    block.locator('[data-testid="buy-links-bookshop-id"]').fill(bookshop_id)
    block.locator('[data-testid="buy-links-amazon-tag"]').fill(amazon_tag)
    with pg.expect_navigation():
        block.locator('button[type="submit"]').click()


def test_settings_save_rerenders_values(live_server, authed_page):
    pg = authed_page
    base = live_server["url"]
    try:
        _save_tags(pg, base, BOOKSHOP_ID, AMAZON_TAG)
        pg.click('[data-testid="tab-library"]')
        block = pg.locator('[data-testid="buy-links-block"]')
        expect(block.locator('[data-testid="buy-links-bookshop-id"]')).to_have_value(BOOKSHOP_ID)
        expect(block.locator('[data-testid="buy-links-amazon-tag"]')).to_have_value(AMAZON_TAG)
    finally:
        _save_tags(pg, base, "", "")
    pg.click('[data-testid="tab-library"]')
    block = pg.locator('[data-testid="buy-links-block"]')
    expect(block.locator('[data-testid="buy-links-bookshop-id"]')).to_have_value("")
    expect(block.locator('[data-testid="buy-links-amazon-tag"]')).to_have_value("")
    assert_page_clean(pg)


def test_item_page_links_and_feature_toggle(live_server, authed_page):
    pg = authed_page
    base = live_server["url"]
    data_dir = live_server["data_dir"]
    toggled_off = False
    try:
        _delete_items(data_dir, [TITLE, OWNED_TITLE])
        wish_id = insert_item(data_dir, title=TITLE, isbn=ISBN13, authors="E2E Author",
                              owned=0, wishlisted=True)
        owned_id = insert_item(data_dir, title=OWNED_TITLE, isbn="9789030004016", authors="E2E Author")
        _save_tags(pg, base, BOOKSHOP_ID, AMAZON_TAG)

        pg.goto(f"{base}/item/{wish_id}")
        links = pg.locator('[data-testid="buy-links"]')
        expect(links).to_be_visible()
        bookshop = links.locator('[data-testid="buy-link-bookshop"]')
        amazon = links.locator('[data-testid="buy-link-amazon"]')
        expect(bookshop).to_have_attribute("href", f"https://bookshop.org/a/{BOOKSHOP_ID}/{ISBN13}")
        expect(amazon).to_have_attribute(
            "href", f"https://www.amazon.com/dp/0306406152?tag={AMAZON_TAG}")
        expect(bookshop).to_have_attribute("rel", "noopener sponsored")
        expect(amazon).to_have_attribute("rel", "noopener sponsored")
        expect(pg.locator('[data-testid="buy-links-disclosure"]')).to_contain_text(
            "Affiliate links")

        pg.goto(f"{base}/item/{owned_id}")
        expect(pg.get_by_role("heading", name=OWNED_TITLE)).to_be_visible()
        expect(pg.locator('[data-testid="buy-links"]')).to_have_count(0)

        _open_features_tab(pg, base)
        assert _feature_state(pg, "buy_links") == "on"
        pg.locator('[data-testid="feature-toggle-buy_links"]').click()
        toggled_off = True
        expect(pg.locator(
            '[data-testid="feature-row-buy_links"] [data-feature-state="off"]')).to_be_visible()

        pg.goto(f"{base}/item/{wish_id}")
        expect(pg.get_by_role("heading", name=TITLE)).to_be_visible()
        expect(pg.locator('[data-testid="buy-links"]')).to_have_count(0)
    finally:
        try:
            if toggled_off:
                _open_features_tab(pg, base)
                if _feature_state(pg, "buy_links") == "off":
                    pg.locator('[data-testid="feature-toggle-buy_links"]').click()
                    expect(pg.locator('[data-testid="feature-row-buy_links"] '
                                      '[data-feature-state="on"]')).to_be_visible()
        finally:
            try:
                _save_tags(pg, base, "", "")
            finally:
                _delete_items(data_dir, [TITLE, OWNED_TITLE])
    assert_page_clean(pg)


def test_series_card_renders_canned_buy_links(live_server, authed_page):
    pg = authed_page
    base = live_server["url"]
    data_dir = live_server["data_dir"]
    series = "E2E Buy Saga"
    titles = [f"{series} Vol 1", f"{series} Vol 2"]
    canned = {
        "ok": True, "series": series, "total": 3, "missing": 1,
        "books": [
            {"hardcover_book_id": 9_300_001, "title": f"{series} Vol 3", "authors": "E2E Author",
             "series_position": 3, "status": "missing",
             "buy_links": [
                 {"store": "bookshop", "label": "Bookshop.org",
                  "url": "https://bookshop.org/a/424242/9780306406157", "deep": True},
                 {"store": "amazon", "label": "Amazon",
                  "url": "https://www.amazon.com/dp/0306406152?tag=e2etag-20", "deep": True},
             ]},
        ],
        "buy_links_disclosure": {"bookshop": True, "amazon": True},
    }

    def _fulfil(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(canned))

    def _seed():
        for n, (title, isbn) in enumerate(zip(titles, ["9789030003019", "9789030003026"]), 1):
            insert_item(data_dir, title=title, isbn=isbn, series_name=series, series_position=n)
        conn = sqlite3.connect(str(data_dir / "shelf.db"))
        try:
            conn.execute("INSERT INTO series_meta (name, hc_missing) VALUES (?, 0)", (series,))
            conn.commit()
        finally:
            conn.close()

    def _cleanup():
        _delete_items(data_dir, titles)
        conn = sqlite3.connect(str(data_dir / "shelf.db"))
        try:
            conn.execute("DELETE FROM series_meta WHERE name = ?", (series,))
            conn.commit()
        finally:
            conn.close()

    routed = False
    try:
        _cleanup()
        _set_token(pg, base, "e2e-fake-hardcover-token")
        _seed()
        pg.route("**/api/series/check?*", _fulfil)
        routed = True

        pg.goto(f"{base}/series")
        card = pg.locator(f'[data-testid="series-card"][data-series-name="{series}"]')
        button = card.get_by_test_id("check-series")
        expect(button).to_be_visible(timeout=10_000)
        button.click()

        anchors = card.get_by_test_id("series-buy-link")
        expect(anchors).to_have_count(2, timeout=10_000)
        row = card.locator("div.flex.items-center").filter(has_text=f"{series} Vol 3")
        bookshop = row.get_by_test_id("series-buy-link").filter(has_text="Bookshop.org")
        amazon = row.get_by_test_id("series-buy-link").filter(has_text="Amazon")
        expect(bookshop).to_have_attribute("href", "https://bookshop.org/a/424242/9780306406157")
        expect(amazon).to_have_attribute(
            "href", "https://www.amazon.com/dp/0306406152?tag=e2etag-20")
        expect(bookshop).to_have_attribute("rel", "noopener sponsored")
        expect(amazon).to_have_attribute("rel", "noopener sponsored")
        expect(card.get_by_test_id("series-buy-links-disclosure")).to_contain_text(
            "Affiliate links")
    finally:
        try:
            if routed:
                pg.unroute("**/api/series/check?*", _fulfil)
        finally:
            try:
                _clear_token(pg, base)
            finally:
                _cleanup()
    assert_page_clean(pg)
