"""Board game covers from BGG in the cover picker, and the Update from BGG button (#167)."""

import pytest

from app import i18n
from app.routers import items_board_games
from app.services import bgg, covers, provider_result
from tests.conftest import _insert_item
from tests.test_board_games import _catan_meta

VERSIONS_XML = """<items>
  <item type="boardgame" id="13">
    <name type="primary" value="CATAN" />
    <image>https://cf.geekdo-images.com/main.jpg</image>
    <thumbnail>https://cf.geekdo-images.com/main_t.jpg</thumbnail>
    <versions>
      <item type="boardgameversion" id="1">
        <thumbnail>https://cf.geekdo-images.com/en_t.jpg</thumbnail>
        <image>https://cf.geekdo-images.com/en.jpg</image>
        <canonicalname value="Catan" />
        <name type="primary" value="English edition 2015" />
        <link type="boardgamepublisher" id="1" value="Catan Studio" />
        <yearpublished value="2015" />
        <link type="language" id="2184" value="English" />
      </item>
      <item type="boardgameversion" id="2">
        <image>https://cf.geekdo-images.com/de1995.jpg</image>
        <canonicalname value="Die Siedler von Catan" />
        <name type="primary" value="German first edition" />
        <link type="boardgamepublisher" id="37" value="KOSMOS" />
        <yearpublished value="1995" />
        <link type="language" id="2188" value="German" />
      </item>
      <item type="boardgameversion" id="3">
        <image>https://cf.geekdo-images.com/de2022.jpg</image>
        <canonicalname value="Catan" />
        <name type="primary" value="German edition 2022" />
        <yearpublished value="2022" />
        <link type="language" id="2188" value="German" />
      </item>
      <item type="boardgameversion" id="4">
        <canonicalname value="Catan" />
        <name type="primary" value="Afrikaans edition" />
        <yearpublished value="0" />
        <link type="language" id="2677" value="Afrikaans" />
      </item>
    </versions>
  </item>
</items>"""


def _game_with_versions():
    return bgg.parse_things(VERSIONS_XML)[0]


class TestVersions:
    def test_versions_are_parsed(self):
        versions = _game_with_versions()["versions"]
        assert [v["version_id"] for v in versions] == [1, 2, 3, 4]
        assert versions[1] == {
            "version_id": 2, "name": "German first edition", "title": "Die Siedler von Catan",
            "languages": ["German"], "publisher": "KOSMOS", "publish_year": 1995,
            "cover_url": "https://cf.geekdo-images.com/de1995.jpg", "thumbnail_url": None,
        }

    def test_a_thing_without_versions_has_an_empty_list(self):
        assert _catan_meta()["versions"] == []

    def test_library_language_first_newest_first_and_no_artless_versions(self):
        ordered = bgg.versions_in_language(_game_with_versions()["versions"], "de")
        assert [v["version_id"] for v in ordered] == [3, 2, 1]
        assert [v["version_id"] for v in bgg.versions_in_language(_game_with_versions()["versions"], "en")] == [1, 2, 3]


@pytest.fixture
def bgg_calls(monkeypatch):
    calls = []

    async def _lookup_game(bgg_id, token, client, *, versions=False):
        calls.append(("thing", bgg_id, versions))
        return provider_result.found("bgg", _game_with_versions())

    async def _search_games(query, token, client, *, limit=10):
        calls.append(("search", query))
        return provider_result.found("bgg", [
            {**_catan_meta(), "title": "Catan", "bgg_id": 13},
            {**_catan_meta(), "title": "Catan: Seafarers", "bgg_id": 325, "publish_year": 1997,
             "cover_url": "https://cf.geekdo-images.com/sea.jpg", "thumbnail_url": None},
        ])

    monkeypatch.setattr(bgg, "lookup_game", _lookup_game)
    monkeypatch.setattr(bgg, "search_games", _search_games)
    return calls


class TestCoverPicker:
    async def test_a_filed_game_offers_its_editions(self, bgg_calls):
        token = i18n.current_locale.set("de")  # a German reader sees German boxes first
        item = {"media_type": "board_game", "title": "CATAN", "bgg_id": 13}
        try:
            result = await covers.search_covers(item, "CATAN", None, creds={"bgg_api_token": "t"})
        finally:
            i18n.current_locale.reset(token)
        assert result.found
        assert [c["url"].rsplit("/", 1)[1] for c in result.payload] == ["main.jpg", "de2022.jpg", "de1995.jpg", "en.jpg"]
        assert result.payload[1]["source"] == "BGG · German edition 2022"
        assert bgg_calls == [("thing", 13, True)]

    async def test_a_typed_query_or_no_bgg_id_searches_by_title(self, bgg_calls):
        item = {"media_type": "board_game", "title": "Catan", "bgg_id": None}
        result = await covers.search_covers(item, "Catan", None, creds={"bgg_api_token": "t"})
        assert [c["source"] for c in result.payload] == ["BGG · Catan (1995)", "BGG · Catan: Seafarers (1997)"]
        assert bgg_calls == [("search", "Catan")]

    async def test_no_token_is_no_credential_and_asks_nobody(self, bgg_calls):
        result = await covers.search_covers({"media_type": "board_game", "title": "Catan"}, "Catan", None, creds={})
        assert result.outcome == "no_credential" and bgg_calls == []

    def test_the_picker_says_what_to_configure(self, editor_client, db, monkeypatch):
        monkeypatch.delenv("BGG_API_TOKEN", raising=False)
        item_id = _insert_item(db, title="Catan", isbn=None, media_type="board_game")
        db.commit()
        resp = editor_client.get(f"/api/items/{item_id}/cover-search")
        assert "BoardGameGeek token" in resp.text

    def test_board_game_covers_need_the_bgg_token(self):
        assert covers.required_credentials("board_game") == ("bgg_api_token",)


@pytest.fixture
def refresh_stubs(monkeypatch, bgg_calls):
    downloads = []

    async def _download(item_id, url, client):
        downloads.append(url)
        return f"data/covers/{item_id}.jpg"

    monkeypatch.setattr(covers, "_download_to_item", _download)
    monkeypatch.setattr(items_board_games, "_credentials", lambda: ("t", None))
    return downloads


def _refresh(client, item_id):
    return client.post(f"/api/board-games/{item_id}/refresh")


class TestUpdateFromBgg:
    def test_fills_only_empty_fields_and_a_missing_cover(self, editor_client, db, refresh_stubs, monkeypatch):
        async def _lookup_game(bgg_id, token, client, *, versions=False):
            return provider_result.found("bgg", {**_catan_meta(), "categories": ["Economic", "Puzzle"]})
        monkeypatch.setattr(bgg, "lookup_game", _lookup_game)
        item_id = _insert_item(db, title="Siedler", isbn=None, media_type="board_game",
                               bgg_id=13, max_players=6)  # the user's own edit: 5-6 extension
        db.commit()
        resp = _refresh(editor_client, item_id)
        assert resp.headers["HX-Redirect"] == f"/item/{item_id}"
        row = db.execute("SELECT * FROM items_live WHERE id = ?", (item_id,)).fetchone()
        assert row["title"] == "Siedler"                       # never overwritten
        assert (row["min_players"], row["max_players"]) == (3, 6)
        assert row["duration_mins"] == 120 and row["publisher"] == "KOSMOS"
        assert row["cover_path"] == f"data/covers/{item_id}.jpg"

    def test_keeps_an_existing_cover(self, editor_client, db, refresh_stubs):
        item_id = _insert_item(db, title="Catan", isbn=None, media_type="board_game", bgg_id=13,
                               cover_path="data/covers/mine.jpg")
        db.commit()
        _refresh(editor_client, item_id)
        assert refresh_stubs == []

    def test_without_a_bgg_id_an_exact_title_match_is_used(self, editor_client, db, refresh_stubs):
        item_id = _insert_item(db, title="catan ", isbn=None, media_type="board_game")
        db.commit()
        _refresh(editor_client, item_id)
        assert db.execute("SELECT bgg_id FROM items_live WHERE id = ?", (item_id,)).fetchone()[0] == 13

    def test_no_exact_match_changes_nothing_and_says_so(self, editor_client, db, refresh_stubs):
        item_id = _insert_item(db, title="Cat", isbn=None, media_type="board_game")
        db.commit()
        resp = _refresh(editor_client, item_id)
        assert "HX-Redirect" not in resp.headers
        assert "error" in resp.headers["HX-Trigger"]
        assert db.execute("SELECT bgg_id FROM items_live WHERE id = ?", (item_id,)).fetchone()[0] is None

    def test_only_board_games(self, editor_client, db, refresh_stubs):
        item_id = _insert_item(db, title="A Book")
        db.commit()
        assert _refresh(editor_client, item_id).status_code == 404

    def test_viewers_cannot_refresh(self, viewer_client, db, refresh_stubs):
        item_id = _insert_item(db, title="Catan", isbn=None, media_type="board_game")
        db.commit()
        assert _refresh(viewer_client, item_id).status_code == 403

    def test_the_item_page_offers_the_button(self, editor_client, db):
        item_id = _insert_item(db, title="Catan", isbn=None, media_type="board_game")
        db.commit()
        assert 'data-testid="bgg-refresh"' in editor_client.get(f"/item/{item_id}").text
        book_id = _insert_item(db, title="Book", isbn="9780000000132")
        db.commit()
        assert 'data-testid="bgg-refresh"' not in editor_client.get(f"/item/{book_id}").text
