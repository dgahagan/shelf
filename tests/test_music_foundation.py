"""Upstream-facing tests for Shelf's first-class music foundation."""

from app.config import MEDIA_TYPES, MUSIC_MEDIA_TYPES
from app.routers import music
from app.services import music_catalog, musicbrainz
from app.services.item_write import insert_item


_SAMPLE_RELEASE = {
    "title": "The Dark Side of the Moon",
    "artist_credit": "Pink Floyd",
    "musicbrainz_release_id": "11111111-1111-1111-1111-111111111111",
    "musicbrainz_release_group_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "release_type": "Album",
    "release_status": "Official",
    "release_date": "1973-03-23",
    "first_release_date": "1973-03-01",
    "country": "GB",
    "label": "Harvest",
    "catalog_number": "SHVL 804",
    "barcode": None,
    "packaging": "Gatefold Cover",
    "media_count": 1,
    "format_summary": "12\" Vinyl",
    "source": "musicbrainz",
    "media": [
        {
            "position": 1,
            "format": "12\" Vinyl",
            "title": None,
            "track_count": 2,
            "tracks": [
                {
                    "position": 1,
                    "number": "A1",
                    "title": "Speak to Me",
                    "artist_credit": "Pink Floyd",
                    "duration_ms": 65000,
                    "musicbrainz_recording_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                },
                {
                    "position": 2,
                    "number": "B1",
                    "title": "Money",
                    "artist_credit": "Pink Floyd",
                    "duration_ms": 382000,
                    "musicbrainz_recording_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                },
            ],
        }
    ],
}


def test_music_media_family_reuses_existing_cd_type():
    assert MUSIC_MEDIA_TYPES == {"vinyl", "cassette", "cd", "digital_music"}
    assert MUSIC_MEDIA_TYPES <= MEDIA_TYPES.keys()
    assert "music_other" not in MEDIA_TYPES
    assert MEDIA_TYPES["cd"] == "CD"


def test_unknown_musicbrainz_format_requires_user_choice():
    release = {"format_summary": "MiniDisc", "media": [{"format": "MiniDisc"}]}
    assert music._infer_media_type(release) is None


def test_music_tables_come_from_migration_tables_and_keep_track_numbers_as_text(db):
    """MIGRATION_TABLES creates the music tables; replaying it is harmless."""
    from app.database import MIGRATION_TABLES

    db.executescript(MIGRATION_TABLES)

    names = {
        row["name"]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    assert {"music_releases", "music_media", "music_tracks", "music_identifiers"} <= names
    columns = {
        row["name"]: row["type"]
        for row in db.execute("PRAGMA table_info(music_tracks)").fetchall()
    }
    assert columns["number"].upper() == "TEXT"

    release_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(music_releases)").fetchall()
    }
    assert "media_condition" not in release_columns
    assert "condition_notes" not in release_columns


def test_musicbrainz_normalisation_preserves_vinyl_side_numbering():
    raw = {
        "id": _SAMPLE_RELEASE["musicbrainz_release_id"],
        "title": _SAMPLE_RELEASE["title"],
        "artist-credit": [
            {"name": "Pink Floyd", "artist": {"name": "Pink Floyd"}, "joinphrase": ""}
        ],
        "status": "Official",
        "date": "1973-03-23",
        "country": "GB",
        "packaging": "Gatefold Cover",
        "release-group": {
            "id": _SAMPLE_RELEASE["musicbrainz_release_group_id"],
            "primary-type": "Album",
            "first-release-date": "1973-03-01",
        },
        "label-info": [
            {"catalog-number": "SHVL 804", "label": {"name": "Harvest"}}
        ],
        "media": [
            {
                "position": 1,
                "format": "12\" Vinyl",
                "track-count": 2,
                "tracks": [
                    {
                        "position": 1,
                        "number": "A1",
                        "title": "Speak to Me",
                        "length": 65000,
                        "recording": {"id": "rec-a", "title": "Speak to Me"},
                    },
                    {
                        "position": 2,
                        "number": "B1",
                        "title": "Money",
                        "length": 382000,
                        "recording": {"id": "rec-b", "title": "Money"},
                    },
                ],
            }
        ],
    }

    release = musicbrainz.normalise_release(raw)
    assert release["musicbrainz_release_group_id"] == raw["release-group"]["id"]
    assert release["artist_credit"] == "Pink Floyd"
    assert release["catalog_number"] == "SHVL 804"
    assert [track["number"] for track in release["media"][0]["tracks"]] == ["A1", "B1"]


def test_release_media_tracks_and_identifiers_round_trip(db):
    item_id = insert_item(
        db,
        title=_SAMPLE_RELEASE["title"],
        authors="Pink Floyd",
        media_type="vinyl",
        source="musicbrainz",
    )
    music_catalog.save_release(db, item_id, _SAMPLE_RELEASE)
    music_catalog.add_identifier(
        db, item_id, "matrix_runout", "SHVL 804 A-2", "Side A"
    )

    release = music_catalog.get_release(db, item_id)
    assert release["catalog_number"] == "SHVL 804"
    assert release["media"][0]["format"] == "12\" Vinyl"
    assert [track["number"] for track in release["media"][0]["tracks"]] == ["A1", "B1"]
    assert release["identifiers"][0]["value"] == "SHVL 804 A-2"


def test_same_release_group_links_different_formats(db):
    vinyl_id = insert_item(db, title="Album", media_type="vinyl")
    cd_id = insert_item(db, title="Album", media_type="cd")

    vinyl = dict(_SAMPLE_RELEASE)
    cd = dict(_SAMPLE_RELEASE)
    cd["musicbrainz_release_id"] = "22222222-2222-2222-2222-222222222222"
    cd["format_summary"] = "CD"
    cd["media"] = []

    music_catalog.save_release(db, vinyl_id, vinyl)
    music_catalog.save_release(db, cd_id, cd)

    a, b = sorted((vinyl_id, cd_id))
    link = db.execute(
        "SELECT link_type FROM item_links WHERE item_a_id = ? AND item_b_id = ?",
        (a, b),
    ).fetchone()
    assert link is not None
    assert link["link_type"] == "format"


def test_cover_art_archive_is_declared_with_the_other_cover_domains():
    """CAA is allow-listed in covers.py, not added by an import side effect.

    #109 shipped a music_covers.register_cover_art_archive() called from
    app/routers/__init__.py, so importing the router package mutated the
    cover allow-list. The domain is now declared beside the other twelve.
    """
    from app.services import covers

    assert "coverartarchive.org" in covers.ALLOWED_COVER_DOMAINS


def test_music_routes_reachable_through_main():
    """Music survives the move off the package-init composition."""
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/music" in paths
    assert "/api/music/add" in paths
