"""Regression coverage for the standalone Manga media-type decision (#102)."""

from app.config import BOOK_MEDIA_TYPES, MEDIA_TYPES
from app.routers import items_catalog, series
from app.services import cover_queue, detect


def test_manga_is_a_first_class_book_family_media_type():
    assert MEDIA_TYPES["manga"] == "Manga"
    assert "manga" in BOOK_MEDIA_TYPES
    assert items_catalog.BOOK_MEDIA_TYPES is BOOK_MEDIA_TYPES
    assert set(series.UNASSIGNED_MEDIA_TYPES) == BOOK_MEDIA_TYPES
    assert detect._BOOK_FAMILY_HINTS == BOOK_MEDIA_TYPES
    assert "manga" in cover_queue.COVER_REQUEUE_MEDIA_TYPES


def test_isbn_scan_preserves_an_explicit_manga_choice():
    result = detect.detect_media_type("isbn", "manga", None, None)

    assert result.media_type == "manga"
    assert result.signal == "detected"
    assert "Manga" in result.reason
