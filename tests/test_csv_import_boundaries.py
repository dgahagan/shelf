"""Regression coverage for CSV import data boundaries."""

import io

from tests.conftest import _insert_item


def _import_csv(client, content: str, mode: str = "skip"):
    response = client.post(
        "/api/import/csv",
        files={"file": ("import.csv", io.BytesIO(content.encode()), "text/csv")},
        data={"mode": mode},
    )
    assert response.status_code == 200
    return response.json()


def test_isbn10_import_matches_existing_isbn13(admin_client, db):
    _insert_item(db, title="Dune", isbn="9780441172719", isbn10="0441172717")
    db.commit()

    result = _import_csv(
        admin_client,
        "title,authors,isbn,media_type\nDune,Frank Herbert,0441172717,book\n",
    )

    assert result["imported"] == 0
    assert result["skipped"] == 1
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_isbn10_import_persists_canonical_pair(admin_client, db):
    result = _import_csv(
        admin_client,
        "title,authors,isbn,media_type\nDune,Frank Herbert,0441172717,book\n",
    )

    assert result["imported"] == 1
    row = db.execute("SELECT isbn, isbn10 FROM items WHERE title = 'Dune'").fetchone()
    assert row["isbn"] == "9780441172719"
    assert row["isbn10"] == "0441172717"


def test_bad_checksum_isbn_is_rejected_not_dropped(admin_client, db):
    result = _import_csv(
        admin_client,
        "title,authors,isbn,media_type\nDune,Frank Herbert,9780441172718,book\n",
    )

    assert result["imported"] == 0
    assert result["errors"] == ["Row 2: invalid ISBN"]
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_invalid_import_mode_is_rejected(admin_client, db):
    result = _import_csv(
        admin_client,
        "title,authors,isbn,media_type\nDune,Frank Herbert,9780441172719,book\n",
        mode="overwrite",
    )

    assert result["error"] == "Invalid import mode"
    assert result["imported"] == 0
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_invalid_media_type_is_rejected(admin_client, db):
    result = _import_csv(
        admin_client,
        "title,authors,isbn,media_type\nDune,Frank Herbert,,vhs\n",
    )

    assert result["imported"] == 0
    assert result["errors"] == ["Row 2: invalid media_type"]
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
