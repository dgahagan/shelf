"""Normalizers for CSV exports from reading-tracker apps (Goodreads, StoryGraph).

The import endpoint lowercases headers and replaces spaces with underscores
before these functions see a row, so keys here match that convention
(e.g. "Exclusive Shelf" -> "exclusive_shelf", "ISBN/UID" -> "isbn/uid").

Each normalizer returns the shelf-native shape consumed by the CSV import:

    {
        "title": str,
        "authors": str | None,
        "isbn": str | None,          # validated 10/13-digit
        "media_type": str,           # book / ebook / audiobook
        "publisher": str | None,
        "publish_year": str | None,  # numeric string, endpoint coerces
        "page_count": str | None,
        "series_name": str | None,
        "reading_status": str | None,   # read / reading / want_to_read
        "date_finished": str | None,    # ISO date
        "owned": bool,
    }
"""

import re

GOODREADS = "goodreads"
STORYGRAPH = "storygraph"
GENERIC = "generic"

# Goodreads embeds series in the title: "The Gray Man (Gray Man, #1)".
# Multi-series titles look like "(Series A, #1; Series B, #4)" — we take the
# first pairing and discard the rest.
_SERIES_TITLE_RE = re.compile(
    r"^(?P<title>.+?)\s*"
    r"\((?P<series>[^()#;]+?),?\s+#(?P<pos>\d+(?:\.\d+)?)(?:;[^)]*)?\)\s*$"
)


def split_series_title(raw: str | None) -> tuple[str, str | None, float | None]:
    """Split a Goodreads-style title into (title, series_name, position)."""
    raw = (raw or "").strip()
    m = _SERIES_TITLE_RE.match(raw)
    if not m:
        return raw, None, None
    pos = float(m.group("pos"))
    return m.group("title").strip(), m.group("series").strip(), pos


def detect_format(fieldnames) -> str:
    """Identify the CSV source from its (normalized) header columns."""
    fields = set(fieldnames or [])
    if "exclusive_shelf" in fields:
        return GOODREADS
    if "read_status" in fields:
        return STORYGRAPH
    return GENERIC


def _clean_isbn(value: str | None) -> str | None:
    """Strip Goodreads' Excel wrapper (="...") and validate length.

    Returns a bare 10- or 13-character ISBN, or None. StoryGraph's ISBN/UID
    column can hold non-ISBN UIDs for some editions — those are dropped too.
    """
    if not value:
        return None
    v = value.strip().lstrip("=").strip('"').strip()
    v = v.replace("-", "").replace(" ", "")
    if len(v) == 13 and v.isdigit():
        return v
    if len(v) == 10 and v[:9].isdigit() and (v[9].isdigit() or v[9] in "Xx"):
        return v.upper()
    return None


def _clean_date(value: str | None) -> str | None:
    """Normalize YYYY/MM/DD (both apps' export format) to ISO YYYY-MM-DD."""
    if not value:
        return None
    v = value.strip().replace("/", "-")
    parts = v.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts) and len(parts[0]) == 4:
        return f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    return None


def _media_type_from(binding: str | None) -> str:
    b = (binding or "").strip().lower()
    if "audio" in b:
        return "audiobook"
    if "kindle" in b or "ebook" in b or b == "digital":
        return "ebook"
    return "book"


_GOODREADS_SHELF_STATUS = {
    "read": "read",
    "currently-reading": "reading",
    "to-read": "want_to_read",
}

_STORYGRAPH_STATUS = {
    "read": "read",
    "currently-reading": "reading",
    "to-read": "want_to_read",
    # did-not-finish intentionally maps to no status
}


def normalize_goodreads(row: dict) -> dict:
    authors = (row.get("author") or "").strip() or None
    additional = (row.get("additional_authors") or "").strip()
    if authors and additional:
        authors = f"{authors}, {additional}"

    shelf = (row.get("exclusive_shelf") or "").strip().lower()
    status = _GOODREADS_SHELF_STATUS.get(shelf)

    title, series_name, series_position = split_series_title(row.get("title"))

    return {
        "title": title,
        "authors": authors,
        "isbn": _clean_isbn(row.get("isbn13")) or _clean_isbn(row.get("isbn")),
        "media_type": _media_type_from(row.get("binding")),
        "publisher": (row.get("publisher") or "").strip() or None,
        "publish_year": (row.get("year_published") or "").strip() or None,
        "page_count": (row.get("number_of_pages") or "").strip() or None,
        "series_name": series_name,
        "series_position": series_position,
        "reading_status": status,
        "date_finished": _clean_date(row.get("date_read")),
        # Goodreads tracks reading, not possession — trust its Owned Copies
        # count instead of assuming everything on a shelf is on a shelf.
        "owned": (row.get("owned_copies") or "").strip().isdigit()
                 and int(row["owned_copies"]) > 0,
    }


def normalize_storygraph(row: dict) -> dict:
    owned_raw = (row.get("owned?") or "").strip().lower()

    status = _STORYGRAPH_STATUS.get((row.get("read_status") or "").strip().lower())

    return {
        "title": (row.get("title") or "").strip(),
        "authors": (row.get("authors") or "").strip() or None,
        "isbn": _clean_isbn(row.get("isbn/uid")),
        "media_type": _media_type_from(row.get("format")),
        "publisher": None,
        "publish_year": None,
        "page_count": None,
        "series_name": None,
        "series_position": None,
        "reading_status": status,
        "date_finished": _clean_date(row.get("last_date_read")),
        # Only an explicit "No" marks the book as not owned (wishlist)
        "owned": owned_raw != "no",
    }


def normalize_generic(row: dict) -> dict:
    """Shelf's own CSV format — mirrors the pre-existing import behavior."""
    return {
        "title": (row.get("title") or "").strip(),
        "authors": (row.get("authors") or row.get("author") or "").strip() or None,
        "isbn": (row.get("isbn") or "").strip() or None,
        "media_type": (row.get("media_type") or "book").strip(),
        "publisher": (row.get("publisher") or "").strip() or None,
        "publish_year": (row.get("publish_year") or row.get("year") or "").strip() or None,
        "page_count": (row.get("page_count") or row.get("pages") or "").strip() or None,
        "series_name": (row.get("series_name") or row.get("series") or "").strip() or None,
        "series_position": None,
        "reading_status": None,
        "date_finished": None,
        "owned": True,
    }


NORMALIZERS = {
    GOODREADS: normalize_goodreads,
    STORYGRAPH: normalize_storygraph,
    GENERIC: normalize_generic,
}
