"""Tests for IGDB response parsing — _parse_game and _escape."""

from app.services.igdb import _parse_game, _escape


class TestParseGame:
    def test_publisher_extracted(self):
        game = {
            "id": 1,
            "name": "Test Game",
            "involved_companies": [
                {"company": {"name": "PubCo"}, "publisher": True, "developer": False},
            ],
        }
        result = _parse_game(game)
        assert result["publisher"] == "PubCo"

    def test_developer_extracted(self):
        game = {
            "id": 1,
            "name": "Test Game",
            "involved_companies": [
                {"company": {"name": "DevCo"}, "publisher": False, "developer": True},
            ],
        }
        result = _parse_game(game)
        assert result["developer"] == "DevCo"

    def test_publisher_and_developer_separate(self):
        game = {
            "id": 1,
            "name": "Test Game",
            "involved_companies": [
                {"company": {"name": "PubCo"}, "publisher": True, "developer": False},
                {"company": {"name": "DevCo"}, "publisher": False, "developer": True},
            ],
        }
        result = _parse_game(game)
        assert result["publisher"] == "PubCo"
        assert result["developer"] == "DevCo"

    def test_unix_timestamp_to_year(self):
        # 1609459200 = 2021-01-01 UTC
        game = {"id": 1, "name": "Game", "first_release_date": 1609459200}
        result = _parse_game(game)
        assert result["publish_year"] == 2021

    def test_no_release_date_gives_none(self):
        game = {"id": 1, "name": "Game"}
        result = _parse_game(game)
        assert result["publish_year"] is None

    def test_cover_url_from_image_id(self):
        game = {"id": 1, "name": "Game", "cover": {"image_id": "abc123"}}
        result = _parse_game(game)
        assert result["cover_url"] == "https://images.igdb.com/igdb/image/upload/t_cover_big/abc123.jpg"

    def test_no_cover_gives_none(self):
        game = {"id": 1, "name": "Game"}
        result = _parse_game(game)
        assert result["cover_url"] is None

    def test_franchise_mapped_to_series_name(self):
        game = {"id": 1, "name": "Game", "franchises": [{"name": "Zelda"}]}
        result = _parse_game(game)
        assert result["series_name"] == "Zelda"

    def test_no_franchises_gives_none(self):
        game = {"id": 1, "name": "Game"}
        result = _parse_game(game)
        assert result["series_name"] is None

    def test_platform_names_extracted(self):
        game = {"id": 1, "name": "Game", "platforms": [{"name": "PS5"}, {"name": "PC"}]}
        result = _parse_game(game)
        assert result["platform_names"] == ["PS5", "PC"]

    def test_title_and_igdb_id(self):
        game = {"id": 42, "name": "My Game"}
        result = _parse_game(game)
        assert result["igdb_id"] == 42
        assert result["title"] == "My Game"


class TestEscape:
    def test_backslash_escaped(self):
        assert _escape("path\\to") == "path\\\\to"

    def test_double_quote_escaped(self):
        assert _escape('say "hello"') == 'say \\"hello\\"'

    def test_clean_string_unchanged(self):
        assert _escape("Zelda") == "Zelda"
