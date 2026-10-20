"""barcode_resolve.resolve — copy barcode first, then the ISBN and UPC reads."""

from app.services import barcode_resolve, item_copies
from app.services import upc as upc_svc
from app.services.item_write import trash_item
from tests.conftest import _insert_item, _insert_location


def _copy(db, item_id, number, barcode, location_id=None, is_primary=0):
    return item_copies.insert_copy(db, {
        "item_id": item_id, "copy_number": number, "copy_barcode": barcode,
        "location_id": location_id, "is_primary": is_primary,
    })


def test_copy_barcode_resolves_to_its_item_and_copy(db):
    loft = _insert_location(db, "Loft")
    item_id = _insert_item(db, title="Dune")
    _copy(db, item_id, 1, "SHF-000001", is_primary=1)
    copy_id = _copy(db, item_id, 2, "SHF-000002", location_id=loft)
    db.commit()

    match = barcode_resolve.resolve("SHF-000002")

    assert match["id"] == item_id and match["title"] == "Dune"
    assert match["copy_id"] == copy_id
    assert match["copy_number"] == 2
    assert match["copy_location_id"] == loft
    assert match["copy_location_name"] == "Loft"
    assert match["copy_in_trash"] is False
    assert match["deleted_at"] is None


def test_isbn_shaped_copy_barcode_names_the_copy_not_the_isbn(db):
    book = _insert_item(db, title="Owner of the ISBN", isbn="9780000000026")
    other = _insert_item(db, title="Labelled copy", isbn="9780306406157")
    copy_id = _copy(db, other, 1, "9780000000026")
    db.commit()

    match = barcode_resolve.resolve("9780000000026")

    assert match["id"] == other != book
    assert match["copy_id"] == copy_id


def test_trashed_items_copy_barcode_returns_the_item_in_trash(db):
    item_id = _insert_item(db, title="Gone")
    _copy(db, item_id, 1, "SHF-000003", is_primary=1)
    assert trash_item(db, item_id)
    db.commit()

    match = barcode_resolve.resolve("SHF-000003")

    assert match["id"] == item_id
    assert match["deleted_at"] is not None
    assert match["copy_in_trash"] is False


def test_trashed_copy_of_a_live_item_is_flagged(db):
    item_id = _insert_item(db, title="Two copies")
    _copy(db, item_id, 1, "SHF-000004", is_primary=1)
    second = _copy(db, item_id, 2, "SHF-000005")
    assert item_copies.trash_copy(db, second)
    db.commit()

    match = barcode_resolve.resolve("SHF-000005")

    assert match["copy_id"] == second
    assert match["copy_in_trash"] is True
    assert match["deleted_at"] is not None
    # The live copy's label still resolves live.
    assert barcode_resolve.resolve("SHF-000004")["deleted_at"] is None


def test_unknown_string_is_none(db):
    assert barcode_resolve.resolve("SHF-999999") is None


def test_isbn_and_upc_still_resolve_without_copy_keys(db):
    book = _insert_item(db, title="By ISBN", isbn="9780306406157")
    upc = upc_svc.normalize_upc("012345678905")
    disc = _insert_item(db, title="By UPC", isbn=None, upc=upc, media_type="dvd")
    db.commit()

    by_isbn = barcode_resolve.resolve("0306406152")
    by_upc = barcode_resolve.resolve("012345678905")

    assert by_isbn["id"] == book
    assert by_upc["id"] == disc
    for match in (by_isbn, by_upc):
        assert match["copy_id"] is None and match["copy_number"] is None
        assert match["copy_location_id"] is None and match["copy_location_name"] is None
        assert match["copy_in_trash"] is False
