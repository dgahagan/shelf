"""Contracts for `collection_value.owned_total`."""

from app.services import collection_value, item_write
from tests.conftest import _insert_item


def test_empty_library_is_zero(db):
    assert collection_value.owned_total(db) == 0


def test_manual_value_overrides_estimate(db):
    _insert_item(db, isbn="9780000000026", manual_value=30.0, estimated_value=10.0)
    assert collection_value.owned_total(db) == 30.0


def test_falls_back_to_estimate(db):
    _insert_item(db, isbn="9780000000026", estimated_value=12.5)
    _insert_item(db, isbn="9780000000033", manual_value=7.5)
    assert collection_value.owned_total(db) == 20.0


def test_excludes_wishlist_rows(db):
    _insert_item(db, isbn="9780000000026", manual_value=10.0)
    _insert_item(db, isbn="9780000000033", manual_value=99.0, owned=0, wishlisted=True)
    assert collection_value.owned_total(db) == 10.0


def test_excludes_trashed_rows(db):
    _insert_item(db, isbn="9780000000026", manual_value=10.0)
    gone = _insert_item(db, isbn="9780000000033", manual_value=99.0)
    item_write.trash_item(db, gone)
    assert collection_value.owned_total(db) == 10.0
