"""Board games filed under the title printed in the library's language (#167).

BGG names a game in English ("CATAN"); each printing carries its own title
("Die Siedler von Catan"). A German library files the German box's title, so the
shelf reads like the shelf at home.
"""

import pytest

from app import config
from app.routers import items_board_games
from app.services import bgg, covers, provider_result
from app.services import tags as tags_svc
from tests.conftest import _insert_item
from tests.test_board_games import _catan_meta


def _version(version_id, title, year, *languages, cover=None):
    return {"version_id": version_id, "name": f"edition {version_id}", "title": title,
            "languages": list(languages), "publisher": None, "publish_year": year,
            "cover_url": cover, "thumbnail_url": None}


VERSIONS = [
    _version(1, "Catan", 2015, "English", cover="https://cf.geekdo-images.com/en.jpg"),
    _version(2, "Die Siedler von Catan", 1995, "German", cover="https://cf.geekdo-images.com/de1995.jpg"),
    _version(3, "Die Siedler von Catan", 2010, "German", cover="https://cf.geekdo-images.com/de2010.jpg"),
    _version(4, "Catan: Das Spiel", 2022, "German", cover="https://cf.geekdo-images.com/de2022.jpg"),
]


def _meta(versions=VERSIONS):
    return {**_catan_meta(), "title": "CATAN", "alternate_titles": ["Die Siedler von Catan"],
            "versions": versions}


@pytest.fixture
def german_library(monkeypatch):
    monkeypatch.setenv("SHELF_I18N", "on")
    monkeypatch.setenv("SHELF_DEFAULT_LANGUAGE", "de")


class TestPickingTheTitle:
    def test_the_title_most_german_printings_carry_wins_newest_box_first(self):
        # Two "Die Siedler von Catan" beat one "Catan: Das Spiel", however new.
        assert bgg.localized_version(_meta(), "de")["version_id"] == 3

    def test_a_tie_goes_to_the_newer_title(self):
        assert bgg.localized_version(_meta([VERSIONS[1], VERSIONS[3]]), "de")["version_id"] == 4

    def test_multilingual_printings_count_too(self):
        multi = _version(5, "Qwirkle", 2015, "German", "English")
        luxe = _version(6, "Qwirkle LUXE", 2023, "German")
        assert bgg.localized_version(_meta([multi, {**multi, "version_id": 7}, luxe]), "de")["title"] == "Qwirkle"

    def test_english_and_unmapped_languages_keep_bggs_name(self):
        assert bgg.localized_version(_meta(), "en") is None
        assert bgg.localized_version(_meta(), "xx") is None
        assert not bgg.wants_versions("en") and bgg.wants_versions("de")

    def test_no_printing_in_the_language_keeps_bggs_name(self):
        assert bgg.localize(_meta([VERSIONS[0]]), "de")["title"] == "CATAN"
        assert "original_title" not in bgg.localize(_meta([VERSIONS[0]]), "de")
        assert bgg.localize(_meta([]), "de")["title"] == "CATAN"

    def test_localize_takes_the_printings_title_and_box_and_keeps_the_english_name(self):
        local = bgg.localize(_meta(), "de")
        assert local["title"] == "Die Siedler von Catan"
        assert local["cover_url"] == "https://cf.geekdo-images.com/de2010.jpg"
        assert local["original_title"] == "CATAN"
        assert local["alternate_titles"] == ["CATAN"]  # the German name is not listed twice
        assert local["categories"] == _meta()["categories"]  # the rest is untouched

    def test_a_printing_without_box_art_keeps_bggs_cover(self):
        local = bgg.localize(_meta([_version(9, "Catan: Das Spiel", 2022, "German")]), "de")
        assert local["title"] == "Catan: Das Spiel"
        assert local["cover_url"] == _catan_meta()["cover_url"]


@pytest.fixture
def add_stubs(monkeypatch):
    calls = []

    async def _lookup_game(bgg_id, token, client, *, versions=False):
        calls.append(versions)
        return provider_result.found("bgg", _meta() if versions else _meta([]))

    async def _download(item_id, url, client):
        calls.append(url)
        return f"data/covers/{item_id}.jpg"

    monkeypatch.setattr(bgg, "lookup_game", _lookup_game)
    monkeypatch.setattr(covers, "_download_to_item", _download)
    monkeypatch.setattr(items_board_games, "_credentials", lambda: ("t", None))
    return calls


def _title(db, item_id=None):
    row = db.execute("SELECT title FROM items_live WHERE media_type = 'board_game' "
                     + ("AND id = ?" if item_id else "ORDER BY id DESC LIMIT 1"),
                     (item_id,) if item_id else ()).fetchone()
    return row["title"]


def _original(db, item_id=None):
    row = db.execute("SELECT original_title FROM items_live WHERE media_type = 'board_game' "
                     + ("AND id = ?" if item_id else "ORDER BY id DESC LIMIT 1"),
                     (item_id,) if item_id else ()).fetchone()
    return row["original_title"]


class TestFiling:
    def test_a_german_library_files_the_german_title_and_box(self, editor_client, db, add_stubs, german_library):
        resp = editor_client.post("/api/board-games/add", data={"bgg_id": 13})
        assert resp.status_code == 200
        assert _title(db) == "Die Siedler von Catan"
        assert _original(db) == "CATAN"
        assert add_stubs == [True, "https://cf.geekdo-images.com/de2010.jpg"]

    def test_an_english_library_asks_for_no_versions(self, editor_client, db, add_stubs):
        editor_client.post("/api/board-games/add", data={"bgg_id": 13})
        assert _title(db) == "CATAN"
        assert _original(db) is None
        assert add_stubs[0] is False


class TestRefresh:
    def test_bggs_english_name_becomes_the_german_title(self, editor_client, db, add_stubs, german_library):
        item_id = _insert_item(db, title="Catan", isbn=None, media_type="board_game", bgg_id=13,
                               cover_path="data/covers/mine.jpg")
        db.commit()
        editor_client.post(f"/api/board-games/{item_id}/refresh")
        assert _title(db, item_id) == "Die Siedler von Catan"
        assert _original(db, item_id) == "CATAN"

    def test_a_game_already_under_its_german_title_gains_the_english_name(
            self, editor_client, db, add_stubs, german_library):
        item_id = _insert_item(db, title="Die Siedler von Catan", isbn=None, media_type="board_game",
                               bgg_id=13, cover_path="data/covers/mine.jpg")
        db.commit()
        editor_client.post(f"/api/board-games/{item_id}/refresh")
        assert (_title(db, item_id), _original(db, item_id)) == ("Die Siedler von Catan", "CATAN")

    def test_a_title_the_user_typed_stays(self, editor_client, db, add_stubs, german_library):
        item_id = _insert_item(db, title="Siedler (Opas Ausgabe)", isbn=None, media_type="board_game",
                               bgg_id=13, cover_path="data/covers/mine.jpg")
        db.commit()
        editor_client.post(f"/api/board-games/{item_id}/refresh")
        assert _title(db, item_id) == "Siedler (Opas Ausgabe)"

    def test_an_original_title_the_user_typed_stays(self, editor_client, db, add_stubs, german_library):
        item_id = _insert_item(db, title="Catan", isbn=None, media_type="board_game", bgg_id=13,
                               original_title="The Settlers of Catan", cover_path="data/covers/mine.jpg")
        db.commit()
        editor_client.post(f"/api/board-games/{item_id}/refresh")
        assert _original(db, item_id) == "The Settlers of Catan"


class TestTheOriginalName:
    """The English name must stay findable: a German library searching for
    "Ticket to Ride" finds "Zug um Zug" (the review on #205)."""

    @pytest.fixture
    def zug_um_zug(self, db):
        item_id = _insert_item(db, title="Zug um Zug", isbn=None, media_type="board_game",
                               bgg_id=9209, original_title="Ticket to Ride")
        db.commit()
        return item_id

    def test_browse_search_finds_the_original_name(self, admin_client, zug_um_zug):
        assert "Zug um Zug" in admin_client.get("/api/search", params={"q": "ticket to ride"}).text
        assert "Zug um Zug" in admin_client.get("/api/search", params={"q": "Zug um"}).text

    def test_the_item_page_shows_it(self, admin_client, zug_um_zug):
        page = admin_client.get(f"/item/{zug_um_zug}").text
        assert 'data-testid="original-title"' in page and "Ticket to Ride" in page

    def test_no_line_when_there_is_none(self, admin_client, db):
        item_id = _insert_item(db, title="Azul", isbn=None, media_type="board_game")
        db.commit()
        assert 'data-testid="original-title"' not in admin_client.get(f"/item/{item_id}").text

    def test_the_edit_form_sets_and_clears_it(self, editor_client, db, zug_um_zug):
        assert 'name="original_title"' in editor_client.get(f"/item/{zug_um_zug}/edit").text
        resp = editor_client.post(f"/api/items/{zug_um_zug}", data={"title": "Zug um Zug",
                                                                      "original_title": "Ticket to Ride: Europe"},
                                  follow_redirects=False)
        assert resp.status_code == 303
        assert _original(db, zug_um_zug) == "Ticket to Ride: Europe"
        editor_client.post(f"/api/items/{zug_um_zug}", data={"title": "Zug um Zug", "original_title": ""},
                           follow_redirects=False)
        assert _original(db, zug_um_zug) is None


class TestStarterTags:
    def test_board_game_starters_follow_the_library_language(self, db, german_library):
        names = [row["name"] for row in tags_svc.suggestions_for(db, "board_game")]
        assert "Knobelspiel" in names and "Puzzle" not in names
        assert len(names) == len(config.TAG_SUGGESTIONS["board_game"])
