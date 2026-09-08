"""Regression tests for publication-level periodicals and concrete issues."""

from app.config import MEDIA_TYPES, PERIODICAL_MEDIA_TYPES
from app.services import periodical_records, periodicals
from app.services.item_write import insert_item


POPULAR_SCIENCE_EAN = "9770161737008"


def test_magazine_is_a_first_class_periodical_media_type():
    assert PERIODICAL_MEDIA_TYPES == {"magazine"}
    assert PERIODICAL_MEDIA_TYPES <= MEDIA_TYPES.keys()
    assert MEDIA_TYPES["magazine"] == "Magazine"


def test_periodical_tables_come_from_migration_tables(db):
    """MIGRATION_TABLES creates both tables, and replaying it is harmless."""
    from app.database import MIGRATION_TABLES

    db.executescript(MIGRATION_TABLES)
    names = {
        row["name"]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    assert {"periodical_publications", "periodical_issues"} <= names


def test_publication_identity_is_separate_from_issue_items(db):
    publication_id = periodical_records.upsert_publication(
        db,
        title="Popular Science",
        issn="01617370",
        publisher="Bonnier",
        language="en",
    )
    jan_id = insert_item(db, title="Popular Science — January", media_type="magazine")
    feb_id = insert_item(db, title="Popular Science — February", media_type="magazine")

    periodical_records.link_issue(
        db,
        item_id=jan_id,
        publication_id=publication_id,
        issue_number="1",
        issue_date="2026-01-01",
    )
    periodical_records.link_issue(
        db,
        item_id=feb_id,
        publication_id=publication_id,
        issue_number="2",
        issue_date="2026-02-01",
    )

    publication = db.execute(
        "SELECT title, issn FROM periodical_publications WHERE id = ?",
        (publication_id,),
    ).fetchone()
    assert tuple(publication) == ("Popular Science", "0161-7370")
    assert [issue["item_id"] for issue in periodical_records.issues_for_publication(db, publication_id)] == [
        feb_id,
        jan_id,
    ]


def test_same_issue_number_is_allowed_in_different_volumes(db):
    publication_id = periodical_records.upsert_publication(db, title="Journal")
    first = insert_item(db, title="Journal v1 n1", media_type="magazine")
    second = insert_item(db, title="Journal v2 n1", media_type="magazine")

    periodical_records.link_issue(
        db, item_id=first, publication_id=publication_id, volume="1", issue_number="1"
    )
    periodical_records.link_issue(
        db, item_id=second, publication_id=publication_id, volume="2", issue_number="1"
    )

    assert periodical_records.find_duplicate_issue(
        db, publication_id, volume="1", issue_number="1"
    ) == first
    assert periodical_records.find_duplicate_issue(
        db, publication_id, volume="2", issue_number="1"
    ) == second


def test_full_977_barcode_plus_supplement_identifies_concrete_issue(db):
    serial = periodicals.parse_barcode(POPULAR_SCIENCE_EAN + "05")
    assert serial is not None

    publication_id = periodical_records.upsert_publication(
        db, title="Popular Science", issn=serial.issn
    )
    item_id = insert_item(db, title="Popular Science issue", media_type="magazine")
    periodical_records.link_issue(
        db,
        item_id=item_id,
        publication_id=publication_id,
        barcode_ean=serial.ean13,
        barcode_supplement=serial.supplement,
    )

    assert periodical_records.find_duplicate_issue(
        db,
        publication_id,
        barcode_ean=serial.ean13,
        barcode_supplement=serial.supplement,
    ) == item_id


def test_977_variant_is_not_guessed_as_issue_number(db):
    serial = periodicals.parse_barcode(POPULAR_SCIENCE_EAN)
    assert serial is not None
    assert serial.variant == "00"

    publication_id = periodical_records.upsert_publication(
        db, title="Popular Science", issn=serial.issn
    )
    item_id = insert_item(db, title="Popular Science unknown issue", media_type="magazine")
    periodical_records.link_issue(
        db,
        item_id=item_id,
        publication_id=publication_id,
        barcode_ean=serial.ean13,
    )

    row = db.execute(
        "SELECT issue_number, barcode_ean FROM periodical_issues WHERE item_id = ?",
        (item_id,),
    ).fetchone()
    assert row["issue_number"] is None
    assert row["barcode_ean"] == POPULAR_SCIENCE_EAN
