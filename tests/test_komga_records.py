import pytest

from app.services import komga_records
from app.services.item_write import insert_item


def _candidate(**overrides):
    value = {
        "komga_id": "book-1",
        "komga_library_id": "library-1",
        "komga_series_id": "series-1",
        "library_kind": "manga",
        "title": "Example Manga",
        "authors": "Example Author",
        "isbn": "9781974700523",
        "series_name": "Example Series",
        "series_position": 1.0,
        "publish_year": 2020,
        "description": "Komga description",
        "page_count": 192,
    }
    value.update(overrides)
    return value


def test_new_manga_candidate_creates_catalogue_item_and_holding(db):
    result = komga_records.persist_candidate(db, _candidate())

    item = db.execute("SELECT * FROM items WHERE id = ?", (result["item_id"],)).fetchone()
    holding = db.execute(
        "SELECT * FROM komga_records WHERE komga_id = 'book-1'"
    ).fetchone()

    assert result["action"] == "created"
    assert result["adopted"] is False
    assert item["media_type"] == "manga"
    assert item["source"] == "komga"
    assert item["location_id"] is None
    assert item["isbn"] == "9781974700523"
    assert holding["item_id"] == item["id"]
    assert holding["kind"] == "manga"
    assert holding["library_id"] == "library-1"


def test_exact_isbn_match_adopts_existing_catalogue_item_without_changing_source(db):
    existing_id = insert_item(
        db,
        {
            "title": "My preferred title",
            "isbn": "9781974700523",
            "media_type": "manga",
            "source": "manual",
            "description": "My own description",
        },
    )

    result = komga_records.persist_candidate(db, _candidate())
    row = db.execute("SELECT * FROM items WHERE id = ?", (existing_id,)).fetchone()

    assert result == {"item_id": existing_id, "action": "adopted", "adopted": True}
    assert row["title"] == "My preferred title"
    assert row["description"] == "My own description"
    assert row["source"] == "manual"
    assert row["authors"] == "Example Author"
    assert komga_records.records_for_item(db, existing_id)[0]["komga_id"] == "book-1"


def test_isbn10_from_provider_matches_existing_canonical_isbn13(db):
    existing_id = insert_item(
        db,
        {
            "title": "Existing Comic",
            "isbn": "9781582406725",
            "media_type": "comic",
            "source": "manual",
        },
    )

    result = komga_records.persist_candidate(
        db,
        _candidate(
            library_kind="comic",
            title="Provider Comic",
            isbn="1582406723",
        ),
    )

    assert result["item_id"] == existing_id
    assert result["action"] == "adopted"
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_invalid_provider_isbn_is_dropped_instead_of_rejecting_import(db):
    result = komga_records.persist_candidate(db, _candidate(isbn="not-an-isbn"))
    row = db.execute("SELECT isbn FROM items WHERE id = ?", (result["item_id"],)).fetchone()
    assert row["isbn"] is None


def test_title_only_match_is_not_silently_adopted(db):
    existing_id = insert_item(
        db,
        {
            "title": "Same Title",
            "media_type": "manga",
            "source": "manual",
        },
    )

    result = komga_records.persist_candidate(
        db,
        _candidate(title="Same Title", isbn=None),
    )

    assert result["action"] == "created"
    assert result["item_id"] != existing_id
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2


def test_resync_updates_komga_created_item_without_erasing_omitted_metadata(db):
    first = komga_records.persist_candidate(db, _candidate())
    second = komga_records.persist_candidate(
        db,
        _candidate(
            title="Updated Manga Title",
            authors=None,
            description=None,
            komga_library_id="library-2",
        ),
    )
    row = db.execute("SELECT * FROM items WHERE id = ?", (first["item_id"],)).fetchone()
    holding = komga_records.records_for_item(db, first["item_id"])[0]

    assert second["action"] == "updated"
    assert second["item_id"] == first["item_id"]
    assert row["title"] == "Updated Manga Title"
    assert row["authors"] == "Example Author"
    assert row["description"] == "Komga description"
    assert holding["library_id"] == "library-2"


def test_explicit_library_reclassification_updates_komga_created_item(db):
    result = komga_records.persist_candidate(db, _candidate())
    updated = komga_records.persist_candidate(
        db,
        _candidate(library_kind="comic"),
    )

    row = db.execute("SELECT media_type FROM items WHERE id = ?", (result["item_id"],)).fetchone()
    holding = komga_records.records_for_item(db, result["item_id"])[0]
    assert updated["action"] == "updated"
    assert row["media_type"] == "comic"
    assert holding["kind"] == "comic"


def test_reclassification_never_silently_changes_manual_item(db):
    existing_id = insert_item(
        db,
        {
            "title": "Manual Manga",
            "isbn": "9781974700523",
            "media_type": "manga",
            "source": "manual",
        },
    )
    komga_records.persist_candidate(db, _candidate())

    with pytest.raises(komga_records.KomgaPersistenceError, match="manually catalogued"):
        komga_records.persist_candidate(db, _candidate(library_kind="comic"))

    row = db.execute("SELECT media_type FROM items WHERE id = ?", (existing_id,)).fetchone()
    assert row["media_type"] == "manga"


def test_detaching_komga_holding_keeps_catalogue_item(db):
    result = komga_records.persist_candidate(db, _candidate())
    komga_records.detach_record(db, "book-1")

    assert db.execute("SELECT COUNT(*) FROM komga_records").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM items WHERE id = ?", (result["item_id"],)).fetchone()[0] == 1


def test_missing_or_unknown_library_classification_is_rejected(db):
    with pytest.raises(komga_records.KomgaPersistenceError):
        komga_records.persist_candidate(db, _candidate(library_kind="unknown"))
