import pytest

from app.services import romm_records
from app.services.item_write import insert_item


def _candidate(**overrides):
    value = {
        "romm_id": "101",
        "romm_platform_id": "1",
        "title": "Chrono Trigger",
        "platform": "snes",
        "platform_name": "Super Nintendo",
        "publisher": "Square",
        "publish_year": 1995,
        "description": "A time-travelling RPG.",
        "cover_url": "/assets/chrono.jpg",
        "source": "romm",
    }
    value.update(overrides)
    return value


def test_new_romm_candidate_creates_service_backed_game_without_location(db):
    result = romm_records.persist_candidate(db, _candidate())
    row = db.execute("SELECT * FROM items WHERE id = ?", (result["item_id"],)).fetchone()
    record = db.execute("SELECT * FROM romm_records WHERE romm_id = '101'").fetchone()

    assert result["action"] == "created"
    assert row["title"] == "Chrono Trigger"
    assert row["media_type"] == "video_game"
    assert row["platform"] == "snes"
    assert row["source"] == "romm"
    assert row["location_id"] is None
    assert record["item_id"] == row["id"]
    assert record["platform_id"] == "1"


def test_same_title_and_platform_never_absorbs_physical_game(db):
    physical_id = insert_item(
        db,
        {
            "title": "Chrono Trigger",
            "media_type": "video_game",
            "platform": "snes",
            "source": "manual",
        },
    )

    result = romm_records.persist_candidate(db, _candidate())

    assert result["item_id"] != physical_id
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM item_links").fetchone()[0] == 0


def test_unknown_romm_platform_is_registered_before_item_insert(db):
    result = romm_records.persist_candidate(
        db,
        _candidate(
            romm_id="202",
            romm_platform_id="44",
            title="Gate of Thunder",
            platform="pcenginecd",
            platform_name="PC Engine CD",
        ),
    )

    platform = db.execute(
        "SELECT name FROM game_platforms WHERE slug = 'pcenginecd'"
    ).fetchone()
    item = db.execute("SELECT platform FROM items WHERE id = ?", (result["item_id"],)).fetchone()
    assert platform["name"] == "PC Engine CD"
    assert item["platform"] == "pcenginecd"


def test_resync_updates_provider_item_without_erasing_sparse_metadata(db):
    first = romm_records.persist_candidate(db, _candidate())
    second = romm_records.persist_candidate(
        db,
        _candidate(
            title="Chrono Trigger Updated",
            publisher=None,
            description=None,
        ),
    )
    row = db.execute("SELECT * FROM items WHERE id = ?", (first["item_id"],)).fetchone()

    assert second == {"item_id": first["item_id"], "action": "updated"}
    assert row["title"] == "Chrono Trigger Updated"
    assert row["publisher"] == "Square"
    assert row["description"] == "A time-travelling RPG."


def test_platform_change_updates_item_and_service_identity(db):
    first = romm_records.persist_candidate(db, _candidate())
    romm_records.persist_candidate(
        db,
        _candidate(
            romm_platform_id="44",
            platform="pcenginecd",
            platform_name="PC Engine CD",
        ),
    )

    row = db.execute("SELECT platform FROM items WHERE id = ?", (first["item_id"],)).fetchone()
    record = romm_records.records_for_item(db, first["item_id"])[0]
    assert row["platform"] == "pcenginecd"
    assert record["platform_id"] == "44"


def test_detaching_romm_holding_keeps_catalogue_item(db):
    result = romm_records.persist_candidate(db, _candidate())
    romm_records.detach_record(db, "101")

    assert db.execute("SELECT COUNT(*) FROM romm_records").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM items WHERE id = ?", (result["item_id"],)).fetchone()[0] == 1


def test_existing_romm_identity_attached_to_manual_item_is_not_taken_over(db):
    item_id = insert_item(
        db,
        {
            "title": "Manual Game",
            "media_type": "video_game",
            "platform": "snes",
            "source": "manual",
        },
    )
    romm_records.ensure_schema(db)
    db.execute(
        "INSERT INTO romm_records (romm_id, item_id, platform_id) VALUES ('101', ?, '1')",
        (item_id,),
    )

    with pytest.raises(romm_records.RomMPersistenceError, match="non-RomM"):
        romm_records.persist_candidate(db, _candidate())

    row = db.execute("SELECT title, source FROM items WHERE id = ?", (item_id,)).fetchone()
    assert dict(row) == {"title": "Manual Game", "source": "manual"}


def test_missing_identity_fields_are_rejected(db):
    with pytest.raises(romm_records.RomMPersistenceError):
        romm_records.persist_candidate(db, _candidate(romm_id=""))
