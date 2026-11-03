"""Affiliate buy links for books, built from the owner's own saved tags.

Safety property: every URL this module returns is assembled from three kinds of
part only. Hosts come from the ``BOOKSHOP_HOSTS`` / ``AMAZON_HOSTS``
allowlists. Tags and ids pass the charset regexes with ``fullmatch`` on
stripped input. Queries go through ``urllib.parse.urlencode``. Templates can
therefore render ``url`` with normal escaping and must never apply ``|safe``.

``for_book`` is pure and the only builder. ``configured`` re-validates whatever
is stored, so a row written outside the save route cannot reach a URL. There is
no module-level cache: every call reads settings afresh.
"""
from dataclasses import dataclass
import re
from urllib.parse import urlencode

from app.config import BOOK_MEDIA_TYPES
from app.database import get_setting
from app.services import isbn as isbn_svc

BOOKSHOP_HOSTS = ("bookshop.org", "uk.bookshop.org")
AMAZON_HOSTS = (
    "www.amazon.com", "www.amazon.co.uk", "www.amazon.ca", "www.amazon.de",
    "www.amazon.fr", "www.amazon.es", "www.amazon.it", "www.amazon.com.au",
    "www.amazon.co.jp", "www.amazon.in",
)
DEFAULT_BOOKSHOP_HOST = BOOKSHOP_HOSTS[0]
DEFAULT_AMAZON_HOST = AMAZON_HOSTS[0]
AMAZON_TAG_RE = re.compile(r"[A-Za-z0-9-]{1,40}")
BOOKSHOP_ID_RE = re.compile(r"[0-9]{1,12}")


@dataclass(frozen=True)
class BuyLink:
    store: str   # "bookshop" or "amazon"
    label: str
    url: str
    deep: bool   # True when the link goes straight to the book's page


@dataclass(frozen=True)
class BuyLinkConfig:
    bookshop_id: str | None
    bookshop_host: str
    amazon_tag: str | None
    amazon_host: str


def _clean(value, pattern):
    value = (value or "").strip()
    return value if pattern.fullmatch(value) else None


def _host(value, allowed, default):
    value = (value or "").strip()
    return value if value in allowed else default


def configured(db):
    """Read and re-validate the four settings. None when neither tag is valid."""
    bookshop_id = _clean(get_setting(db, "buy_links_bookshop_id"), BOOKSHOP_ID_RE)
    amazon_tag = _clean(get_setting(db, "buy_links_amazon_tag"), AMAZON_TAG_RE)
    if not bookshop_id and not amazon_tag:
        return None
    return BuyLinkConfig(
        bookshop_id=bookshop_id,
        bookshop_host=_host(get_setting(db, "buy_links_bookshop_host"),
                            BOOKSHOP_HOSTS, DEFAULT_BOOKSHOP_HOST),
        amazon_tag=amazon_tag,
        amazon_host=_host(get_setting(db, "buy_links_amazon_host"),
                          AMAZON_HOSTS, DEFAULT_AMAZON_HOST),
    )


def for_book(cfg, *, title, authors, isbn13):
    """Build the links for one book. Bookshop first, then Amazon."""
    if cfg is None:
        return []
    title = (title or "").strip()
    authors = (authors or "").strip()
    isbn13 = (isbn13 or "").strip() or None
    isbn10 = isbn_svc.isbn13_to_isbn10(isbn13) if isbn13 else None
    links = []

    if cfg.bookshop_id:
        if isbn13:
            url = f"https://{cfg.bookshop_host}/a/{cfg.bookshop_id}/{isbn13}"
            links.append(BuyLink("bookshop", "Bookshop.org", url, True))
        elif title:
            url = f"https://{cfg.bookshop_host}/search?" + urlencode({"keywords": title})
            links.append(BuyLink("bookshop", "Bookshop.org", url, False))

    if cfg.amazon_tag:
        if isbn10:
            url = f"https://{cfg.amazon_host}/dp/{isbn10}?" + urlencode({"tag": cfg.amazon_tag})
            links.append(BuyLink("amazon", "Amazon", url, True))
        else:
            q = isbn13 if isbn13 else f"{title} {authors}".strip()
            if q:
                url = f"https://{cfg.amazon_host}/s?" + urlencode({"k": q, "tag": cfg.amazon_tag})
                links.append(BuyLink("amazon", "Amazon", url, False))
    return links


def for_item(db, item):
    """Links for an item row; empty unless it is a book-family type."""
    if item["media_type"] not in BOOK_MEDIA_TYPES:
        return []
    cfg = configured(db)
    if cfg is None:
        return []
    pair = isbn_svc.canonical_isbn_pair(item["isbn"]) if item["isbn"] else None
    return for_book(
        cfg,
        title=item["title"],
        authors=item["authors"],
        isbn13=pair[0] if pair else None,
    )
