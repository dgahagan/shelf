"""Tests for Pydantic model validation."""

import pytest
from pydantic import ValidationError

from app.models import ScanRequest, ItemCreate, ItemUpdate, LocationCreate


class TestScanRequest:
    def test_requires_isbn(self):
        with pytest.raises(ValidationError):
            ScanRequest()

    def test_defaults_media_type_to_book(self):
        req = ScanRequest(isbn="9780000000001")
        assert req.media_type == "book"

    def test_custom_media_type(self):
        req = ScanRequest(isbn="9780000000001", media_type="dvd")
        assert req.media_type == "dvd"


class TestItemCreate:
    def test_all_optional_fields_as_none(self):
        item = ItemCreate(title="Test")
        assert item.subtitle is None
        assert item.authors is None
        assert item.isbn is None
        assert item.publisher is None
        assert item.publish_year is None
        assert item.page_count is None
        assert item.description is None
        assert item.location_id is None
        assert item.platform is None

    def test_defaults_media_type_to_book(self):
        item = ItemCreate(title="Test")
        assert item.media_type == "book"


class TestItemUpdate:
    def test_title_can_be_none(self):
        update = ItemUpdate()
        assert update.title is None

    def test_media_type_can_be_none(self):
        update = ItemUpdate()
        assert update.media_type is None


class TestLocationCreate:
    def test_defaults_sort_order_to_zero(self):
        loc = LocationCreate(name="Shelf A")
        assert loc.sort_order == 0

    def test_custom_sort_order(self):
        loc = LocationCreate(name="Shelf B", sort_order=5)
        assert loc.sort_order == 5

    def test_requires_name(self):
        with pytest.raises(ValidationError):
            LocationCreate()
