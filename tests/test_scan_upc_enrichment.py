"""Both UPC scan paths climb the shared title ladder (issue #36 §3, §4).

Before this, `_scan_upc` sent the raw retail title to TMDb once and `_scan_upc_game`
kept its own unpaced copy of the UPC Item DB call. Nothing exercised either
provider path — `tests/test_upc_manual_add.py` reaches the duplicate branch
before any network call — which is how four defects survived a green suite.

Providers are patched on the modules that **define** them (G37). `items_common`
holds module references, so patching the attribute on the service module is what
its call actually sees.
"""

import pytest

from app.services import igdb, tmdb, upcitemdb


DVD_UPC = "085391163121"
GAME_UPC = "045496590741"

GOODFELLAS = (
    "Goodfellas [DVD]  Feature Thriller Drama  Action  Suspense  Drama  "
    "Crime  Drama Drama"
)
MARIO = "Super Mario: Odyssey - Nintendo Switch"
TOM = "Tom & Jerry: Lost Dragon / Giant Adventure [DVD]"


def _product(title):
    return {"title": title, "category": None, "brand": None, "images": []}


@pytest.fixture
def stub_upc(monkeypatch):
    """Patch upcitemdb.lookup to return one product, with no network."""
    def _install(title):
        async def _lookup(upc, client):
            return _product(title) if title is not None else None
        monkeypatch.setattr(upcitemdb, "lookup", _lookup)
    return _install


class TestDvdScanClimbsTheLadder:
    def test_a_second_rung_hit_files_the_tmdb_metadata(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(GOODFELLAS)
        seen = []

        async def _lookup_by_title(query, key, client):
            seen.append(query)
            if len(seen) == 1:
                return None
            return {
                "title": "Goodfellas",
                "description": "Henry Hill rises through the mob.",
                "publish_year": 1990,
                "cover_url": None,
            }

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert seen == upcitemdb.search_queries(GOODFELLAS)[:2]
        row = db.execute("SELECT * FROM items WHERE upc IS NOT NULL").fetchone()
        assert row["title"] == "Goodfellas"
        assert row["description"] == "Henry Hill rises through the mob."
        assert row["publish_year"] == 1990

    def test_the_raw_retail_title_is_never_sent(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(GOODFELLAS)
        seen = []

        async def _lookup_by_title(query, key, client):
            seen.append(query)
            return None

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert seen  # the ladder was climbed
        assert GOODFELLAS not in seen

    def test_no_hit_anywhere_still_files_the_cleaned_title(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(GOODFELLAS)

        async def _lookup_by_title(query, key, client):
            return None

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert "added" in resp.text.lower()
        row = db.execute("SELECT * FROM items WHERE upc IS NOT NULL").fetchone()
        assert row["title"] == upcitemdb.search_queries(GOODFELLAS)[0]
        assert row["description"] is None

    def test_a_coin_flip_word_is_never_sent_and_never_files_a_wrong_film(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        """The ladder must stop rather than hand a one-word query to TMDb.

        "Tom" returns real films — none of them this disc — and `_first_hit`
        takes the first truthy result, so an unfloored ladder files another
        work's title, synopsis, year and cover as fact. Thin beats wrong: the
        item is filed title-only, which is what happened before the ladder.
        """
        stub_upc(TOM)
        seen = []

        async def _lookup_by_title(query, key, client):
            seen.append(query)
            if query == "Tom":
                return {"title": "Tom at the Farm", "description": "A different film.",
                        "publish_year": 2013, "cover_url": None}
            return None

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert "Tom" not in seen
        row = db.execute("SELECT * FROM items WHERE upc IS NOT NULL").fetchone()
        assert row["title"] == upcitemdb.search_queries(TOM)[0]
        assert row["description"] is None
        assert row["publish_year"] is None

    def test_a_rejected_key_still_files_the_item_title_only(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        """An auth failure must not become a lost scan — nor a 500."""
        stub_upc(GOODFELLAS)

        async def _lookup_by_title(query, key, client):
            raise tmdb.TmdbAuthError("HTTP 401")

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert "added" in resp.text.lower()
        row = db.execute("SELECT * FROM items WHERE upc IS NOT NULL").fetchone()
        assert row["title"] == upcitemdb.search_queries(GOODFELLAS)[0]
        assert row["description"] is None

    def test_no_key_configured_searches_nothing_and_files_the_cleaned_title(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(GOODFELLAS)
        called = []

        async def _lookup_by_title(query, key, client):
            called.append(query)
            return None

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        monkeypatch.delenv("TMDB_API_KEY", raising=False)

        editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert called == []
        row = db.execute("SELECT * FROM items WHERE upc IS NOT NULL").fetchone()
        assert row["title"] == upcitemdb.search_queries(GOODFELLAS)[0]


class TestGameScanClimbsTheSameLadder:
    def test_a_hit_stores_the_igdb_metadata_not_the_result_list(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        """igdb.search_games returns a list; the save tail requires a dict."""
        stub_upc(MARIO)
        seen = []

        async def _search_games(query, cid, secret, client, platform=None, limit=10):
            seen.append(query)
            if len(seen) == 1:
                return []
            return [{
                "igdb_id": 1,
                "title": "Super Mario Odyssey",
                "description": "Mario travels the globe.",
                "publisher": "Nintendo",
                "publish_year": 2017,
                "cover_url": None,
                "developer": "Nintendo EPD",
            }]

        monkeypatch.setattr(igdb, "search_games", _search_games)
        _set_igdb_creds(monkeypatch)

        resp = editor_client.post(
            "/api/scan", data={"isbn": GAME_UPC, "media_type": "video_game"}
        )

        assert resp.status_code == 200
        assert seen == upcitemdb.search_queries(MARIO)[:2]
        row = db.execute("SELECT * FROM items WHERE media_type = 'video_game'").fetchone()
        assert row["title"] == "Super Mario Odyssey"
        assert row["description"] == "Mario travels the globe."
        assert row["publisher"] == "Nintendo"
        assert row["publish_year"] == 2017
        assert row["source"] == "igdb"

    def test_no_hit_files_the_cleaned_title_from_upc(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(MARIO)

        async def _search_games(query, cid, secret, client, platform=None, limit=10):
            return []

        monkeypatch.setattr(igdb, "search_games", _search_games)
        _set_igdb_creds(monkeypatch)

        resp = editor_client.post(
            "/api/scan", data={"isbn": GAME_UPC, "media_type": "video_game"}
        )

        assert resp.status_code == 200
        row = db.execute("SELECT * FROM items WHERE media_type = 'video_game'").fetchone()
        assert row["title"] == "Super Mario: Odyssey"
        assert row["source"] == "upc"


class TestUnresolvableAndTitlelessProducts:
    def test_an_unresolvable_upc_renders_not_found(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(None)
        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})
        assert "not found" in resp.text.lower()
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 0

    @pytest.mark.parametrize("title", [None, "", "   ", "[DVD]"])
    @pytest.mark.parametrize("media_type", ["dvd", "video_game"])
    def test_a_titleless_product_renders_not_found_without_calling_a_provider(
        self, editor_client, db, monkeypatch, stub_upc, title, media_type
    ):
        """A 200 with no usable title is not_found, not an IndexError → HTTP 500."""
        async def _lookup(upc, client):
            return {"title": title, "category": None, "brand": None, "images": []}

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)

        called = []

        async def _lookup_by_title(query, key, client):
            called.append(query)
            return None

        async def _search_games(query, cid, secret, client, platform=None, limit=10):
            called.append(query)
            return []

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        monkeypatch.setattr(igdb, "search_games", _search_games)
        _set_tmdb_key(monkeypatch)
        _set_igdb_creds(monkeypatch)

        upc = DVD_UPC if media_type == "dvd" else GAME_UPC
        resp = editor_client.post("/api/scan", data={"isbn": upc, "media_type": media_type})

        assert resp.status_code == 200
        assert "not found" in resp.text.lower()
        assert called == []
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 0


def _set_tmdb_key(monkeypatch, key="0123456789abcdef0123456789abcdef"):
    """Configure a TMDb key by env var — get_setting reads SECRET_ENV_VARS, so
    this needs no settings row and no encryption round-trip."""
    monkeypatch.setenv("TMDB_API_KEY", key)


def _set_igdb_creds(monkeypatch):
    monkeypatch.setenv("IGDB_CLIENT_ID", "cid")
    monkeypatch.setenv("IGDB_CLIENT_SECRET", "secret")
