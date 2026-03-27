"""Tests for OpenLibrary internal helpers — _extract_description."""

from app.services.openlibrary import _extract_description


class TestExtractDescription:
    def test_string_description_returned(self):
        work = {"description": "A great book about things."}
        assert _extract_description(work) == "A great book about things."

    def test_dict_with_value_key(self):
        work = {"description": {"type": "/type/text", "value": "A dict description."}}
        assert _extract_description(work) == "A dict description."

    def test_none_input_returns_none(self):
        assert _extract_description(None) is None

    def test_empty_work_returns_none(self):
        assert _extract_description({}) is None

    def test_work_without_description_key(self):
        assert _extract_description({"title": "Some Work"}) is None
