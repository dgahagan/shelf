"""app/services/buy_links.py: the affiliate link builder."""
from urllib.parse import parse_qs, urlsplit

from app.services import buy_links
from app.services.buy_links import BuyLinkConfig
from tests.conftest import _insert_item

ISBN13 = "9781250145260"


def _cfg(**kw):
    base = dict(bookshop_id="513", bookshop_host="bookshop.org",
                amazon_tag="shelf-20", amazon_host="www.amazon.com")
    base.update(kw)
    return BuyLinkConfig(**base)


def _set(db, key, value):
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
        (key, value, value),
    )
    db.commit()


def _q(url):
    return parse_qs(urlsplit(url).query)


def test_978_book_with_both_tags(db):
    links = buy_links.for_book(_cfg(), title="Dune", authors="Frank Herbert", isbn13=ISBN13)
    assert [l.store for l in links] == ["bookshop", "amazon"]
    assert links[0].url == f"https://bookshop.org/a/513/{ISBN13}"
    assert links[1].url == "https://www.amazon.com/dp/1250145260?tag=shelf-20"
    assert all(l.deep for l in links)


def test_979_isbn_uses_amazon_search_on_the_isbn13():
    links = buy_links.for_book(_cfg(), title="T", authors="A", isbn13="9791032305690")
    assert links[0].url == "https://bookshop.org/a/513/9791032305690"
    assert links[1].url == "https://www.amazon.com/s?k=9791032305690&tag=shelf-20"
    assert links[1].deep is False


def test_no_isbn_uses_search_forms():
    links = buy_links.for_book(_cfg(), title="Dune", authors="Frank Herbert", isbn13=None)
    assert links[0].url == "https://bookshop.org/search?keywords=Dune"
    assert "513" not in links[0].url
    assert links[1].url == "https://www.amazon.com/s?k=Dune+Frank+Herbert&tag=shelf-20"


def test_only_one_tag_yields_only_that_store():
    only_amz = buy_links.for_book(_cfg(bookshop_id=None), title="D", authors="", isbn13=ISBN13)
    assert [l.store for l in only_amz] == ["amazon"]
    only_bs = buy_links.for_book(_cfg(amazon_tag=None), title="D", authors="", isbn13=ISBN13)
    assert [l.store for l in only_bs] == ["bookshop"]


def test_no_tag_yields_nothing(db):
    assert buy_links.configured(db) is None
    assert buy_links.for_book(buy_links.configured(db), title="D", authors="", isbn13=ISBN13) == []
    assert buy_links.for_book(None, title="D", authors="", isbn13=ISBN13) == []


def test_non_book_types_yield_nothing_from_for_item(db):
    _set(db, "buy_links_amazon_tag", "shelf-20")
    for mt in ("video_game", "vinyl"):
        iid = _insert_item(db, media_type=mt, isbn=ISBN13)
        db.commit()
        item = db.execute("SELECT * FROM items WHERE id = ?", (iid,)).fetchone()
        assert buy_links.for_item(db, item) == []
    iid = _insert_item(db, media_type="book", isbn=ISBN13, authors="Frank Herbert")
    db.commit()
    item = db.execute("SELECT * FROM items WHERE id = ?", (iid,)).fetchone()
    assert [l.url for l in buy_links.for_item(db, item)] == [
        "https://www.amazon.com/dp/1250145260?tag=shelf-20"]


def test_stored_tag_failing_charset_reads_as_absent(db):
    _set(db, "buy_links_amazon_tag", "bad tag&x=1")
    _set(db, "buy_links_bookshop_id", "12ab")
    assert buy_links.configured(db) is None
    _set(db, "buy_links_bookshop_id", "513")
    cfg = buy_links.configured(db)
    assert cfg.amazon_tag is None and cfg.bookshop_id == "513"


def test_stored_host_outside_allowlist_reads_as_default(db):
    _set(db, "buy_links_amazon_tag", "shelf-20")
    _set(db, "buy_links_amazon_host", "evil.example.com")
    _set(db, "buy_links_bookshop_host", "evil.example.com")
    cfg = buy_links.configured(db)
    assert cfg.amazon_host == "www.amazon.com"
    assert cfg.bookshop_host == "bookshop.org"
    _set(db, "buy_links_amazon_host", "www.amazon.de")
    assert buy_links.configured(db).amazon_host == "www.amazon.de"


def test_awkward_title_round_trips_through_the_query():
    title = 'Cats & "Dogs" #1'
    links = buy_links.for_book(_cfg(), title=title, authors="", isbn13=None)
    assert _q(links[0].url)["keywords"] == [title]
    assert _q(links[1].url)["k"] == [title]
    assert "#" not in links[0].url and '"' not in links[0].url


def test_fullmatch_rejects_trailing_newline(db):
    assert buy_links.AMAZON_TAG_RE.fullmatch("shelf-20\n") is None
    _set(db, "buy_links_amazon_tag", "shelf-20\n")
    # stripped on read, so a stray newline cannot reach a URL either way
    cfg = buy_links.configured(db)
    assert cfg is None or "\n" not in cfg.amazon_tag
    _set(db, "buy_links_amazon_tag", "shel\nf")
    assert buy_links.configured(db) is None
