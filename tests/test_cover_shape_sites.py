"""Issue #119, T2: `data-cover-shape` on the grid, list and read-only
thumbnail sites.

Each test seeds one `vinyl` (square) and one `book` (portrait), each with a
`cover_path` so the templates' `{% if *.cover_path %}` branch — the one that
carries the attribute — actually renders, commits before the request (G48),
and asserts on the specific element for the seeded item, never a bare
substring search over the whole page (G69). The block helpers below scope
each assertion to the HTML between one item's own anchor (its id, its href,
its title, or a `data-testid`/`data-copy-id` the template already carries)
and the next such anchor, so a pin can never accidentally read a sibling
item's attribute.
"""
import re
from unittest.mock import AsyncMock

from app.services import item_write, media_groups, provider_result
from tests.conftest import _insert_item, _insert_location

VINYL_COVER = "covers/cover-shape-vinyl.jpg"
BOOK_COVER = "covers/cover-shape-book.jpg"


def _seed_pair(db, **overrides):
    """Seed one vinyl (square) + one book (portrait), each with a cover."""
    vinyl_kwargs = dict(
        title="Cover Shape Vinyl", isbn="9780000000301",
        media_type="vinyl", cover_path=VINYL_COVER,
    )
    book_kwargs = dict(
        title="Cover Shape Book", isbn="9780000000302",
        media_type="book", cover_path=BOOK_COVER,
    )
    vinyl_kwargs.update(overrides.get("vinyl", {}))
    book_kwargs.update(overrides.get("book", {}))
    vinyl_id = _insert_item(db, **vinyl_kwargs)
    book_id = _insert_item(db, **book_kwargs)
    return vinyl_id, book_id


def _blocks(html: str, marker: str) -> list[str]:
    """Split `html` into chunks, each starting at one occurrence of `marker`.

    Consecutive per-item anchors (an id, an href, a data-testid) split the
    page into one chunk per item; the chunk for item N runs up to (but not
    including) item N+1's anchor, so a shape read from inside a chunk can
    belong only to that item.
    """
    idxs = [m.start() for m in re.finditer(re.escape(marker), html)]
    idxs.append(len(html))
    return [html[idxs[i]:idxs[i + 1]] for i in range(len(idxs) - 1)]


def _shape_in(block: str) -> str:
    m = re.search(r'data-cover-shape="(square|portrait)"', block)
    assert m, f"no data-cover-shape found in block: {block[:300]!r}"
    return m.group(1)


def _block_starting_with(html: str, marker: str, prefix: str) -> str:
    for block in _blocks(html, marker):
        if block.startswith(prefix):
            return block
    raise AssertionError(f"no block starting with {prefix!r} (marker {marker!r})")


def _block_containing(html: str, marker: str, needle: str) -> str:
    for block in _blocks(html, marker):
        if needle in block:
            return block
    raise AssertionError(f"no block containing {needle!r} (marker {marker!r})")


# --- Browse grid + list (item_card.html / item_row.html, via /api/search) --

def test_browse_grid_carries_shape_on_the_cover_card(admin_client, db):
    vinyl_id, book_id = _seed_pair(db)
    db.commit()

    html = admin_client.get("/api/search").text

    vinyl_block = _block_starting_with(html, 'data-item-id="', f'data-item-id="{vinyl_id}"')
    book_block = _block_starting_with(html, 'data-item-id="', f'data-item-id="{book_id}"')
    assert _shape_in(vinyl_block) == "square"
    assert _shape_in(book_block) == "portrait"


def test_browse_list_carries_shape_on_the_img(admin_client, db):
    vinyl_id, book_id = _seed_pair(db)
    db.commit()

    html = admin_client.get("/api/search?view=list").text

    vinyl_block = _block_starting_with(html, 'data-item-id="', f'data-item-id="{vinyl_id}"')
    book_block = _block_starting_with(html, 'data-item-id="', f'data-item-id="{book_id}"')
    assert _shape_in(vinyl_block) == "square"
    assert _shape_in(book_block) == "portrait"


# --- Home recent (home.html) -----------------------------------------------

def test_home_recent_carries_shape(admin_client, db):
    vinyl_id, book_id = _seed_pair(db)
    db.commit()

    html = admin_client.get("/").text

    vinyl_block = _block_starting_with(html, 'href="/item/', f'href="/item/{vinyl_id}"')
    book_block = _block_starting_with(html, 'href="/item/', f'href="/item/{book_id}"')
    assert _shape_in(vinyl_block) == "square"
    assert _shape_in(book_block) == "portrait"


# --- Public share page (share.html) -----------------------------------------

def test_share_page_carries_shape(admin_client, client, db):
    vinyl_id, book_id = _seed_pair(db)
    db.commit()

    resp = admin_client.post("/api/share", data={"scope": "collection", "label": "Cover Shape"},
                              follow_redirects=False)
    assert resp.status_code == 303
    from app.database import get_db
    with get_db() as share_db:
        link = dict(share_db.execute(
            "SELECT * FROM share_links ORDER BY id DESC LIMIT 1").fetchone())

    client.cookies.clear()
    html = client.get(f"/share/{link['token']}").text

    vinyl_block = _block_containing(html, '<div class="cover-card', "Cover Shape Vinyl")
    book_block = _block_containing(html, '<div class="cover-card', "Cover Shape Book")
    assert _shape_in(vinyl_block) == "square"
    assert _shape_in(book_block) == "portrait"


# --- Stats: Recently Added (stats.html) -------------------------------------

def test_stats_recent_carries_shape(admin_client, db):
    vinyl_id, book_id = _seed_pair(db)
    db.commit()

    html = admin_client.get("/stats").text

    vinyl_block = _block_starting_with(html, '<a href="/item/', f'<a href="/item/{vinyl_id}?from=stats"')
    book_block = _block_starting_with(html, '<a href="/item/', f'<a href="/item/{book_id}?from=stats"')
    assert _shape_in(vinyl_block) == "square"
    assert _shape_in(book_block) == "portrait"


# --- Item edit (item_edit.html) ---------------------------------------------

def test_item_edit_carries_shape(admin_client, db):
    vinyl_id, book_id = _seed_pair(db)
    db.commit()

    vinyl_html = admin_client.get(f"/item/{vinyl_id}/edit").text
    book_html = admin_client.get(f"/item/{book_id}/edit").text

    # Each page is scoped to exactly one item -- the whole page is "the element".
    assert _shape_in(vinyl_html) == "square"
    assert _shape_in(book_html) == "portrait"


# --- Trash (trash_list.html) -------------------------------------------------

def test_trash_carries_shape(admin_client, db):
    vinyl_id, book_id = _seed_pair(db)
    db.commit()
    item_write.trash_item(db, vinyl_id)
    item_write.trash_item(db, book_id)
    db.commit()

    html = admin_client.get("/trash").text

    vinyl_block = _block_starting_with(
        html, "data-testid=\"trash-item-row-", f'data-testid="trash-item-row-{vinyl_id}"')
    book_block = _block_starting_with(
        html, "data-testid=\"trash-item-row-", f'data-testid="trash-item-row-{book_id}"')
    assert _shape_in(vinyl_block) == "square"
    assert _shape_in(book_block) == "portrait"


# --- Related media panel (related_media_panel.html) -------------------------

def test_related_media_panel_carries_shape(admin_client, db):
    vinyl_id, book_id = _seed_pair(db)
    anchor_id = _insert_item(db, title="Cover Shape Anchor", isbn="9780000000303", media_type="dvd")
    media_groups.link_items(db, anchor_id, vinyl_id, link_type="format")
    media_groups.link_items(db, anchor_id, book_id, link_type="format")
    db.commit()

    html = admin_client.get(f"/api/related-media/items/{anchor_id}/panel").text

    vinyl_block = _block_containing(
        html, '<div class="flex items-center gap-3 bg-shelf-bg border border-shelf-border rounded-lg p-2">',
        f'/item/{vinyl_id}"')
    book_block = _block_containing(
        html, '<div class="flex items-center gap-3 bg-shelf-bg border border-shelf-border rounded-lg p-2">',
        f'/item/{book_id}"')
    assert _shape_in(vinyl_block) == "square"
    assert _shape_in(book_block) == "portrait"


# --- Location arrange (location_order.html) ---------------------------------

def test_location_arrange_carries_shape(admin_client, db):
    location_id = _insert_location(db, "Cover Shape Shelf")
    vinyl_id, book_id = _seed_pair(db)
    vinyl_copy = db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id, is_primary) "
        "VALUES (?, 1, ?, 1)", (vinyl_id, location_id),
    ).lastrowid
    book_copy = db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id, is_primary) "
        "VALUES (?, 1, ?, 1)", (book_id, location_id),
    ).lastrowid
    db.commit()

    html = admin_client.get(f"/locations/{location_id}/arrange").text

    vinyl_block = _block_starting_with(html, "data-copy-id=\"", f'data-copy-id="{vinyl_copy}"')
    book_block = _block_starting_with(html, "data-copy-id=\"", f'data-copy-id="{book_copy}"')
    assert _shape_in(vinyl_block) == "square"
    assert _shape_in(book_block) == "portrait"


# --- Recent scans (recent_scans.html) ---------------------------------------

def test_recent_scans_carries_shape(admin_client, db):
    vinyl_id, book_id = _seed_pair(db)
    db.execute(
        "INSERT INTO scan_log (isbn, media_type, result, item_id, mode) "
        "VALUES (?, 'vinyl', 'added', ?, 'add')", (None, vinyl_id),
    )
    db.execute(
        "INSERT INTO scan_log (isbn, media_type, result, item_id, mode) "
        "VALUES (?, 'book', 'added', ?, 'add')", (None, book_id),
    )
    db.commit()

    html = admin_client.get("/api/recent-scans?mode=add").text

    vinyl_block = _block_containing(html, '<div class="scan-result', f'/item/{vinyl_id}"')
    book_block = _block_containing(html, '<div class="scan-result', f'/item/{book_id}"')
    assert _shape_in(vinyl_block) == "square"
    assert _shape_in(book_block) == "portrait"


# --- Shelf Fill result fragment (shelf_fill_result.html) --------------------
# Cheap to render directly via /api/shelf-fill/place (form-encoded item_id +
# location_id, no scan/session state needed), so it is pinned here rather
# than left only to the T6 guard.

def test_shelf_fill_result_carries_shape(admin_client, db):
    location_id = _insert_location(db, "Cover Shape Shelf Fill")
    vinyl_id, book_id = _seed_pair(db)
    db.commit()

    vinyl_html = admin_client.post(
        "/api/shelf-fill/place", data={"item_id": vinyl_id, "location_id": location_id},
    ).text
    book_html = admin_client.post(
        "/api/shelf-fill/place", data={"item_id": book_id, "location_id": location_id},
    ).text

    assert _shape_in(vinyl_html) == "square"
    assert _shape_in(book_html) == "portrait"


# --- Cover picker (cover_search.html — T3) -----------------------------------
# `cover_search.html` is rendered from five call sites (G113): three direct
# renders (`items_covers.cover_search`, `items_covers.cover_select`'s failure
# re-render, `cover_review.cover_review_search`) exercised below with a
# covered item, plus two that reach it only through `cover_review_item.html`
# (`cover_review.render_card` and `cover_review.cover_review_page`) exercised
# in the next section against the queue's coverless "No cover" slot.

_CANDIDATES = [
    {"url": "https://example.invalid/cover-shape-1.jpg",
     "thumbnail": "https://example.invalid/cover-shape-1-thumb.jpg",
     "source": "stub-source"},
    {"url": "https://example.invalid/cover-shape-2.jpg",
     "thumbnail": "https://example.invalid/cover-shape-2-thumb.jpg",
     "source": "stub-source"},
]


def _stub_search_covers(monkeypatch):
    """No network call (`tests/test_cover*.py`'s own stubbing pattern)."""
    from app.services import covers

    search = AsyncMock(return_value=provider_result.found("stub-source", _CANDIDATES))
    monkeypatch.setattr(covers, "search_covers", search)
    return search


def _current_and_candidate_blocks(html: str) -> list[str]:
    """Every `<img ...>` opening tag in the picker markup, current cover first."""
    return re.findall(r"<img\b[^>]*>", html)


def test_cover_search_endpoint_carries_shape_on_current_and_candidates(
    editor_client, db, monkeypatch
):
    _stub_search_covers(monkeypatch)
    vinyl_id, book_id = _seed_pair(db)
    db.commit()

    vinyl_html = editor_client.get(f"/api/items/{vinyl_id}/cover-search").text
    book_html = editor_client.get(f"/api/items/{book_id}/cover-search").text

    vinyl_imgs = _current_and_candidate_blocks(vinyl_html)
    book_imgs = _current_and_candidate_blocks(book_html)
    assert len(vinyl_imgs) == 3  # current + 2 candidates
    assert all(_shape_in(img) == "square" for img in vinyl_imgs)
    assert len(book_imgs) == 3
    assert all(_shape_in(img) == "portrait" for img in book_imgs)


def test_cover_select_failure_rerender_carries_shape(editor_client, db, monkeypatch):
    from app.services import covers

    monkeypatch.setattr(covers, "_download_to_item", AsyncMock(return_value=None))
    _stub_search_covers(monkeypatch)
    vinyl_id, book_id = _seed_pair(db)
    db.commit()

    vinyl_html = editor_client.post(
        f"/api/items/{vinyl_id}/cover-select", data={"url": "https://example.invalid/bad.jpg"}
    ).text
    book_html = editor_client.post(
        f"/api/items/{book_id}/cover-select", data={"url": "https://example.invalid/bad.jpg"}
    ).text

    vinyl_imgs = _current_and_candidate_blocks(vinyl_html)
    book_imgs = _current_and_candidate_blocks(book_html)
    assert len(vinyl_imgs) == 3
    assert all(_shape_in(img) == "square" for img in vinyl_imgs)
    assert len(book_imgs) == 3
    assert all(_shape_in(img) == "portrait" for img in book_imgs)


def test_cover_review_search_endpoint_carries_shape(admin_client, db, monkeypatch):
    """`/api/covers/review/{id}/cover-search` takes any item id — it is not
    gated by the queue predicate — so it is exercised here directly against a
    covered item, the same as the item-detail picker above."""
    _stub_search_covers(monkeypatch)
    vinyl_id, book_id = _seed_pair(db)
    db.commit()

    vinyl_html = admin_client.get(f"/api/covers/review/{vinyl_id}/cover-search").text
    book_html = admin_client.get(f"/api/covers/review/{book_id}/cover-search").text

    vinyl_imgs = _current_and_candidate_blocks(vinyl_html)
    book_imgs = _current_and_candidate_blocks(book_html)
    assert len(vinyl_imgs) == 3
    assert all(_shape_in(img) == "square" for img in vinyl_imgs)
    assert len(book_imgs) == 3
    assert all(_shape_in(img) == "portrait" for img in book_imgs)


# --- Cover review queue (cover_review_item.html's "No cover" slot — T3) -----
# Every row in the queue is coverless by `_QUEUE_PREDICATE`, so the picker's
# "current cover" tile never renders here (`cover_search.html`'s
# `{% if candidates or cover_path %}` guard) — the slot these tests pin is
# `cover_review_item.html:66`, the placeholder box, which unconditionally
# needs the shape.

def test_cover_review_page_carries_shape_on_the_no_cover_slot(admin_client, db):
    """The fifth context (G113, rev 1): `cover_review_page` builds its own
    dict and renders `cover_review.html` directly, not through `render_card`."""
    vinyl_id = _insert_item(db, title="Cover Shape Queue Vinyl", isbn="9780000000401",
                             media_type="vinyl", cover_path=None)
    db.commit()

    html = admin_client.get("/cover-review").text

    assert f'data-item-id="{vinyl_id}"' in html
    assert _shape_in(html) == "square"


def test_cover_review_page_carries_shape_on_the_no_cover_slot_for_a_book(admin_client, db):
    book_id = _insert_item(db, title="Cover Shape Queue Book", isbn="9780000000402",
                            media_type="book", cover_path=None)
    db.commit()

    html = admin_client.get("/cover-review").text

    assert f'data-item-id="{book_id}"' in html
    assert _shape_in(html) == "portrait"


def test_cover_review_next_render_card_carries_shape_on_the_no_cover_slot(admin_client, db):
    """`render_card` is the other path into `cover_review_item.html` — hit by
    Skip/dismiss/upload-failure re-renders. `/api/covers/review/next` with no
    `after` seeks the same top-of-queue row `cover_review_page` would, via
    `render_card` instead."""
    vinyl_id = _insert_item(db, title="Cover Shape Skip Vinyl", isbn="9780000000403",
                             media_type="vinyl", cover_path=None)
    db.commit()

    html = admin_client.get("/api/covers/review/next").text

    assert f'data-item-id="{vinyl_id}"' in html
    assert _shape_in(html) == "square"


def test_cover_review_next_render_card_carries_shape_for_a_book(admin_client, db):
    book_id = _insert_item(db, title="Cover Shape Skip Book", isbn="9780000000404",
                            media_type="book", cover_path=None)
    db.commit()

    html = admin_client.get("/api/covers/review/next").text

    assert f'data-item-id="{book_id}"' in html
    assert _shape_in(html) == "portrait"


# --- T4: scan card, its poller, and the inventory audit ---------------------
# `scan_result.html`'s column-0 `{% else %}` result card, `cover_thumb.html`
# (both included and polled), `inventory_missing.html` and every
# `restore_report.restored_card` consumer. One renderer per family, plus the
# poller, the audit fragment and the book control (G80/G113 in the NOTE).

def _jpeg(size=512) -> bytes:
    """A minimal blob that passes `covers._looks_like_image` — the same shape
    `tests/test_covers.py::TestCoverUpload._jpeg` uses."""
    return b"\xff\xd8\xff" + b"\x00" * (size - 3)


def test_upc_scan_add_carries_shape_for_a_cd(editor_client, db, monkeypatch):
    """Vinyl has no UPC metadata provider (`UPC_METADATA_PROVIDERS` maps only
    dvd/video_game), so the ordinary no-provider title-only path never gets a
    cover_url to download — the out-of-scope "6b residue" the design plan
    names. Patched here, in the shared dict `items_common.UPC_METADATA_PROVIDERS`
    re-exports from `title_lookup` (same object, `monkeypatch.setitem` reverts
    it), so this pins the *renderer*, not a real music cover source."""
    from app.routers import items_common
    from app.services import provider_result, tmdb, upcitemdb

    async def _lookup(upc, client):
        return provider_result.found("upcitemdb", {
            "title": "Cover Shape Test Album", "category": None, "brand": None, "images": [],
        })
    monkeypatch.setattr(upcitemdb, "lookup", _lookup)
    monkeypatch.setitem(items_common.UPC_METADATA_PROVIDERS, "vinyl", "tmdb")
    monkeypatch.setenv("TMDB_API_KEY", "0123456789abcdef0123456789abcdef")

    async def _lookup_by_title(query, key, client):
        return provider_result.found("tmdb", {
            "title": "Cover Shape Test Album", "description": None,
            "publish_year": None, "cover_url": "https://example.invalid/cover-shape.jpg",
        })
    monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)

    async def _download(item_id, url, client):
        return VINYL_COVER
    monkeypatch.setattr(items_common.covers, "_download_to_item", _download)

    resp = editor_client.post(
        "/api/scan", data={"isbn": "085391163121", "media_type": "vinyl"})

    assert resp.status_code == 200
    assert 'data-scan-status="added"' in resp.text
    assert 'data-cover-shape="square"' in resp.text
    row = db.execute("SELECT media_type, cover_path FROM items WHERE title = ?",
                      ("Cover Shape Test Album",)).fetchone()
    assert row["media_type"] == "vinyl"
    assert row["cover_path"] == VINYL_COVER


def test_manual_add_carries_shape_for_an_uploaded_vinyl_cover(admin_client, db):
    resp = admin_client.post(
        "/api/items/manual",
        data={"title": "Cover Shape Manual Vinyl", "media_type": "vinyl"},
        files={"cover": ("c.jpg", _jpeg(), "image/jpeg")},
    )

    assert resp.status_code == 200
    assert 'data-scan-status="added"' in resp.text
    assert 'data-cover-shape="square"' in resp.text


def test_manual_add_carries_shape_for_an_uploaded_book_cover(admin_client, db):
    """The book control the acceptance criteria name: same renderer, a book,
    still portrait."""
    resp = admin_client.post(
        "/api/items/manual",
        data={"title": "Cover Shape Manual Book", "media_type": "book"},
        files={"cover": ("c.jpg", _jpeg(), "image/jpeg")},
    )

    assert resp.status_code == 200
    assert 'data-scan-status="added"' in resp.text
    assert 'data-cover-shape="portrait"' in resp.text


def test_scan_mode_lookup_carries_shape_for_a_seeded_vinyl(admin_client, db):
    _insert_item(db, title="Cover Shape Lookup Vinyl", isbn="9780000000501",
                 media_type="vinyl", cover_path=VINYL_COVER)
    db.commit()

    html = admin_client.post(
        "/api/scan", data={"isbn": "9780000000501", "mode": "lookup"}).text

    assert 'data-scan-status="found"' in html
    assert 'data-cover-shape="square"' in html


def test_trash_restore_from_scan_carries_shape_for_a_vinyl(admin_client, db):
    from app.services import item_write

    item_id = _insert_item(db, title="Cover Shape Trash Vinyl", isbn="9780000000502",
                           media_type="vinyl", cover_path=VINYL_COVER)
    item_write.trash_item(db, item_id)
    db.commit()

    resp = admin_client.post(f"/api/trash/items/{item_id}/restore", data={
        "isbn": "9780000000502", "mode": "add", "render": "scan",
    })

    assert resp.status_code == 200
    assert 'data-scan-status="restored"' in resp.text
    assert 'data-cover-shape="square"' in resp.text


def test_cover_status_poll_carries_shape_for_a_vinyl(admin_client, db):
    item_id = _insert_item(db, title="Cover Shape Poll Vinyl", isbn="9780000000503",
                           media_type="vinyl", cover_path=VINYL_COVER)
    db.commit()

    html = admin_client.get(f"/api/items/{item_id}/cover-status").text

    assert _shape_in(html) == "square"


def test_inventory_missing_carries_shape_for_a_vinyl(admin_client, db):
    location_id = _insert_location(db, "Cover Shape Audit Shelf")
    _insert_item(db, title="Cover Shape Audit Vinyl", isbn="9780000000504",
                 media_type="vinyl", cover_path=VINYL_COVER, location_id=location_id)
    db.commit()

    html = admin_client.post(
        "/api/inventory/missing", data={"location_id": location_id}).text

    assert _shape_in(html) == "square"


# --- Music item page (music_item.html, T5) ---------------------------------

def test_music_item_page_shows_the_cover_square(admin_client, db):
    vinyl_id = _insert_item(
        db, title="Cover Shape Music Page", isbn="9780000000311",
        media_type="vinyl", cover_path=VINYL_COVER,
    )
    db.commit()

    html = admin_client.get(f"/music/item/{vinyl_id}").text
    m = re.search(r'<img\b[^>]*data-testid="music-item-cover"[^>]*>', html, re.S)
    assert m, "the Music item page renders no cover <img>"
    assert f'src="/{VINYL_COVER}"' in m.group(0)
    assert _shape_in(m.group(0)) == "square"


def test_music_item_page_without_a_cover_renders_no_cover_img(admin_client, db):
    vinyl_id = _insert_item(
        db, title="Coverless Music Page", isbn="9780000000312",
        media_type="vinyl",
    )
    db.commit()

    html = admin_client.get(f"/music/item/{vinyl_id}").text
    assert "Coverless Music Page" in html  # the page itself rendered
    assert not re.search(r'<img\b[^>]*data-testid="music-item-cover"', html)
    assert not re.search(r'<[^>]*\bdata-cover-shape=', html)
