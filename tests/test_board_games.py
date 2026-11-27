"""Board games: BGG + GameUPC clients and the scan / search / add routes (issue #167).

Providers are patched on the modules that define them (G37); the routers hold
module references, so that is what their calls see. No test here touches the
network: the parsers get recorded-shape XML/JSON, the routes get stubs.
"""

import httpx
import pytest

from app.routers import items_board_games
from app.services import bgg, covers, gameupc, provider_result
from app.services import upc as upc_svc

CATAN_EAN = "4002051693602"
CASCADIA_EAN = "4002051682590"

CATAN_XML = """<?xml version="1.0" encoding="utf-8"?>
<items termsofuse="https://boardgamegeek.com/xmlapi/termsofuse">
  <item type="boardgame" id="13">
    <thumbnail>https://cf.geekdo-images.com/thumb/pic2419375.jpg</thumbnail>
    <image>https://cf.geekdo-images.com/original/pic2419375.jpg</image>
    <name type="primary" sortindex="1" value="CATAN" />
    <name type="alternate" sortindex="1" value="Die Siedler von Catan" />
    <description>In CATAN, players try to be the dominant force.&amp;#10;&amp;#10;Trade wisely.</description>
    <yearpublished value="1995" />
    <minplayers value="3" />
    <maxplayers value="4" />
    <playingtime value="120" />
    <minplaytime value="60" />
    <maxplaytime value="120" />
    <minage value="10" />
    <link type="boardgamecategory" id="1021" value="Economic" />
    <link type="boardgamecategory" id="1026" value="Negotiation" />
    <link type="boardgamemechanic" id="2072" value="Dice Rolling" />
    <link type="boardgameexpansion" id="926" value="Catan: 5-6 Player Extension" />
    <link type="boardgamedesigner" id="11" value="Klaus Teuber" />
    <link type="boardgamepublisher" id="37" value="KOSMOS" />
    <link type="boardgamepublisher" id="5" value="999 Games" />
  </item>
</items>"""

EXPANSION_XML = """<items>
  <item type="boardgameexpansion" id="926">
    <name type="primary" value="Catan: 5-6 Player Extension" />
    <yearpublished value="1996" />
    <minplayers value="5" /><maxplayers value="6" />
    <playingtime value="0" />
    <minage value="0" />
    <link type="boardgameexpansion" id="13" value="CATAN" inbound="true" />
    <link type="boardgamedesigner" id="3" value="(Uncredited)" />
  </item>
</items>"""

SEARCH_XML = """<items total="3">
  <item type="boardgame" id="1"><name type="alternate" value="Siedler" /><yearpublished value="2001" /></item>
  <item type="boardgame" id="13"><name type="primary" value="CATAN" /><yearpublished value="1995" /></item>
  <item type="boardgameexpansion" id="13"><name type="primary" value="CATAN" /></item>
</items>"""


def _catan_meta():
    return bgg.parse_things(CATAN_XML)[0]


def _candidates(*rows):
    return [{"bgg_id": i, "title": t, "publish_year": None, "confidence": c,
             "thumbnail_url": None, "cover_url": None} for i, t, c in rows]


class TestBggParsing:
    def test_a_thing_becomes_shelf_metadata(self):
        meta = _catan_meta()
        assert meta["bgg_id"] == 13
        assert meta["title"] == "CATAN"
        assert meta["alternate_titles"] == ["Die Siedler von Catan"]
        assert (meta["min_players"], meta["max_players"]) == (3, 4)
        assert meta["play_time_mins"] == 120
        assert meta["min_age"] == 10
        assert meta["publish_year"] == 1995
        assert meta["authors"] == "Klaus Teuber"
        assert meta["publisher"] == "KOSMOS"
        assert meta["categories"] == ["Economic", "Negotiation"]
        assert meta["mechanics"] == ["Dice Rolling"]
        assert meta["cover_url"].endswith("pic2419375.jpg")
        assert meta["base_game_ids"] == []  # an outbound expansion link is not a base game

    def test_entity_escaped_line_breaks_are_unescaped(self):
        assert _catan_meta()["description"] == (
            "In CATAN, players try to be the dominant force.\n\nTrade wisely.")

    def test_an_expansion_names_its_base_game_and_drops_placeholders(self):
        meta = bgg.parse_things(EXPANSION_XML)[0]
        assert meta["kind"] == "boardgameexpansion"
        assert meta["base_game_ids"] == [13]
        assert meta["authors"] is None  # "(Uncredited)" is not a designer
        assert meta["play_time_mins"] is None  # BGG writes 0 for unknown
        assert meta["min_age"] is None

    def test_search_dedupes_by_id_and_falls_back_to_any_name(self):
        hits = bgg.parse_search(SEARCH_XML)
        assert [h["bgg_id"] for h in hits] == [1, 13]
        assert hits[0]["title"] == "Siedler"


class TestBggClient:
    @pytest.mark.asyncio
    async def test_no_token_asks_nobody(self, monkeypatch):
        async def _boom(*a, **k):
            raise AssertionError("no request without a token")
        monkeypatch.setattr(bgg.outbound, "fetch", _boom)
        async with httpx.AsyncClient() as client:
            result = await bgg.lookup_game(13, None, client)
        assert result.outcome == "no_credential"

    @pytest.mark.asyncio
    async def test_a_401_is_a_rejected_token(self, monkeypatch):
        async def _fetch(client, method, url, **kwargs):
            assert kwargs["headers"]["Authorization"] == "Bearer secret"
            return httpx.Response(401, text="", request=httpx.Request(method, url))
        monkeypatch.setattr(bgg.outbound, "fetch", _fetch)
        async with httpx.AsyncClient() as client:
            result = await bgg.lookup_game(13, "secret", client)
        assert result.outcome == "rejected"

    @pytest.mark.asyncio
    async def test_search_puts_the_exact_title_first_and_fills_in_things(self, monkeypatch):
        calls = []

        async def _fetch(client, method, url, **kwargs):
            calls.append((url, kwargs.get("params")))
            body = SEARCH_XML if url.endswith("/search") else CATAN_XML
            return httpx.Response(200, text=body, request=httpx.Request(method, url))
        monkeypatch.setattr(bgg.outbound, "fetch", _fetch)
        async with httpx.AsyncClient() as client:
            result = await bgg.search_games("catan", "secret", client)
        assert result.found
        assert result.payload[0]["bgg_id"] == 13
        assert result.payload[0]["min_players"] == 3  # the thing record, not the bare hit
        assert calls[1][1]["id"] == "13,1"

    @pytest.mark.asyncio
    async def test_broken_xml_is_a_transport_failure_not_a_miss(self, monkeypatch):
        async def _fetch(client, method, url, **kwargs):
            return httpx.Response(200, text="<items><item", request=httpx.Request(method, url))
        monkeypatch.setattr(bgg.outbound, "fetch", _fetch)
        async with httpx.AsyncClient() as client:
            result = await bgg.lookup_game(13, "secret", client)
        assert result.outcome == "transport_failed"


class TestGameUpc:
    def test_parse_sorts_candidates_by_confidence(self):
        parsed = gameupc.parse_lookup({
            "upc": CATAN_EAN, "name": "Catan", "bgg_info_status": "choose_from_bgg_info_or_search",
            "bgg_info": [
                {"id": 278, "name": "Catan Card Game", "confidence": 6, "published": "1996"},
                {"id": 13, "name": "Catan", "confidence": 84, "published": "1995"},
                {"id": "x", "name": "broken"},
            ],
        })
        assert parsed["name"] == "Catan"
        assert parsed["verified"] is False
        assert [c["bgg_id"] for c in parsed["candidates"]] == [13, 278]
        assert parsed["candidates"][0]["publish_year"] == 1995

    @pytest.mark.parametrize("candidates, verified, expected", [
        (_candidates((13, "Catan", 84), (278, "Catan Card Game", 6)), False, 13),
        (_candidates((295947, "Cascadia", 64)), True, 295947),
        (_candidates((1, "Edition A", 60), (2, "Edition B", 55)), False, None),
        (_candidates((1, "Sure but close", 85), (2, "Rival", 70)), False, None),
        (_candidates((295947, "Cascadia", 64)), False, None),
        ([], True, None),
    ])
    def test_auto_pick_only_takes_a_clear_winner(self, candidates, verified, expected):
        pick = gameupc.auto_pick({"verified": verified, "candidates": candidates})
        assert (pick["bgg_id"] if pick else None) == expected

    def test_without_a_key_the_public_test_endpoint_is_used(self, monkeypatch):
        monkeypatch.delenv("SHELF_GAMEUPC_URL", raising=False)
        assert gameupc._endpoint(None) == (gameupc.GAMEUPC_TEST_URL, gameupc.GAMEUPC_TEST_KEY)
        assert gameupc._endpoint("mine") == (gameupc.GAMEUPC_URL, "mine")


@pytest.fixture
def board_game_stubs(monkeypatch):
    """Stub GameUPC, BGG and the cover download; returns knobs and a call log."""
    state = {
        "token": "secret",
        "gameupc": provider_result.found("gameupc", {
            "name": "Catan", "verified": False,
            "candidates": _candidates((13, "Catan", 84), (278, "Catan Card Game", 6)),
        }),
        "calls": [],
    }

    async def _lookup(upc, client, *, api_key=None, search=None):
        state["calls"].append(("gameupc", upc))
        return state["gameupc"]

    async def _lookup_game(bgg_id, token, client, **kwargs):
        state["calls"].append(("bgg", bgg_id))
        meta = {**_catan_meta(), "bgg_id": bgg_id}
        return provider_result.found("bgg", meta)

    async def _fetch_things(ids, token, client):
        state["calls"].append(("things", tuple(ids)))
        return provider_result.found("bgg", [{**_catan_meta(), "bgg_id": i} for i in ids])

    async def _no_cover(item_id, url, client):
        return None

    monkeypatch.setattr(gameupc, "lookup", _lookup)
    monkeypatch.setattr(bgg, "lookup_game", _lookup_game)
    monkeypatch.setattr(bgg, "fetch_things", _fetch_things)
    monkeypatch.setattr(covers, "_download_to_item", _no_cover)
    monkeypatch.setattr(items_board_games, "_credentials", lambda: (state["token"], None))
    return state


def _scan(client, barcode, **extra):
    return client.post("/api/scan", data={"isbn": barcode, "media_type": "board_game", **extra})


def _board_games(db):
    return db.execute(
        "SELECT title, upc, bgg_id, min_players, max_players, duration_mins, min_age, "
        "authors, publisher, source, owned FROM items_live WHERE media_type = 'board_game'"
    ).fetchall()


class TestScanABoardGame:
    def test_a_clear_match_is_filed_with_bgg_metadata(self, editor_client, db, board_game_stubs):
        resp = _scan(editor_client, CATAN_EAN)
        assert resp.status_code == 200
        assert 'data-scan-status="added"' in resp.text
        assert "Board Game via BoardGameGeek" in resp.text
        rows = _board_games(db)
        assert len(rows) == 1
        row = rows[0]
        assert row["title"] == "CATAN"
        assert row["upc"] == upc_svc.normalize_upc(CATAN_EAN)
        assert row["bgg_id"] == 13
        assert (row["min_players"], row["max_players"]) == (3, 4)
        assert row["duration_mins"] == 120
        assert row["min_age"] == 10
        assert row["authors"] == "Klaus Teuber"
        assert row["source"] == "bgg"
        assert row["owned"] == 1

    def test_a_second_scan_is_a_duplicate_without_any_lookup(self, editor_client, db, board_game_stubs):
        _scan(editor_client, CATAN_EAN)
        board_game_stubs["calls"].clear()
        resp = _scan(editor_client, CATAN_EAN)
        assert 'data-scan-status="duplicate"' in resp.text
        assert board_game_stubs["calls"] == []
        assert len(_board_games(db)) == 1

    def test_an_unsure_match_asks_and_the_pick_keeps_the_barcode(self, editor_client, db, board_game_stubs):
        board_game_stubs["gameupc"] = provider_result.found("gameupc", {
            "name": "Zombicide", "verified": False,
            "candidates": _candidates((1, "Edition A", 60), (2, "Edition B", 55)),
        })
        resp = _scan(editor_client, CASCADIA_EAN)
        assert "data-board-game-results" in resp.text
        assert 'name="bgg_id" value="2"' in resp.text
        assert f'name="upc" value="{CASCADIA_EAN}"' in resp.text
        assert _board_games(db) == []

        added = editor_client.post("/api/board-games/add", data={"bgg_id": 2, "upc": CASCADIA_EAN})
        assert 'data-scan-status="added"' in added.text
        row = _board_games(db)[0]
        assert row["bgg_id"] == 2
        assert row["upc"] == upc_svc.normalize_upc(CASCADIA_EAN)

    def test_without_a_bgg_token_gameupc_alone_files_the_game(self, editor_client, db, board_game_stubs):
        board_game_stubs["token"] = None
        resp = _scan(editor_client, CATAN_EAN)
        assert 'data-scan-status="added"' in resp.text
        assert ("bgg", 13) not in board_game_stubs["calls"]
        row = _board_games(db)[0]
        assert (row["title"], row["bgg_id"], row["source"]) == ("Catan", 13, "gameupc")
        assert row["min_players"] is None

    def test_an_unknown_barcode_offers_manual_entry(self, editor_client, db, board_game_stubs):
        board_game_stubs["gameupc"] = provider_result.no_match("gameupc", status=404)
        resp = _scan(editor_client, CATAN_EAN)
        assert 'data-scan-status="not_found"' in resp.text
        assert _board_games(db) == []

    def test_an_unreachable_gameupc_is_a_connectivity_error(self, editor_client, db, board_game_stubs):
        board_game_stubs["gameupc"] = provider_result.transport_failed("gameupc")
        resp = _scan(editor_client, CATAN_EAN)
        assert "check connectivity" in resp.text
        assert _board_games(db) == []

    def test_wishlist_mode_files_an_unowned_game(self, editor_client, db, board_game_stubs):
        _scan(editor_client, CATAN_EAN, mode="wishlist")
        assert _board_games(db)[0]["owned"] == 0


class TestTitleSearch:
    def test_without_a_token_it_points_to_settings(self, editor_client, monkeypatch):
        monkeypatch.setattr(items_board_games, "_credentials", lambda: (None, None))
        resp = editor_client.get("/api/title-search", params={"q": "catan", "media_type": "board_game"})
        assert "token not configured" in resp.text

    def test_results_list_bgg_games_with_players(self, editor_client, monkeypatch, board_game_stubs):
        async def _search(query, token, client, *, limit=10):
            return provider_result.found("bgg", [_catan_meta()])
        monkeypatch.setattr(bgg, "search_games", _search)
        resp = editor_client.get("/api/title-search", params={"q": "catan", "media_type": "board_game"})
        assert "CATAN" in resp.text
        assert "3&ndash;4" in resp.text
        assert "Powered by" in resp.text
        assert 'hx-post="/api/board-games/add"' in resp.text

    def test_adding_twice_without_a_barcode_is_a_duplicate(self, editor_client, db, board_game_stubs):
        first = editor_client.post("/api/board-games/add", data={"bgg_id": 13})
        second = editor_client.post("/api/board-games/add", data={"bgg_id": 13})
        assert 'data-scan-status="added"' in first.text
        assert 'data-scan-status="duplicate"' in second.text
        assert len(_board_games(db)) == 1

    def test_viewers_cannot_add(self, viewer_client, board_game_stubs):
        resp = viewer_client.post("/api/board-games/add", data={"bgg_id": 13})
        assert resp.status_code in (401, 403)


class TestItemPage:
    def test_players_play_time_age_and_bgg_link_show(self, editor_client, db, board_game_stubs):
        _scan(editor_client, CATAN_EAN)
        item_id = db.execute("SELECT id FROM items_live WHERE media_type = 'board_game'").fetchone()["id"]
        page = editor_client.get(f"/item/{item_id}").text
        assert "3&ndash;4" in page
        assert "120 min" in page
        assert "10+" in page
        assert "boardgamegeek.com/boardgame/13" in page


def test_bgg_box_art_passes_the_cover_allowlist():
    """Live run, 2026-11-18: BGG and GameUPC both hand out cf.geekdo-images.com URLs."""
    assert covers.is_allowed_cover_url("https://cf.geekdo-images.com/abc__original/img/x.jpg")


class TestAutoFindsBoardGames:
    """Auto mode asks GameUPC first, but only with a GameUPC key of its own."""

    @pytest.fixture(autouse=True)
    def _own_gameupc_key(self, board_game_stubs, monkeypatch):
        monkeypatch.setattr(items_board_games, "_credentials", lambda: ("secret", "own-key"))

    def test_auto_files_a_game_gameupc_knows(self, editor_client, db, board_game_stubs, monkeypatch):
        from app.services import upcitemdb

        async def _no_upcitemdb(*a, **k):
            raise AssertionError("UPC Item DB must not be asked for a known game")
        monkeypatch.setattr(upcitemdb, "lookup", _no_upcitemdb)
        resp = _scan(editor_client, CATAN_EAN, media_type="auto")
        assert 'data-scan-status="added"' in resp.text
        assert [r["title"] for r in _board_games(db)] == ["CATAN"]
        assert board_game_stubs["calls"].count(("gameupc", CATAN_EAN)) == 1  # the probe's answer is reused

    def test_auto_carries_on_when_gameupc_has_no_game(self, editor_client, db, board_game_stubs, monkeypatch):
        from app.services import upcitemdb
        board_game_stubs["gameupc"] = provider_result.found(
            "gameupc", {"name": "Some Film", "verified": False, "candidates": []})
        asked = []

        async def _upcitemdb(upc, client):
            asked.append(upc)
            return provider_result.no_match("upcitemdb")
        monkeypatch.setattr(upcitemdb, "lookup", _upcitemdb)
        _scan(editor_client, CATAN_EAN, media_type="auto")
        assert asked == [CATAN_EAN]
        assert _board_games(db) == []

    def test_auto_without_board_game_setup_asks_nobody_new(self, editor_client, db, board_game_stubs, monkeypatch):
        from app.services import upcitemdb
        monkeypatch.setattr(items_board_games, "_credentials", lambda: (None, None))

        async def _upcitemdb(upc, client):
            return provider_result.no_match("upcitemdb")
        monkeypatch.setattr(upcitemdb, "lookup", _upcitemdb)
        _scan(editor_client, CATAN_EAN, media_type="auto")
        assert not any(c[0] == "gameupc" for c in board_game_stubs["calls"])

    def test_a_bgg_token_without_a_gameupc_key_does_not_probe(self, editor_client, db, board_game_stubs, monkeypatch):
        """No install's Auto scans may depend on GameUPC's shared test key."""
        from app.services import upcitemdb
        monkeypatch.setattr(items_board_games, "_credentials", lambda: ("secret", None))

        async def _upcitemdb(upc, client):
            return provider_result.no_match("upcitemdb")
        monkeypatch.setattr(upcitemdb, "lookup", _upcitemdb)
        _scan(editor_client, CATAN_EAN, media_type="auto")
        assert not any(c[0] == "gameupc" for c in board_game_stubs["calls"])

    def test_an_explicit_board_game_scan_still_works_without_a_key(self, editor_client, db, board_game_stubs, monkeypatch):
        """The explicit path may use GameUPC's test endpoint: the user asked for a game."""
        monkeypatch.setattr(items_board_games, "_credentials", lambda: ("secret", None))
        resp = _scan(editor_client, CATAN_EAN, media_type="board_game")
        assert 'data-scan-status="added"' in resp.text
        assert ("gameupc", CATAN_EAN) in board_game_stubs["calls"]

    def test_an_explicit_other_type_is_not_probed(self, editor_client, db, board_game_stubs, monkeypatch):
        from app.services import upcitemdb

        async def _upcitemdb(upc, client):
            return provider_result.no_match("upcitemdb")
        monkeypatch.setattr(upcitemdb, "lookup", _upcitemdb)
        _scan(editor_client, CATAN_EAN, media_type="dvd")
        assert not any(c[0] == "gameupc" for c in board_game_stubs["calls"])


class TestEditForm:
    def test_players_and_age_can_be_set_by_hand(self, editor_client, db):
        from tests.conftest import _insert_item
        item_id = _insert_item(db, title="Hausspiel", isbn=None, media_type="board_game")
        db.commit()
        page = editor_client.get(f"/item/{item_id}/edit").text
        for name in ("min_players", "max_players", "min_age"):
            assert f'name="{name}"' in page, name
        resp = editor_client.post(f"/api/items/{item_id}", data={
            "title": "Hausspiel", "media_type": "board_game",
            "min_players": "2", "max_players": "5", "min_age": "8"},
            follow_redirects=False)
        assert resp.status_code == 303
        row = db.execute("SELECT min_players, max_players, min_age FROM items_live WHERE id = ?",
                         (item_id,)).fetchone()
        assert tuple(row) == (2, 5, 8)

    def test_a_non_number_is_refused(self, editor_client, db):
        from tests.conftest import _insert_item
        item_id = _insert_item(db, title="Hausspiel", isbn=None, media_type="board_game", min_players=2)
        db.commit()
        resp = editor_client.post(f"/api/items/{item_id}", data={"title": "Hausspiel", "min_players": "zwei"},
                                  follow_redirects=False)
        assert "error=invalid_number" in resp.headers["location"]
        assert db.execute("SELECT min_players FROM items_live WHERE id = ?", (item_id,)).fetchone()[0] == 2
