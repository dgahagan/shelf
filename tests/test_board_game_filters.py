"""Board game categories as tags, and the Players / Play time Browse filters (#167)."""

import pytest

from app import browse_filters
from app.services import bgg_tags
from app.services import tags as tags_svc
from tests.conftest import _insert_item
from tests import test_board_games
from tests.test_board_games import CATAN_EAN, _scan

board_game_stubs = test_board_games.board_game_stubs  # the shared fixture, reused here


def _game(db, title, lo, hi, minutes=None, media_type="board_game"):
    return _insert_item(db, title=title, isbn=None, media_type=media_type,
                        min_players=lo, max_players=hi, duration_mins=minutes)


@pytest.fixture
def library(db):
    _game(db, "Solo Puzzle", 1, 1, 20)
    _game(db, "Catan", 3, 4, 90)
    _game(db, "Cascadia", 1, 4, 45)
    _game(db, "Codenames", 2, 8, 15)
    _game(db, "Unknown Max", 2, None, 200)
    _insert_item(db, title="A Film", isbn=None, media_type="dvd", duration_mins=25)
    db.commit()


def _titles(client, **params):
    page = client.get("/api/search", params=params).text
    return {t for t in ("Solo Puzzle", "Catan", "Cascadia", "Codenames", "Unknown Max", "A Film") if t in page}


class TestTagsFromBgg:
    def test_categories_and_known_mechanics_become_tags(self):
        meta = {"categories": ["Puzzle", "Animals", "Not A Category"],
                "mechanics": ["Cooperative Game", "Dice Rolling"], "kind": "boardgame"}
        assert bgg_tags.tag_names(meta) == ["Puzzle", "Animals", "Cooperative"]

    def test_an_expansion_is_tagged_once(self):
        meta = {"categories": ["Expansion for Base-game"], "mechanics": [], "kind": "boardgameexpansion"}
        assert bgg_tags.tag_names(meta) == ["Expansion"]

    def test_gameupc_fallback_has_no_tags(self):
        assert bgg_tags.tag_names({"title": "Catan", "bgg_id": 13}) == []

    def test_tags_are_in_the_library_language(self, monkeypatch):
        monkeypatch.setenv("SHELF_I18N", "on")
        monkeypatch.setenv("SHELF_DEFAULT_LANGUAGE", "de")
        meta = {"categories": ["Puzzle", "Party Game", "Book"], "mechanics": ["Open Drafting", "Closed Drafting"]}
        assert bgg_tags.tag_names(meta) == ["Knobelspiel", "Partyspiel", "Spielbuch", "Drafting"]

    def test_the_ui_flag_off_keeps_english_tags(self, monkeypatch):
        monkeypatch.delenv("SHELF_I18N", raising=False)
        monkeypatch.setenv("SHELF_DEFAULT_LANGUAGE", "de")
        assert bgg_tags.tag_names({"categories": ["Puzzle"]}) == ["Puzzle"]

    def test_every_tag_fits_the_tag_length_cap(self, monkeypatch):
        monkeypatch.setenv("SHELF_I18N", "on")
        for lang in ("en", "de"):
            monkeypatch.setenv("SHELF_DEFAULT_LANGUAGE", lang)
            for name in {**bgg_tags.CATEGORY_TAGS, **bgg_tags.MECHANIC_TAGS}.values():
                tag = bgg_tags.tag_names({"categories": [k for k, v in bgg_tags.CATEGORY_TAGS.items() if v == name][:1],
                                          "mechanics": [k for k, v in bgg_tags.MECHANIC_TAGS.items() if v == name][:1]})
                assert all(len(t) <= tags_svc.MAX_TAG_LENGTH for t in tag), tag

    def test_a_scanned_game_is_filed_with_its_category_tags(self, editor_client, db, board_game_stubs):
        _scan(editor_client, CATAN_EAN)
        item_id = db.execute("SELECT id FROM items_live WHERE media_type = 'board_game'").fetchone()["id"]
        names = {row["name"] for row in tags_svc.get_item_tags(db, item_id)}
        assert names == {"Economic", "Negotiation"}  # "Dice Rolling" is not a mapped mechanic


class TestPlayersFilter:
    @pytest.mark.parametrize("n, expected", [
        ("1", {"Solo Puzzle", "Cascadia"}),
        ("2", {"Cascadia", "Codenames", "Unknown Max"}),  # a game with no max plays with its min
        ("3", {"Catan", "Cascadia", "Codenames"}),
        ("8", {"Codenames"}),
        ("10", set()),
    ])
    def test_a_player_count_finds_the_games_for_that_many(self, admin_client, library, n, expected):
        assert _titles(admin_client, players=n) == expected

    @pytest.mark.parametrize("bad", ["0", "11", "three", "-1", "99999999999999999999"])
    def test_a_bad_value_matches_nothing_rather_than_everything(self, admin_client, library, bad):
        assert _titles(admin_client, players=bad) == set()

    def test_the_top_option_holds_every_larger_group(self, admin_client, db):
        _game(db, "Werewolf", 8, 30)
        db.commit()
        page = admin_client.get("/api/search", params={"players": str(browse_filters.MAX_PLAYERS_OPTION)}).text
        assert "Werewolf" in page

    def test_a_game_whose_minimum_is_above_the_top_option_is_counted_there(self, admin_client, db):
        # The filter finds a game for 12-30 under "10 or more"; the count
        # beside the option must say so too, not leave it out.
        _game(db, "Big Werewolf", 12, 30)
        _game(db, "Party", 4, 10)
        db.commit()
        top = browse_filters.MAX_PLAYERS_OPTION
        assert "Big Werewolf" in admin_client.get("/api/search", params={"players": str(top)}).text
        assert "10 or more players (2)" in admin_client.get("/browse").text

    def test_browse_offers_each_count_with_its_number(self, admin_client, library):
        page = admin_client.get("/browse").text
        assert 'data-testid="players-filter"' in page
        assert "Solo (2)" in page          # Solo Puzzle, Cascadia
        assert "3 players (3)" in page     # Catan, Cascadia, Codenames
        assert "8 players (1)" in page
        assert "9 players" not in page     # no game reaches nine

    def test_counts_follow_the_other_filters(self, admin_client, library):
        page = admin_client.get("/api/search", params={"play_time": "short"}).text
        assert 'id="players-filter"' in page and 'hx-swap-oob="true"' in page
        assert "Solo (1)" in page          # only Solo Puzzle is short
        assert "3 players (1)" in page     # only Codenames

    def test_no_board_games_no_filter(self, admin_client, db):
        _insert_item(db, title="Just a Book")
        db.commit()
        page = admin_client.get("/browse").text
        assert "players-filter" not in page and "play-time-filter" not in page

    def test_the_select_stays_when_a_swap_narrows_to_books(self, admin_client, library):
        page = admin_client.get("/api/search", params={"media_type_filter": "dvd"}).text
        assert 'id="players-filter"' in page  # rendered, so the stale copy is replaced


class TestPlayTimeFilter:
    @pytest.mark.parametrize("bucket, expected", [
        ("short", {"Solo Puzzle", "Codenames"}),   # the 25-minute film is not a game
        ("medium", {"Cascadia"}),
        ("long", {"Catan"}),
        ("epic", {"Unknown Max"}),
        ("forever", set()),
    ])
    def test_buckets(self, admin_client, library, bucket, expected):
        assert _titles(admin_client, play_time=bucket) == expected

    def test_browse_lists_only_buckets_with_games(self, admin_client, db):
        _game(db, "Quick", 2, 4, 20)
        db.commit()
        page = admin_client.get("/browse").text
        assert "Up to 30 min (1)" in page
        assert "Over 2 hours" not in page

    def test_labels_match_the_bucket_bounds(self):
        # "30–60 min" over a 31–60 bucket read as if a 30-minute game were in it.
        from app.services import browse_counts
        for key, (low, high) in browse_filters.PLAY_TIME_BUCKETS.items():
            label = browse_counts.PLAY_TIME_LABELS[key]
            if low is not None and high is not None:
                assert label == f"{low}–{high} min", label

    def test_filters_combine(self, admin_client, library):
        assert _titles(admin_client, players="2", play_time="short") == {"Codenames"}

    def test_chip_prefixes_are_declared(self):
        names = {f.name: f.prefix for f in browse_filters.FILTERS}
        assert names["players"] == "Players" and names["play_time"] == "Play time"


def test_german_labels(admin_client, library, monkeypatch):
    monkeypatch.setenv("SHELF_I18N", "on")
    admin_client.post("/api/account/language", data={"language": "de"})
    page = admin_client.get("/browse").text
    for text in ("Zu dritt (3)", "Allein (2)", "Bis 30 Min. (2)", "Beliebig viele Spieler"):
        assert text in page, text
