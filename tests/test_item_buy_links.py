"""Item detail: buy links under a wishlisted book's title.

Every arm of the template condition is reached (G65): wishlisted or not,
a book type or not, tags set or not, the feature on or off.
"""
from tests.conftest import _insert_item
from tests.test_features_gate import off

ISBN13 = "9781250145260"
ISBN10 = "1250145260"
DISCLOSURE = "Affiliate links carrying your own tags"
BOOKSHOP_SENTENCE = "Bookshop.org supports independent bookstores."


def _set(db, key, value):
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
        (key, value, value),
    )


def _both(db):
    _set(db, "buy_links_bookshop_id", "513")
    _set(db, "buy_links_amazon_tag", "shelf-20")


def _wish(db, **kw):
    fields = dict(title="Dune", isbn=ISBN13, owned=0, wishlisted=True)
    fields.update(kw)
    return _insert_item(db, **fields)


def _page(client, db, item_id):
    db.commit()  # G48
    return client.get(f"/item/{item_id}").text


def test_both_tags_render_both_links_and_disclosure(viewer_client, db):
    _both(db)
    html = _page(viewer_client, db, _wish(db))
    assert 'data-testid="buy-links"' in html
    assert f'href="https://bookshop.org/a/513/{ISBN13}"' in html
    assert f'href="https://www.amazon.com/dp/{ISBN10}?tag=shelf-20"' in html
    assert 'data-testid="buy-link-bookshop"' in html
    assert 'data-testid="buy-link-amazon"' in html
    assert html.count('rel="noopener sponsored"') >= 2
    assert html.count('target="_blank"') >= 2
    assert 'data-testid="buy-links-disclosure"' in html
    assert DISCLOSURE in html
    assert BOOKSHOP_SENTENCE in html


def test_amazon_only_has_no_bookshop_sentence(viewer_client, db):
    _set(db, "buy_links_amazon_tag", "shelf-20")
    html = _page(viewer_client, db, _wish(db))
    assert 'data-testid="buy-link-amazon"' in html
    assert 'data-testid="buy-link-bookshop"' not in html
    assert DISCLOSURE in html
    assert BOOKSHOP_SENTENCE not in html


def test_owned_book_renders_none(viewer_client, db):
    _both(db)
    iid = _insert_item(db, title="Dune", isbn=ISBN13, owned=1, wishlisted=False)
    assert 'data-testid="buy-links"' not in _page(viewer_client, db, iid)


def test_wishlisted_video_game_renders_none(viewer_client, db):
    _both(db)
    iid = _wish(db, title="Halo", media_type="video_game")
    assert 'data-testid="buy-links"' not in _page(viewer_client, db, iid)


def test_no_tags_renders_none(viewer_client, db):
    assert 'data-testid="buy-links"' not in _page(viewer_client, db, _wish(db))


def test_flag_off_renders_none(viewer_client, db):
    _both(db)
    iid = _wish(db)
    db.commit()
    off("buy_links")
    assert 'data-testid="buy-links"' not in viewer_client.get(f"/item/{iid}").text


def test_list_price_line_unchanged_beside_buy_links(viewer_client, db):
    _both(db)
    iid = _wish(db)
    db.execute(
        "INSERT INTO price_history (item_id, price, observed_at) VALUES (?, ?, ?)",
        (iid, 12.99, "2026-09-02 03:00:00"),
    )
    html = _page(viewer_client, db, iid)
    assert 'data-testid="list-price-line"' in html
    assert "List price $12.99" in html
    assert 'data-testid="buy-links"' in html


def test_title_with_script_is_escaped_in_href(viewer_client, db):
    _set(db, "buy_links_bookshop_id", "513")
    iid = _wish(db, title="<script>alert(1)</script>", isbn=None)
    html = _page(viewer_client, db, iid)
    assert 'data-testid="buy-link-bookshop"' in html
    assert "<script>alert(1)" not in html
