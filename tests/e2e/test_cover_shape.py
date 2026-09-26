"""Square music covers, measured in the browser (#119).

The unit suite pins the attribute on every site; only a browser can say the
CSS turns it into a square box. Each assertion reads `getBoundingClientRect()`
after the element is visible (G42 — a 0x0 rect means it was measured too
early), and is scoped to the seeded item by `data-item-id` or by a Type
filter, because the session server holds every earlier file's rows (G34).

The scan card is not driven here: reaching its cover needs the UPC stub path,
and `tests/test_cover_shape_sites.py` pins every scan-card renderer family.
"""

import base64
import re
import sqlite3

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item

pytestmark = pytest.mark.e2e

# A decodable 1x1 GIF. The box, not the image, sets the shape: a book's 2:3
# card and a record's square card both hold the same pixel.
_GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


def _seed_item_with_cover(live_server, **kwargs) -> int:
    """Insert an item and plant its cover file on disk, then point
    cover_path at it. Same shape as tests/e2e/test_item_crud.py's helper of
    the same name (not imported across e2e modules by house convention)."""
    data_dir = live_server["data_dir"]
    item_id = insert_item(data_dir, **kwargs)
    covers_dir = data_dir / "covers"
    covers_dir.mkdir(exist_ok=True)
    (covers_dir / f"{item_id}.jpg").write_bytes(_GIF)
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        conn.execute(
            "UPDATE items SET cover_path = ? WHERE id = ?",
            (f"covers/{item_id}.jpg", item_id),
        )
        conn.commit()
    finally:
        conn.close()
    return item_id


@pytest.fixture(scope="module")
def pair(live_server):
    """One vinyl and one book, both with covers, seeded once for the module
    (the session DB would reject a second insert of the same ISBN)."""
    vinyl = _seed_item_with_cover(
        live_server, title="Zq Square Shape Record", isbn="9780009119001",
        media_type="vinyl",
    )
    book = _seed_item_with_cover(
        live_server, title="Zq Square Shape Book", isbn="9780009119002",
        media_type="book",
    )
    return {"vinyl": vinyl, "book": book}


def _rect(locator):
    expect(locator).to_be_visible()  # G42
    box = locator.evaluate(
        "el => { const r = el.getBoundingClientRect();"
        " return {w: r.width, h: r.height}; }"
    )
    assert box["w"] > 0 and box["h"] > 0, box
    return box


def _browse(page, base_url, query=""):
    page.goto(f"{base_url}/browse{query}")
    page.wait_for_load_state("networkidle")


def _grid_card(page, item_id):
    return page.locator(f"#item-grid [data-item-id='{item_id}'] .cover-card").first


def test_browse_grid_mixed_types(live_server, authed_page, pair):
    """All types: the record's card is square, the book's is 2:3."""
    _browse(authed_page, live_server["url"], "?q=Zq+Square+Shape")
    vinyl = _rect(_grid_card(authed_page, pair["vinyl"]))
    book = _rect(_grid_card(authed_page, pair["book"]))
    assert abs(vinyl["w"] - vinyl["h"]) <= 1, vinyl
    assert abs(book["h"] / book["w"] - 1.5) <= 0.02, book


def test_browse_grid_music_only_is_uniform(live_server, authed_page, pair):
    """Type = Vinyl: every visible card is square."""
    _browse(authed_page, live_server["url"], "?media_type_filter=vinyl")
    cards = authed_page.locator("#item-grid .cover-card")
    expect(cards.first).to_be_visible()
    assert cards.count() >= 1
    for i in range(cards.count()):
        box = _rect(cards.nth(i))
        assert abs(box["w"] - box["h"]) <= 1, (i, box)


def test_browse_list_thumbnails(live_server, authed_page, pair):
    """List view: the record's thumbnail is 32x32, the book's 32x48."""
    _browse(authed_page, live_server["url"], "?q=Zq+Square+Shape")
    with authed_page.expect_response(lambda r: "/api/search" in r.url):
        authed_page.locator("[data-testid='view-list']").click()

    def thumb(item_id):
        row = authed_page.locator(f"tr[data-item-id='{item_id}']")
        return row.locator("img[data-cover-shape]").first

    vinyl = _rect(thumb(pair["vinyl"]))
    book = _rect(thumb(pair["book"]))
    assert (round(vinyl["w"]), round(vinyl["h"])) == (32, 32), vinyl
    assert (round(book["w"]), round(book["h"])) == (32, 48), book


def test_music_item_page_cover_is_square(live_server, authed_page, pair):
    authed_page.goto(f"{live_server['url']}/music/item/{pair['vinyl']}")
    box = _rect(authed_page.locator("[data-testid='music-item-cover']"))
    assert abs(box["w"] - box["h"]) <= 1, box


def test_music_item_page_without_a_cover_shows_none(live_server, authed_page):
    item_id = insert_item(
        live_server["data_dir"], title="Zq Coverless Record",
        isbn="9780009119003", media_type="vinyl",
    )
    authed_page.goto(f"{live_server['url']}/music/item/{item_id}")
    expect(authed_page.locator("h1")).to_have_text(re.compile("Zq Coverless Record"))
    expect(authed_page.locator("[data-testid='music-item-cover']")).to_have_count(0)
