"""Both UPC scan paths climb the shared title ladder (issue #36 §3, §4).

Before this, `_scan_upc` sent the raw retail title to TMDb once and `_scan_upc_game`
kept its own unpaced copy of the UPC Item DB call. Nothing exercised either
provider path — `tests/test_upc_manual_add.py` reaches the duplicate branch
before any network call — which is how four defects survived a green suite.

Providers are patched on the modules that **define** them (G37). `items_common`
holds module references, so patching the attribute on the service module is what
its call actually sees.
"""

import httpx
import pytest

from app.services import igdb, tmdb, upcitemdb
from app.services import upc as upc_svc
from app.database import get_db
from app.routers import items_common
from tests.conftest import _insert_item


DVD_UPC = "085391163121"
GAME_UPC = "045496590741"

GOODFELLAS = (
    "Goodfellas [DVD]  Feature Thriller Drama  Action  Suspense  Drama  "
    "Crime  Drama Drama"
)
MARIO = "Super Mario: Odyssey - Nintendo Switch"
TOM = "Tom & Jerry: Lost Dragon / Giant Adventure [DVD]"


class _StubResp:
    """Minimal response for driving a real status through `outbound.fetch`."""

    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = {"items": []} if json_data is None else json_data
        self.headers = {}
        self.text = ""

    def json(self):
        return self._json


def _product(title):
    return {"title": title, "category": None, "brand": None, "images": []}


@pytest.fixture
def stub_upc(monkeypatch):
    """Patch upcitemdb.lookup to return one product, with no network."""
    def _install(title):
        async def _lookup(upc, client, on_rate_limit=None):
            return _product(title) if title is not None else None
        monkeypatch.setattr(upcitemdb, "lookup", _lookup)
    return _install


class TestDvdScanClimbsTheLadder:
    def test_a_second_rung_hit_files_the_tmdb_metadata(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(GOODFELLAS)
        seen = []

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
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
        assert "HX-Trigger" not in resp.headers

    def test_the_raw_retail_title_is_never_sent(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(GOODFELLAS)
        seen = []

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
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

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
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

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
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

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
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

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
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

        async def _search_games(
            query, cid, secret, client, platform=None, limit=10, on_rate_limit=None
        ):
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

        async def _search_games(
            query, cid, secret, client, platform=None, limit=10, on_rate_limit=None
        ):
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


class TestGameScanHonoursWishlistMode:
    """`_scan_upc_game` used to hardcode owned/added regardless of `mode`

    (issue #36 T3) — a game scanned in wishlist mode was filed as owned. The
    film path (`_scan_upc`) already threads `mode` through the same four
    places; this pins the game path doing the same.
    """

    def _stub_no_igdb_hit(self, monkeypatch):
        async def _search_games(
            query, cid, secret, client, platform=None, limit=10, on_rate_limit=None
        ):
            return []
        monkeypatch.setattr(igdb, "search_games", _search_games)
        _set_igdb_creds(monkeypatch)

    def test_wishlist_mode_stores_unowned_and_logs_wishlisted(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(MARIO)
        self._stub_no_igdb_hit(monkeypatch)

        resp = editor_client.post(
            "/api/scan",
            data={"isbn": GAME_UPC, "media_type": "video_game", "mode": "wishlist"},
        )

        assert resp.status_code == 200
        row = db.execute("SELECT * FROM items WHERE media_type = 'video_game'").fetchone()
        assert row["owned"] == 0
        log_row = db.execute(
            "SELECT result FROM scan_log WHERE item_id = ?", (row["id"],)
        ).fetchone()
        assert log_row["result"] == "wishlisted"
        assert "wishlisted" in resp.text.lower()
        assert "HX-Trigger" not in resp.headers

    def test_add_mode_is_unchanged(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(MARIO)
        self._stub_no_igdb_hit(monkeypatch)

        resp = editor_client.post(
            "/api/scan",
            data={"isbn": GAME_UPC, "media_type": "video_game", "mode": "add"},
        )

        assert resp.status_code == 200
        row = db.execute("SELECT * FROM items WHERE media_type = 'video_game'").fetchone()
        assert row["owned"] == 1
        log_row = db.execute(
            "SELECT result FROM scan_log WHERE item_id = ?", (row["id"],)
        ).fetchone()
        assert log_row["result"] == "added"


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
        async def _lookup(upc, client, on_rate_limit=None):
            return {"title": title, "category": None, "brand": None, "images": []}

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)

        called = []

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
            called.append(query)
            return None

        async def _search_games(
            query, cid, secret, client, platform=None, limit=10, on_rate_limit=None
        ):
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


class TestTheProductIsFetchedOnce:
    """The hoist: one UPC Item DB call per scan, on either branch.

    Both branches used to fetch the same record independently, below a fork
    chosen from the dropdown hint alone. Counting the calls is the observable
    proof the fetch moved above the fork.
    """

    @pytest.mark.parametrize("media_type, upc, title", [
        ("dvd", DVD_UPC, GOODFELLAS),
        ("video_game", GAME_UPC, MARIO),
    ])
    def test_upcitemdb_is_called_once_not_twice(
        self, editor_client, db, monkeypatch, media_type, upc, title
    ):
        calls = []

        async def _lookup(code, client, on_rate_limit=None):
            calls.append(code)
            return _product(title)

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
            return None

        async def _search_games(
            query, cid, secret, client, platform=None, limit=10, on_rate_limit=None
        ):
            return []

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)
        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        monkeypatch.setattr(igdb, "search_games", _search_games)
        _set_tmdb_key(monkeypatch)
        _set_igdb_creds(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": upc, "media_type": media_type})

        assert resp.status_code == 200
        assert len(calls) == 1, calls
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 1


class TestARescanCostsNoOutboundCall:
    """The barcode-alone pre-check stayed above the lookup.

    Moving the whole duplicate check below `upcitemdb.lookup` would make every
    re-scan of an owned disc pay for a network round-trip, and — because that
    client returns None on any non-200 and swallows every exception (G47) — a
    429, an exhausted quota or a broken DNS would render "Not found — add
    manually below" for an item already on the shelf.
    """

    def _own_the_disc(self, db):
        _insert_item(
            db, title="Already Owned", isbn=None, media_type="dvd",
            upc=upc_svc.normalize_upc(DVD_UPC),
        )
        db.commit()

    def test_a_rescan_reports_duplicate_without_calling_upcitemdb(
        self, editor_client, db, monkeypatch
    ):
        self._own_the_disc(db)
        calls = []

        async def _lookup(code, client, on_rate_limit=None):
            calls.append(code)
            return _product(GOODFELLAS)

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert "duplicate" in resp.text.lower()
        assert "Already Owned" in resp.text
        assert calls == []

    def test_a_rescan_still_reports_duplicate_when_the_lookup_returns_nothing(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        """The quota-exhausted case, stated as its own contract.

        `stub_upc(None)` is exactly what a 429 or an offline box produces.
        Below the pre-check this renders not_found; above it, duplicate.
        """
        self._own_the_disc(db)
        stub_upc(None)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert "duplicate" in resp.text.lower()
        assert "not found" not in resp.text.lower()

    def test_a_rescan_dedupes_across_the_hint(self, editor_client, db, monkeypatch):
        """One barcode is one product, whatever the dropdown says.

        The pre-check drops the `media_type` term on purpose: after detection
        the stored type may differ from the hint the scan was made under, and
        a hint-keyed check would miss the row it should have found. The
        "same UPC under two types" contract that *is* pinned lives on
        `/api/items/manual`, a different route, and is untouched.
        """
        self._own_the_disc(db)
        stub_calls = []

        async def _lookup(code, client, on_rate_limit=None):
            stub_calls.append(code)
            return _product(MARIO)

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)

        resp = editor_client.post(
            "/api/scan", data={"isbn": DVD_UPC, "media_type": "video_game"}
        )

        assert "duplicate" in resp.text.lower()
        assert stub_calls == []


class TestScanIntegrityErrorGuard:
    """`G18` — a row committed during the lookup window is not a 500.

    The barcode-alone pre-check runs *before* the outbound call, so the whole
    lookup is a window in which a rival scan of the same barcode can commit.
    Seeding the row from inside the stubbed lookup reproduces exactly that
    interleaving.

    Two layers defend the property and they need one pin each (`G31`): the
    media_type-keyed guard under `BEGIN IMMEDIATE`, and the
    `sqlite3.IntegrityError` catch below it. With the guard live the catch
    never runs, so the second test blinds the guard — otherwise deleting the
    catch outright would leave the whole suite green.
    """

    PARAMS = [("dvd", DVD_UPC, GOODFELLAS), ("video_game", GAME_UPC, MARIO)]

    def _race_during_lookup(self, monkeypatch, media_type, title):
        async def _lookup_then_race(code, client, on_rate_limit=None):
            # A *separate* connection, in this thread — the `db` fixture's
            # belongs to the test thread and the route runs in another. This
            # is the rival writer, committing inside the lookup window.
            with get_db() as rival:
                _insert_item(
                    rival, title="Raced In", isbn=None, media_type=media_type,
                    upc=upc_svc.normalize_upc(code),
                )
            return _product(title)

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
            return None

        async def _search_games(
            query, cid, secret, client, platform=None, limit=10, on_rate_limit=None
        ):
            return []

        monkeypatch.setattr(upcitemdb, "lookup", _lookup_then_race)
        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        monkeypatch.setattr(igdb, "search_games", _search_games)
        _set_tmdb_key(monkeypatch)
        _set_igdb_creds(monkeypatch)

    def _one_row(self, db, upc):
        return db.execute(
            "SELECT COUNT(*) c FROM items WHERE upc = ?",
            (upc_svc.normalize_upc(upc),),
        ).fetchone()["c"]

    @pytest.mark.parametrize("media_type, upc, title", PARAMS)
    def test_the_guard_catches_a_row_committed_during_the_lookup(
        self, editor_client, db, monkeypatch, media_type, upc, title
    ):
        """Layer 1: the media_type-keyed guard under the write lock."""
        self._race_during_lookup(monkeypatch, media_type, title)

        resp = editor_client.post("/api/scan", data={"isbn": upc, "media_type": media_type})

        assert resp.status_code == 200
        assert "duplicate" in resp.text.lower()
        assert self._one_row(db, upc) == 1

    @pytest.mark.parametrize("media_type, upc, title", PARAMS)
    def test_a_blinded_guard_still_reports_duplicate_not_500(
        self, editor_client, db, monkeypatch, media_type, upc, title
    ):
        """Layer 2: the IntegrityError catch, with layer 1 disabled.

        `_find_upc_row` returns None the first time — the guard missing the
        row, which is what a race tighter than the write lock would look like.
        The insert then trips `UNIQUE(upc, media_type)` and only the catch can
        turn that into the duplicate card instead of a 500.
        """
        self._race_during_lookup(monkeypatch, media_type, title)

        real = items_common._find_upc_row
        calls = {"n": 0}

        def _blind_first_call(conn, upc_key, mt):
            calls["n"] += 1
            return None if calls["n"] == 1 else real(conn, upc_key, mt)

        monkeypatch.setattr(items_common, "_find_upc_row", _blind_first_call)

        resp = editor_client.post("/api/scan", data={"isbn": upc, "media_type": media_type})

        assert resp.status_code == 200
        assert "duplicate" in resp.text.lower()
        assert calls["n"] == 2  # guard missed, the catch re-looked
        assert self._one_row(db, upc) == 1


class TestTheProductRecordOutranksTheDropdown:
    """T4 — the fork reads the product record, not the dropdown hint.

    Every assertion is on the **stored row** and on **which provider was
    asked**, because that pair is the whole behaviour change. The hint is
    deliberately wrong in each case.
    """

    @pytest.fixture
    def providers(self, monkeypatch):
        """Record which provider each scan reached, and hit neither."""
        seen = {"tmdb": [], "igdb": []}

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
            seen["tmdb"].append(query)
            return None

        async def _search_games(
            query, cid, secret, client, platform=None, limit=10, on_rate_limit=None
        ):
            seen["igdb"].append(query)
            return []

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        monkeypatch.setattr(igdb, "search_games", _search_games)
        _set_tmdb_key(monkeypatch)
        _set_igdb_creds(monkeypatch)
        return seen

    def _scan(self, monkeypatch, editor_client, title, category, hint):
        async def _lookup(code, client, on_rate_limit=None):
            return {"title": title, "category": category, "brand": None, "images": []}

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)
        return editor_client.post(
            "/api/scan", data={"isbn": GAME_UPC, "media_type": hint}
        )

    def _stored(self, db):
        return db.execute("SELECT media_type, title FROM items").fetchone()

    def test_a_video_game_software_category_routes_to_igdb_whatever_the_hint_said(
        self, editor_client, db, monkeypatch, providers
    ):
        self._scan(monkeypatch, editor_client, MARIO, "Software > Video Game Software", "dvd")
        assert providers["igdb"], "IGDB was never asked"
        assert providers["tmdb"] == []
        assert self._stored(db)["media_type"] == "video_game"

    def test_a_console_category_with_a_platform_marker_routes_to_igdb(
        self, editor_client, db, monkeypatch, providers
    ):
        """The Zelda row — tier 2 decides, tier 3 could not have."""
        self._scan(
            monkeypatch, editor_client,
            "The Legend of Zelda: Breath of the Wild - Nintendo Switch",
            "Electronics > Video Game Consoles", "dvd",
        )
        assert providers["igdb"]
        assert providers["tmdb"] == []
        assert self._stored(db)["media_type"] == "video_game"

    def test_a_console_category_without_a_platform_marker_does_not_route_to_igdb(
        self, editor_client, db, monkeypatch, providers
    ):
        """The PlayStation 5 row — a console must not be filed as a game.

        This is the contract a future maintainer widening the category table
        will break, and the reason `Electronics > Video Game Consoles` is
        deliberately absent from tier 3.
        """
        self._scan(
            monkeypatch, editor_client, "PlayStation 5 Console",
            "Electronics > Video Game Consoles", "dvd",
        )
        assert providers["igdb"] == [], "a console reached IGDB as if it were a game"
        assert self._stored(db)["media_type"] != "video_game"

    def test_a_dvd_format_tag_routes_to_tmdb_even_under_a_game_hint(
        self, editor_client, db, monkeypatch, providers
    ):
        self._scan(
            monkeypatch, editor_client, TOM,
            "Electronics > Video > Televisions", "video_game",
        )
        assert providers["tmdb"], "TMDb was never asked"
        assert providers["igdb"] == []
        assert self._stored(db)["media_type"] == "dvd"

    def test_a_platform_marker_beats_a_format_tag_in_the_same_title(
        self, editor_client, db, monkeypatch, providers
    ):
        """`Alice Madness Returns (PC DVD)` is a game whose title says DVD."""
        self._scan(
            monkeypatch, editor_client, "Alice Madness Returns (PC DVD)",
            "Software > Video Game Software", "dvd",
        )
        assert providers["igdb"]
        assert self._stored(db)["media_type"] == "video_game"

    def test_a_deliberate_cd_choice_survives_a_product_record_with_no_markers(
        self, editor_client, db, monkeypatch, providers
    ):
        """Shelf has no CD detection, so the dropdown is a CD's only evidence.

        Detection must not quietly refile an album as a DVD — the tier-4 rule
        is that a *book-family* hint is wrong on a UPC, not that every hint is.
        """
        self._scan(
            monkeypatch, editor_client, "Abbey Road (Remastered)",
            "Music > Rock", "cd",
        )
        assert self._stored(db)["media_type"] == "cd"


class TestEnrichmentNoticeSlot:
    """Issue #36 T5 — the notice slot on the scan result card.

    Four cases: no credential configured, a rejected credential, a genuine
    empty result, and an overridden media type. The first three are the
    film branch's `enrich_notice`; the fourth is `detect_reason`, already
    computed by T4 and rendered here for the first time. All four still
    create the item — thin metadata is never a reason to lose the scan.
    """

    def test_no_credential_configured_still_files_the_item_and_shows_the_notice(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(GOODFELLAS)
        called = []

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
            called.append(query)
            return None

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        monkeypatch.delenv("TMDB_API_KEY", raising=False)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert called == []  # never reached without a key
        row = db.execute("SELECT * FROM items WHERE upc IS NOT NULL").fetchone()
        assert row is not None
        assert "Add a TMDb API key" in resp.text
        assert 'href="/settings"' in resp.text

    def test_a_rejected_credential_renders_a_different_notice(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(GOODFELLAS)

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
            raise tmdb.TmdbAuthError("HTTP 401")

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert "TMDb rejected the configured key" in resp.text
        assert "Add a TMDb API key" not in resp.text
        row = db.execute("SELECT * FROM items WHERE upc IS NOT NULL").fetchone()
        assert row is not None

    def test_an_empty_result_set_renders_a_third_notice(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(GOODFELLAS)

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
            return None

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert "no TMDb match for this barcode" in resp.text
        row = db.execute("SELECT * FROM items WHERE upc IS NOT NULL").fetchone()
        assert row is not None

    def test_an_overridden_media_type_renders_the_detect_reason(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        """Scanned under 'video_game' but the title's own '[DVD]' tag wins."""
        stub_upc(TOM)

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
            return None

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post(
            "/api/scan", data={"isbn": DVD_UPC, "media_type": "video_game"}
        )

        assert resp.status_code == 200
        assert "filed as DVD / Blu-ray" in resp.text
        row = db.execute("SELECT * FROM items WHERE media_type = 'dvd'").fetchone()
        assert row is not None

    def test_metadata_found_shows_no_notice_at_all(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(GOODFELLAS)

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
            return {
                "title": "Goodfellas", "description": "Henry Hill rises through the mob.",
                "publish_year": 1990, "cover_url": None,
            }

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert "Added with title only" not in resp.text


class TestFilmBranchProvenance:
    """The film branch may only claim `tmdb` when TMDb actually answered.

    Found at `/test-drive` (`qa-issue-36-scan-media-detection.md`,
    Observation 1): with no key stored, the card read "DVD / Blu-ray **via
    tmdb**" two lines above "Add a TMDb API key", and the stored row carried
    `source='tmdb'` for a title that came off the UPC record. The game branch
    has always got this right (`source = "igdb" if metadata else "upc"`); the
    T5 notice is what turned the film branch's hard-coded `"tmdb"` into a
    visible contradiction.
    """

    def test_no_credential_files_the_row_as_upc_not_tmdb(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(GOODFELLAS)

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
            raise AssertionError("must not be called without a key")

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        monkeypatch.delenv("TMDB_API_KEY", raising=False)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert db.execute("SELECT source FROM items WHERE upc IS NOT NULL").fetchone()[0] == "upc"
        assert "via upc" in resp.text
        assert "via tmdb" not in resp.text

    def test_a_rejected_credential_files_the_row_as_upc_not_tmdb(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(GOODFELLAS)

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
            raise tmdb.TmdbAuthError("HTTP 401")

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert db.execute("SELECT source FROM items WHERE upc IS NOT NULL").fetchone()[0] == "upc"
        assert "via tmdb" not in resp.text

    def test_an_empty_result_files_the_row_as_upc_not_tmdb(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(GOODFELLAS)

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
            return None

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert db.execute("SELECT source FROM items WHERE upc IS NOT NULL").fetchone()[0] == "upc"
        assert "via tmdb" not in resp.text

    def test_a_real_tmdb_hit_still_claims_tmdb(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        """The other half — the fix must not blank out honest provenance."""
        stub_upc(GOODFELLAS)

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
            return {
                "title": "Goodfellas", "description": "Henry Hill rises through the mob.",
                "publish_year": 1990, "cover_url": None,
            }

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert db.execute("SELECT source FROM items WHERE upc IS NOT NULL").fetchone()[0] == "tmdb"
        assert "via tmdb" in resp.text


class TestGameBranchEnrichmentNotice:
    """Game branch: the same four distinctions the film branch makes (#42).

    `igdb.search_games` used to collapse a rejected Twitch token, a transport
    failure and a genuine empty result into one `[]`, so "no match" was
    rendered for a revoked credential too. It raises `igdb.IgdbAuthError` for
    the first of those now and reports the third through `on_rate_limit`, so
    "not configured", "rejected", "rate-limited" and "no match" are four
    distinct cards. A transport failure is still `[]` and still reads as a
    miss — that one is genuinely ambiguous from the router.
    """

    def test_an_empty_igdb_result_renders_the_no_match_copy(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(MARIO)

        async def _search_games(
            query, cid, secret, client, platform=None, limit=10, on_rate_limit=None
        ):
            return []

        monkeypatch.setattr(igdb, "search_games", _search_games)
        _set_igdb_creds(monkeypatch)

        resp = editor_client.post(
            "/api/scan", data={"isbn": GAME_UPC, "media_type": "video_game"}
        )

        assert resp.status_code == 200
        assert "no IGDB match for this barcode" in resp.text
        row = db.execute("SELECT * FROM items WHERE media_type = 'video_game'").fetchone()
        assert row is not None

    def test_a_rejected_credential_renders_the_rejected_copy(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        """Issue #42: a revoked Twitch credential is no longer filed as a miss.

        This test pinned the limitation before; it pins the fix now. Same stub
        shape as the no-match control above, except `search_games` raises
        `igdb.IgdbAuthError` instead of returning `[]` — which is exactly what
        the real client does since the token exchange stopped swallowing its
        own raise. The control beside it is what makes this mean anything: if
        both moved together the stub would be what is pinned, not the branch.
        """
        stub_upc(MARIO)

        async def _search_games(
            query, cid, secret, client, platform=None, limit=10, on_rate_limit=None
        ):
            raise igdb.IgdbAuthError("Twitch rejected the IGDB credentials (HTTP 401)")

        monkeypatch.setattr(igdb, "search_games", _search_games)
        _set_igdb_creds(monkeypatch)

        resp = editor_client.post(
            "/api/scan", data={"isbn": GAME_UPC, "media_type": "video_game"}
        )

        assert resp.status_code == 200
        assert "IGDB rejected the configured key" in resp.text
        assert "no IGDB match for this barcode" not in resp.text

    def test_a_rejected_credential_still_files_the_item(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        """The contract the film branch has held since #36, and the one a
        reader will assume changed: the card is `added`, not `error`, and the
        game is in the collection under its cleaned barcode title."""
        stub_upc(MARIO)

        async def _search_games(
            query, cid, secret, client, platform=None, limit=10, on_rate_limit=None
        ):
            raise igdb.IgdbAuthError("Twitch rejected the IGDB credentials (HTTP 401)")

        monkeypatch.setattr(igdb, "search_games", _search_games)
        _set_igdb_creds(monkeypatch)

        resp = editor_client.post(
            "/api/scan", data={"isbn": GAME_UPC, "media_type": "video_game"}
        )

        assert resp.status_code == 200
        row = db.execute(
            "SELECT title, source FROM items WHERE media_type = 'video_game'"
        ).fetchone()
        assert row is not None
        assert row["source"] == "upc"
        assert row["title"]

    def test_no_credentials_configured_shows_the_configure_notice(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(MARIO)
        called = []

        async def _search_games(
            query, cid, secret, client, platform=None, limit=10, on_rate_limit=None
        ):
            called.append(query)
            return []

        monkeypatch.setattr(igdb, "search_games", _search_games)
        monkeypatch.delenv("IGDB_CLIENT_ID", raising=False)
        monkeypatch.delenv("IGDB_CLIENT_SECRET", raising=False)

        resp = editor_client.post(
            "/api/scan", data={"isbn": GAME_UPC, "media_type": "video_game"}
        )

        assert resp.status_code == 200
        assert called == []
        assert "Add an IGDB API key" in resp.text
        assert 'href="/settings"' in resp.text


class TestQuotaNotice:
    """T6 (#42/#44 follow-on) — a 429 from either outbound phase renders the
    quota copy, not a genuine miss. `lookup_rate_limited` in `_scan_upc` is
    one flag shared by both phases (UPC Item DB, TMDb) via one closure, so
    these stubs call `on_rate_limit` directly rather than trying to reproduce
    what a real 429 response looks like end to end — that plumbing is pinned
    at the client layer in `test_upcitemdb.py` / `test_tmdb_auth.py`.
    """

    @pytest.mark.parametrize("hint", ("dvd", "video_game"))
    def test_a_product_lookup_429_lands_on_the_not_found_card(
        self, editor_client, db, monkeypatch, hint
    ):
        """Driven through the real client, because the stubbed shape is a lie.

        A 429 is a non-200, so `upcitemdb.lookup` returns `None` after firing
        the callback — there is no title, `search_queries` yields `[]`, and
        `_scan_upc` returns on the `if not queries:` branch **above** the
        `enrich_status` ladder. So the product-phase quota can never reach the
        added-card notice; a stub that both fires the callback and returns a
        product pins a response the client cannot produce.

        The state is threaded onto the `not_found` context instead.

        Parametrized over both hints because the flag is set *above* the
        game/film fork: `_scan_upc` used to read it on the film branch only, so
        the same 429 rendered the quota copy for a `dvd` and a bare "Not found"
        for a `video_game`. One barcode, two stories.
        """
        from unittest.mock import AsyncMock

        with monkeypatch.context() as m:
            m.setattr(
                "app.services.outbound.fetch",
                AsyncMock(return_value=_StubResp(429)),
            )
            _set_tmdb_key(monkeypatch)
            resp = editor_client.post(
                "/api/scan", data={"isbn": DVD_UPC, "media_type": hint}
            )

        assert resp.status_code == 200
        assert "Not found" in resp.text
        assert "rate-limiting us right now" in resp.text
        # Nothing was filed, and nothing claims a TMDb miss it never asked about.
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 0
        assert "no TMDb match for this barcode" not in resp.text


    def test_a_tmdb_lookup_429_renders_the_quota_copy(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(GOODFELLAS)

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
            if on_rate_limit:
                on_rate_limit()
            return None

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert "rate-limiting us right now" in resp.text

    def test_a_rejected_key_that_is_also_429ing_renders_rejected_not_quota(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        """Precedence pin: a rejected credential outranks a quota signal seen
        on the same scan — same ranking the game branch already holds."""
        stub_upc(GOODFELLAS)

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
            if on_rate_limit:
                on_rate_limit()
            raise tmdb.TmdbAuthError("HTTP 401")

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert "TMDb rejected the configured key" in resp.text
        assert "rate-limiting us right now" not in resp.text

    def test_a_genuine_miss_still_renders_the_no_match_copy_byte_identically(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(GOODFELLAS)

        async def _lookup_by_title(query, key, client, on_rate_limit=None):
            return None

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert "no TMDb match for this barcode" in resp.text
        assert "rate-limiting us right now" not in resp.text

    def test_the_game_branch_igdb_429_renders_the_quota_copy(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(MARIO)

        async def _search_games(
            query, cid, secret, client, platform=None, limit=10, on_rate_limit=None
        ):
            if on_rate_limit:
                on_rate_limit()
            return []

        monkeypatch.setattr(igdb, "search_games", _search_games)
        _set_igdb_creds(monkeypatch)

        resp = editor_client.post(
            "/api/scan", data={"isbn": GAME_UPC, "media_type": "video_game"}
        )

        assert resp.status_code == 200
        assert "rate-limiting us right now" in resp.text


class TestAMediaTypeWithNoProvider:
    """Issue #44: a CD was searched on The Movie Database.

    `_scan_upc` forks to IGDB for a game and fell through to TMDb for
    *everything else* — so a scanned CD sent a real request to a film provider
    for a music disc, and the card then said "no TMDb match for this barcode",
    naming a provider that was never going to have it. The defect is the
    routing, not the copy: a test that only reads the card would pass with the
    request still going out.
    """

    def test_a_cd_renders_the_no_provider_copy_and_is_still_filed(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc("Kind of Blue - Miles Davis")
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "cd"})

        assert resp.status_code == 200
        assert "Shelf has no metadata source for this format yet" in resp.text
        row = db.execute("SELECT * FROM items WHERE upc IS NOT NULL").fetchone()
        assert row is not None
        assert row["media_type"] == "cd"
        assert row["title"]
        assert row["source"] == "upc"

    def test_a_cd_never_reaches_tmdb(self, editor_client, db, monkeypatch, stub_upc):
        """The load-bearing pin. #44 is a routing bug, so assert on the *call*.

        A card-only assertion passes with the outbound request still going
        out, which is the failure this whole task exists to remove.
        """
        stub_upc("Kind of Blue - Miles Davis")
        calls = []

        async def _lookup_by_title(query, key, client, **kwargs):
            calls.append(query)
            return None

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "cd"})

        assert resp.status_code == 200
        assert calls == []

    def test_a_dvd_is_unaffected_and_still_reads_as_it_did(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        """The control: the `no_match` card must be byte-identical to v0.21.1."""
        stub_upc(GOODFELLAS)
        calls = []

        async def _lookup_by_title(query, key, client, **kwargs):
            calls.append(query)
            return None

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert calls  # TMDb *is* asked for a film
        assert "no TMDb match for this barcode" in resp.text
        assert "Shelf has no metadata source" not in resp.text

    def test_a_video_game_forks_before_this_branch(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        """`video_game` is in the map, and forks at the game branch anyway."""
        stub_upc(MARIO)

        async def _search_games(
            query, cid, secret, client, platform=None, limit=10, on_rate_limit=None
        ):
            return []

        monkeypatch.setattr(igdb, "search_games", _search_games)
        _set_igdb_creds(monkeypatch)

        resp = editor_client.post(
            "/api/scan", data={"isbn": GAME_UPC, "media_type": "video_game"}
        )

        assert resp.status_code == 200
        assert "no IGDB match for this barcode" in resp.text
        assert "Shelf has no metadata source" not in resp.text

    def test_the_provider_map_is_exactly_dvd_and_video_game(self):
        """Asserted against the literal set, so adding a MEDIA_TYPES member
        without deciding its provider fails here rather than silently
        searching TMDb for it."""
        assert set(items_common.UPC_METADATA_PROVIDERS) == {"dvd", "video_game"}
        assert items_common.UPC_METADATA_PROVIDERS["dvd"] == "tmdb"
        assert items_common.UPC_METADATA_PROVIDERS["video_game"] == "igdb"


class TestATransportFailureIsNotAnAbsentBarcode:
    """GOTCHAS G47: offline and "no such record" were the same outcome.

    `upcitemdb.lookup` swallowed `httpx.ConnectError` by design so an unknown
    barcode reaches the manual-add form. That also made `_scan_upc`'s
    connectivity handler dead code that read as live — a self-hoster with
    broken DNS was told the disc was not found, and the scan was logged
    `not_found`, so the log the troubleshooting docs point them at agreed with
    the wrong story. Both halves are pinned here: the card *and* the log row.
    """

    def test_a_transport_failure_renders_the_connectivity_card(
        self, editor_client, db, monkeypatch
    ):
        async def _lookup(upc, client, on_rate_limit=None):
            raise httpx.ConnectError("offline")

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert "check connectivity" in resp.text
        assert "not found" not in resp.text.lower()

    def test_a_transport_failure_is_logged_as_error_not_not_found(
        self, editor_client, db, monkeypatch
    ):
        """Read it back from the DB — "the log agrees with the card" is half
        of what G47 is about."""
        async def _lookup(upc, client, on_rate_limit=None):
            raise httpx.ConnectError("offline")

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)

        editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        row = db.execute(
            "SELECT result FROM scan_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["result"] == "error"

    def test_the_card_reaches_it_through_the_real_client(
        self, editor_client, db, monkeypatch
    ):
        """The pin that ties the client change to the card.

        The two above stub `upcitemdb.lookup` itself, so they pin the router
        branch and are blind to what the client does with a transport failure
        — restoring the bare `except Exception` leaves them green. This one
        raises from `outbound.fetch`, one layer lower, so it goes red with
        the client's re-raise removed. Both layers are needed: the router pin
        alone cannot tell a live branch from a dead one.
        """
        from unittest.mock import AsyncMock

        with monkeypatch.context() as m:
            m.setattr(
                "app.services.outbound.fetch",
                AsyncMock(side_effect=httpx.ConnectError("offline")),
            )
            resp = editor_client.post(
                "/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"}
            )

        assert resp.status_code == 200
        assert "check connectivity" in resp.text
        row = db.execute(
            "SELECT result FROM scan_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["result"] == "error"

    def test_an_unresolvable_upc_still_reaches_the_manual_add_form(
        self, editor_client, db, monkeypatch
    ):
        """The sibling contract, and the reason the bare catch existed: a 200
        with an empty `items` list is still "no such record"."""
        async def _lookup(upc, client, on_rate_limit=None):
            return None

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert "Not found" in resp.text
        assert "check connectivity" not in resp.text
        row = db.execute(
            "SELECT result FROM scan_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["result"] == "not_found"


def _set_tmdb_key(monkeypatch, key="0123456789abcdef0123456789abcdef"):
    """Configure a TMDb key by env var — get_setting reads SECRET_ENV_VARS, so
    this needs no settings row and no encryption round-trip."""
    monkeypatch.setenv("TMDB_API_KEY", key)


def _set_igdb_creds(monkeypatch):
    monkeypatch.setenv("IGDB_CLIENT_ID", "cid")
    monkeypatch.setenv("IGDB_CLIENT_SECRET", "secret")
