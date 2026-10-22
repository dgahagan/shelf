"""E2E coverage for the Duplicates page: merge with the fill highlight, a
refusal shown verbatim, and dismiss then restore.

`live_server` is shared (G34), so every title carries a per-test token, groups
are located by their `data-member-ids`, and each test deletes what it seeded."""
import re
import sqlite3
import uuid

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import assert_page_clean, insert_item

pytestmark = pytest.mark.e2e


def _db(data_dir):
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _insert_location(data_dir, name):
    conn = _db(data_dir)
    try:
        cur = conn.execute("INSERT INTO locations (name) VALUES (?)", (name,))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _add_copy(data_dir, item_id, location_id):
    from app.services.item_copies import insert_copy

    conn = _db(data_dir)
    try:
        insert_copy(conn, {"item_id": item_id, "copy_number": 1,
                           "location_id": location_id, "is_primary": 1})
        conn.commit()
    finally:
        conn.close()


def _open_checkout(data_dir, item_id, borrower):
    conn = _db(data_dir)
    try:
        row = conn.execute("SELECT id FROM borrowers WHERE name = ?", (borrower,)).fetchone()
        bid = row[0] if row else conn.execute(
            "INSERT INTO borrowers (name) VALUES (?)", (borrower,)).lastrowid
        conn.execute("INSERT INTO checkouts (item_id, borrower_id) VALUES (?, ?)",
                     (item_id, bid))
        conn.commit()
    finally:
        conn.close()


def _cleanup(data_dir, item_ids=(), location_ids=(), borrowers=()):
    conn = _db(data_dir)
    try:
        for iid in item_ids:
            for table in ("checkouts", "item_copies", "duplicate_dismissals"):
                cols = ("item_a_id", "item_b_id") if table == "duplicate_dismissals" else ("item_id",)
                for col in cols:
                    try:
                        conn.execute(f"DELETE FROM {table} WHERE {col} = ?", (iid,))
                    except sqlite3.Error:
                        pass
            try:
                conn.execute("DELETE FROM items WHERE id = ?", (iid,))
            except sqlite3.Error:
                pass
        for lid in location_ids:
            try:
                conn.execute("DELETE FROM locations WHERE id = ?", (lid,))
            except sqlite3.Error:
                pass
        for name in borrowers:
            try:
                conn.execute("DELETE FROM borrowers WHERE name = ?", (name,))
            except sqlite3.Error:
                pass
        conn.commit()
    finally:
        conn.close()


def _group(pg, *ids):
    return pg.locator(f'[data-dup-group][data-member-ids="{",".join(str(i) for i in ids)}"]')


def _accepting(pg):
    messages = []

    def _accept(dialog):
        messages.append(dialog.message)
        dialog.accept()

    pg.once("dialog", _accept)
    return messages


@pytest.mark.parametrize("size", [(1280, 800), (390, 844)], ids=["desktop", "phone"])
def test_merge_highlights_fill_and_keeps_both_copies(live_server, authed_page, size):
    data_dir = live_server["data_dir"]
    base = live_server["url"]
    token = uuid.uuid4().hex[:8]
    title = f"Dupe {token}"
    room_a, room_b = f"Dupe Room A {token}", f"Dupe Room B {token}"
    loc_a, loc_b = _insert_location(data_dir, room_a), _insert_location(data_dir, room_b)
    first = insert_item(data_dir, title=title, media_type="book", isbn=None)
    second = insert_item(data_dir, title=title, media_type="book", isbn=None,
                         publisher=f"Pub {token}")
    _add_copy(data_dir, first, loc_a)
    _add_copy(data_dir, second, loc_b)
    pg = authed_page
    pg.set_viewport_size({"width": size[0], "height": size[1]})
    try:
        pg.goto(f"{base}/duplicates")
        group = _group(pg, first, second)
        expect(group).to_have_count(1)
        card_1 = group.locator(f'[data-dup-member="{first}"]')
        card_2 = group.locator(f'[data-dup-member="{second}"]')
        expect(card_1.locator("input[data-dup-keep]")).to_be_checked()
        expect(card_2.locator('dd[data-field="publisher"]')).to_have_attribute(
            "data-fill-active", "")

        card_2.locator("input[data-dup-keep]").check()
        expect(group.locator('dd[data-field="publisher"][data-fill-active]')).to_have_count(0)
        card_1.locator("input[data-dup-keep]").check()
        expect(card_2.locator('dd[data-field="publisher"]')).to_have_attribute(
            "data-fill-active", "")

        messages = _accepting(pg)
        with pg.expect_response(
                lambda r: "/api/items/merge" in r.url and r.request.method == "POST") as resp:
            group.locator('[data-dup-action="merge"]').click()
        assert resp.value.ok
        assert messages == [f'Merge 2 records into "{title}"? '
                            'The merged-away records cannot be restored.']
        expect(group).to_have_count(0)

        pg.goto(f"{base}/item/{first}")
        copies = pg.locator("#item-copies")
        expect(copies).to_contain_text(room_a)
        expect(copies).to_contain_text(room_b)
        assert_page_clean(pg)
    finally:
        _cleanup(data_dir, (first, second), (loc_a, loc_b))


def test_refusal_is_shown_verbatim(live_server, authed_page):
    data_dir = live_server["data_dir"]
    base = live_server["url"]
    token = uuid.uuid4().hex[:8]
    title = f"Dupe {token}"
    borrower = f"Dupe Borrower {token}"
    first = insert_item(data_dir, title=title, media_type="book", isbn=None)
    second = insert_item(data_dir, title=title, media_type="book", isbn=None)
    _open_checkout(data_dir, first, borrower)
    _open_checkout(data_dir, second, borrower)
    pg = authed_page
    pg.set_viewport_size({"width": 1280, "height": 800})
    try:
        pg.goto(f"{base}/duplicates")
        group = _group(pg, first, second)
        expect(group).to_have_count(1)
        messages = _accepting(pg)
        with pg.expect_response(
                lambda r: "/api/items/merge" in r.url and r.request.method == "POST"):
            group.locator('[data-dup-action="merge"]').click()
        assert len(messages) == 1
        expect(group.locator("[data-dup-error]")).to_have_text(
            f'Cannot merge items that are both on loan: "{title}", "{title}". '
            'Check one in first.')
        expect(group).to_have_count(1)
        assert_page_clean(pg)
    finally:
        _cleanup(data_dir, (first, second), (), (borrower,))


def test_dismiss_then_restore(live_server, authed_page):
    data_dir = live_server["data_dir"]
    base = live_server["url"]
    token = uuid.uuid4().hex[:8]
    title = f"Dupe {token}"
    first = insert_item(data_dir, title=title, media_type="book", isbn=None)
    second = insert_item(data_dir, title=title, media_type="book", isbn=None)
    pg = authed_page
    pg.set_viewport_size({"width": 1280, "height": 800})
    try:
        pg.goto(f"{base}/duplicates")
        group = _group(pg, first, second)
        expect(group).to_have_count(1)
        with pg.expect_response(
                lambda r: "/api/duplicates/dismiss" in r.url and r.request.method == "POST") as resp:
            group.locator('[data-dup-action="dismiss"]').click()
        assert resp.value.ok
        expect(group).to_have_count(0)

        pg.reload()
        expect(_group(pg, first, second)).to_have_count(0)
        restore = pg.locator(
            f'[data-dup-list="dismissed"] [data-dup-action="restore"][data-pair="{first},{second}"]')
        expect(restore).to_have_count(1)
        with pg.expect_navigation(url=re.compile(r"/duplicates/?$"), timeout=10_000):
            restore.click()
        expect(pg.locator(
            f'[data-dup-list="title_author"] [data-dup-group][data-member-ids="{first},{second}"]'
        )).to_have_count(1)
        assert_page_clean(pg)
    finally:
        _cleanup(data_dir, (first, second))


@pytest.mark.parametrize("width", [390, 639, 640])
def test_long_unbroken_title_stays_inside_the_viewport(live_server, authed_page, width):
    """Below `sm` the card grid is one implicit track; without `grid-cols-1` and
    `min-w-0` a long unbroken title widened the page to ~2,900 px (diff review m1).
    640 is the seam control, where `sm:grid-cols-2` takes over."""
    data_dir = live_server["data_dir"]
    base = live_server["url"]
    title = f"Dupe{uuid.uuid4().hex[:8]}" + "x" * 320
    first = insert_item(data_dir, title=title, media_type="book", isbn=None)
    second = insert_item(data_dir, title=title, media_type="book", isbn=None)
    pg = authed_page
    pg.set_viewport_size({"width": width, "height": 844})
    try:
        pg.goto(f"{base}/duplicates")
        group = pg.locator(f'[data-dup-group][data-member-ids="{first},{second}"]')
        expect(group).to_be_visible()
        assert pg.evaluate("document.documentElement.scrollWidth") <= width
        for card in group.locator("[data-dup-member]").all():
            box = card.bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= width
        assert_page_clean(pg)
    finally:
        _cleanup(data_dir, item_ids=(first, second))
