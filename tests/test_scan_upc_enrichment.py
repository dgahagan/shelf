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

from app.services import igdb, outbound, provider_result, tmdb, upcitemdb
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
        async def _lookup(upc, client):
            if title is None:
                return provider_result.no_match("upcitemdb")
            return provider_result.found("upcitemdb", _product(title))
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
                return provider_result.no_match("tmdb")
            return provider_result.found("tmdb", {
                "title": "Goodfellas",
                "description": "Henry Hill rises through the mob.",
                "publish_year": 1990,
                "cover_url": None,
            })

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

        async def _lookup_by_title(query, key, client):
            seen.append(query)
            return provider_result.no_match("tmdb")

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
            return provider_result.no_match("tmdb")

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
                return provider_result.found("tmdb", {
                    "title": "Tom at the Farm", "description": "A different film.",
                    "publish_year": 2013, "cover_url": None,
                })
            return provider_result.no_match("tmdb")

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
            return provider_result.rejected("tmdb", status=401)

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
            return provider_result.no_match("tmdb")

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
                return provider_result.no_match("igdb")
            return provider_result.found("igdb", [{
                "igdb_id": 1,
                "title": "Super Mario Odyssey",
                "description": "Mario travels the globe.",
                "publisher": "Nintendo",
                "publish_year": 2017,
                "cover_url": None,
                "developer": "Nintendo EPD",
            }])

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
            return provider_result.no_match("igdb")

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
        async def _search_games(query, cid, secret, client, platform=None, limit=10):
            return provider_result.no_match("igdb")
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
        async def _lookup(upc, client):
            return provider_result.found(
                "upcitemdb", {"title": title, "category": None, "brand": None, "images": []}
            )

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)

        called = []

        async def _lookup_by_title(query, key, client):
            called.append(query)
            return provider_result.no_match("tmdb")

        async def _search_games(query, cid, secret, client, platform=None, limit=10):
            called.append(query)
            return provider_result.no_match("igdb")

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

        async def _lookup(code, client):
            calls.append(code)
            return provider_result.found("upcitemdb", _product(title))

        async def _lookup_by_title(query, key, client):
            return provider_result.no_match("tmdb")

        async def _search_games(query, cid, secret, client, platform=None, limit=10):
            return provider_result.no_match("igdb")

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
    client's payload is None on any non-200, and offline is now its own
    `transport_failed` outcome rather than a raise (G47) — a 429, an exhausted
    quota or a broken DNS would render "Not found — add manually below" for an
    item already on the shelf.
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

        async def _lookup(code, client):
            calls.append(code)
            return provider_result.found("upcitemdb", _product(GOODFELLAS))

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

        async def _lookup(code, client):
            stub_calls.append(code)
            return provider_result.found("upcitemdb", _product(MARIO))

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
        async def _lookup_then_race(code, client):
            # A *separate* connection, in this thread — the `db` fixture's
            # belongs to the test thread and the route runs in another. This
            # is the rival writer, committing inside the lookup window.
            with get_db() as rival:
                _insert_item(
                    rival, title="Raced In", isbn=None, media_type=media_type,
                    upc=upc_svc.normalize_upc(code),
                )
            return provider_result.found("upcitemdb", _product(title))

        async def _lookup_by_title(query, key, client):
            return provider_result.no_match("tmdb")

        async def _search_games(query, cid, secret, client, platform=None, limit=10):
            return provider_result.no_match("igdb")

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

        async def _lookup_by_title(query, key, client):
            seen["tmdb"].append(query)
            return provider_result.no_match("tmdb")

        async def _search_games(query, cid, secret, client, platform=None, limit=10):
            seen["igdb"].append(query)
            return provider_result.no_match("igdb")

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        monkeypatch.setattr(igdb, "search_games", _search_games)
        _set_tmdb_key(monkeypatch)
        _set_igdb_creds(monkeypatch)
        return seen

    def _scan(self, monkeypatch, editor_client, title, category, hint):
        async def _lookup(code, client):
            return provider_result.found(
                "upcitemdb", {"title": title, "category": category, "brand": None, "images": []}
            )

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
        # Issue #43. This pin had the PlayStation 5 row all along and asserted
        # only that it missed *IGDB* — so the scan sailed on to TMDb, whose
        # one-word "PlayStation" rung answered with a different film, and the
        # suite stayed green while the bug shipped. Asking the other half of
        # the question is what turns this row into a real guard.
        assert providers["tmdb"] == [], "a console was searched on a film database"

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
        """When the retail record names neither a CD tag nor a music-CD
        category, the dropdown is what says it, and the choice stands.

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

        async def _lookup_by_title(query, key, client):
            called.append(query)
            return provider_result.no_match("tmdb")

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

        async def _lookup_by_title(query, key, client):
            return provider_result.rejected("tmdb", status=401)

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

        async def _lookup_by_title(query, key, client):
            return provider_result.no_match("tmdb")

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

        async def _lookup_by_title(query, key, client):
            return provider_result.no_match("tmdb")

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

        async def _lookup_by_title(query, key, client):
            return provider_result.found("tmdb", {
                "title": "Goodfellas", "description": "Henry Hill rises through the mob.",
                "publish_year": 1990, "cover_url": None,
            })

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

        async def _lookup_by_title(query, key, client):
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

        async def _lookup_by_title(query, key, client):
            return provider_result.rejected("tmdb", status=401)

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

        async def _lookup_by_title(query, key, client):
            return provider_result.no_match("tmdb")

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

        async def _lookup_by_title(query, key, client):
            return provider_result.found("tmdb", {
                "title": "Goodfellas", "description": "Henry Hill rises through the mob.",
                "publish_year": 1990, "cover_url": None,
            })

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert db.execute("SELECT source FROM items WHERE upc IS NOT NULL").fetchone()[0] == "tmdb"
        assert "via tmdb" in resp.text


class TestGameBranchEnrichmentNotice:
    """Game branch: the same four distinctions the film branch makes (#42).

    `igdb.search_games` used to collapse a rejected Twitch token, a spent
    quota, a transport failure and a genuine empty result into one `[]`, so
    "no match" was rendered for a revoked credential too. Each is its own
    `ProviderResult` outcome now, so "not configured", "rejected",
    "rate-limited" and "no match" are four distinct cards. A transport failure
    still reads as a miss — that one is genuinely ambiguous from the router.
    """

    def test_an_empty_igdb_result_renders_the_no_match_copy(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(MARIO)

        async def _search_games(query, cid, secret, client, platform=None, limit=10):
            return provider_result.no_match("igdb")

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
        shape as the no-match control above, except `search_games` answers
        `rejected` instead of `no_match` — which is exactly what the real
        client does now that the token exchange returns its own outcome. The
        control beside it is what makes this mean anything: if both moved
        together the stub would be what is pinned, not the branch.
        """
        stub_upc(MARIO)

        async def _search_games(query, cid, secret, client, platform=None, limit=10):
            return provider_result.rejected("igdb", status=401)

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

        async def _search_games(query, cid, secret, client, platform=None, limit=10):
            return provider_result.rejected("igdb", status=401)

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

        async def _search_games(query, cid, secret, client, platform=None, limit=10):
            called.append(query)
            return provider_result.no_match("igdb")

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
    one flag shared by both phases (UPC Item DB, TMDb), so these stubs return
    a `rate_limited` `ProviderResult` directly rather than trying to
    reproduce what a real 429 response looks like end to end — that plumbing
    is pinned at the client layer in `test_upcitemdb.py` / `test_tmdb_auth.py`.
    """

    @pytest.mark.parametrize("hint", ("dvd", "video_game"))
    def test_a_product_lookup_429_lands_on_the_not_found_card(
        self, editor_client, db, monkeypatch, hint
    ):
        """Driven through the real client, because the stubbed shape is a lie.

        A 429 is a non-200, so `upcitemdb.lookup`'s payload is `None` for its
        `rate_limited` outcome — there is no title, `search_queries` yields
        `[]`, and `_scan_upc` returns on the `if not queries:` branch **above** the
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

        async def _lookup_by_title(query, key, client):
            return provider_result.rate_limited("tmdb")

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert "rate-limiting us right now" in resp.text

    def test_a_429_stops_the_ladder_and_renders_quota(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        """The ladder stops on a quota rung; it does not climb down to a
        shorter query.

        Deliberate, and a change from the callback era: the same throttled
        host cannot answer differently on a shorter title, and each retry
        costs another `HTTP_TIMEOUT`. `_first_hit`'s docstring says so.

        `rejected` outranking `quota` is not pinned here any more — a single
        `ProviderResult` carries one outcome and the stop means one scan
        cannot see both, so the ranking lives where it is now decided:
        `test_provider_result.py::test_rejected_outranks_rate_limited` and
        `test_scan_outcome.py::test_rejected_outranks_quota_through_combine`.
        """
        stub_upc(GOODFELLAS)
        calls = []

        async def _lookup_by_title(query, key, client):
            calls.append(query)
            return provider_result.rate_limited("tmdb")

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert "rate-limiting us right now" in resp.text
        assert len(calls) == 1, calls

    def test_a_rejected_key_stops_the_ladder_and_renders_rejected(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        """A rejected credential does not improve on a shorter query.

        The raise already stopped the ladder here; the record has to keep
        doing it, or a revoked key costs one outbound call per rung.
        """
        stub_upc(GOODFELLAS)
        calls = []

        async def _lookup_by_title(query, key, client):
            calls.append(query)
            return provider_result.rejected("tmdb", status=401)

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert "TMDb rejected the configured key" in resp.text
        assert "rate-limiting us right now" not in resp.text
        assert len(calls) == 1, calls

    def test_a_transport_failure_stops_the_ladder_and_says_so(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        """The ladder-stop property is this test's point and must survive.

        `tmdb.lookup_by_title` once answered `None` on a timeout and the
        ladder tried the next rung — a second `HTTP_TIMEOUT` against a host
        that had just proved unreachable. It stops now, which is what
        `len(calls) == 1` pins.

        Issue #49 changed only what the card *says*: the disc is still filed
        rather than refused (the connectivity card belongs to the *product*
        lookup, which succeeded), but the enrichment notice now names the
        unreachable provider instead of claiming a miss.
        """
        stub_upc(GOODFELLAS)
        calls = []

        async def _lookup_by_title(query, key, client):
            calls.append(query)
            return provider_result.transport_failed("tmdb")

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert "could not reach TMDb" in resp.text
        assert "no TMDb match for this barcode" not in resp.text
        # Still the *added* card, not the connectivity card: the product
        # lookup succeeded, so the disc is filed. Assert the connectivity
        # card's own message, not the bare phrase — the added card's new
        # offline copy says "Check connectivity" too, and a pin that passed
        # only on capitalisation would defend nothing (G31).
        assert "Metadata lookup failed" not in resp.text
        assert len(calls) == 1, calls

    def test_a_genuine_miss_still_renders_the_no_match_copy_byte_identically(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(GOODFELLAS)

        async def _lookup_by_title(query, key, client):
            return provider_result.no_match("tmdb")

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

        async def _search_games(query, cid, secret, client, platform=None, limit=10):
            return provider_result.rate_limited("igdb")

        monkeypatch.setattr(igdb, "search_games", _search_games)
        _set_igdb_creds(monkeypatch)

        resp = editor_client.post(
            "/api/scan", data={"isbn": GAME_UPC, "media_type": "video_game"}
        )

        assert resp.status_code == 200
        assert "rate-limiting us right now" in resp.text

    def test_a_search_leg_401_reaches_the_rejected_arm_through_the_real_client(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        """The reachability pin: `items_common.py`'s `rejected` branch was
        unreachable from the game path until now, so the arm read as live copy
        that nothing could produce (G65's corollary, the other way round).

        Driven through the **real** `igdb.search_games` — `outbound.fetch`
        answers the token exchange 200 and the `/games` search 401 — because a
        pin that stubs the client is blind to the client's own classification,
        which is the whole thing this task changed (G31).
        """
        stub_upc(MARIO)
        _set_igdb_creds(monkeypatch)
        igdb._token_cache.clear()

        async def _fetch(client, method, url, **kwargs):
            if url == igdb.TWITCH_TOKEN_URL:
                return _StubResp(200, {"access_token": "tok", "expires_in": 3600})
            return _StubResp(401)

        monkeypatch.setattr(outbound, "fetch", _fetch)

        resp = editor_client.post(
            "/api/scan", data={"isbn": GAME_UPC, "media_type": "video_game"}
        )

        assert resp.status_code == 200
        assert "IGDB rejected the configured key" in resp.text
        # Filed with the title the product lookup gave, not refused.
        row = db.execute("SELECT * FROM items WHERE media_type = 'video_game'").fetchone()
        assert row is not None
        assert row["title"]
        # And the dead token is gone, so the user's next scan re-exchanges.
        assert ("cid", "secret") not in igdb._token_cache


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
            return provider_result.no_match("tmdb")

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
            return provider_result.no_match("tmdb")

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

        async def _search_games(query, cid, secret, client, platform=None, limit=10):
            return provider_result.no_match("igdb")

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

    `upcitemdb.lookup` now returns a `transport_failed` `ProviderResult`
    rather than raising — the two stubs below return that record directly
    instead of raising `httpx.ConnectError` themselves.
    """

    def test_a_transport_failure_renders_the_connectivity_card(
        self, editor_client, db, monkeypatch
    ):
        async def _lookup(upc, client):
            return provider_result.transport_failed("upcitemdb")

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
        async def _lookup(upc, client):
            return provider_result.transport_failed("upcitemdb")

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
        async def _lookup(upc, client):
            return provider_result.no_match("upcitemdb")

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert "Not found" in resp.text
        assert "check connectivity" not in resp.text
        row = db.execute(
            "SELECT result FROM scan_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["result"] == "not_found"


class TestARecognisedHardwareScanAsksNobody:
    """Issue #43: a PlayStation 5 barcode was filed as a Street Fighter film.

    `detect` knew the title named console hardware and threw that judgment
    away, so `_scan_upc` climbed its retail-title ladder to the one-word rung
    "PlayStation" — which TMDb answers, confidently, with a different work.
    The card then carried an honest "no usable signal" notice directly above a
    confidently wrong title, and the stored row kept it.

    `G46`'s trade, applied: **missing enrichment is recoverable, wrong
    enrichment is not.** A title-only row still has Retry cover, Find cover and
    the item editor; a row filed as someone else's film has nothing that says
    it is wrong.

    Every pin here asserts **both halves** — the stored fields *and* that the
    provider was never called. `G46`'s own note is that asserting only which
    queries were sent passes against the bug, because the query sequence was
    correct and the answer was another film.
    """

    # What TMDb really answers for the short rungs — a confident hit for an
    # unrelated work. The first row is verbatim from the issue's own log.
    _WRONG_FILM = {
        "PlayStation": "PlayStation: Makers & Gamers - Street Fighter",
        "Nintendo": "Nintendo Quest",
        "Xbox": "Xbox: The Console Wars",
    }

    @pytest.fixture
    def tmdb_calls(self, monkeypatch):
        """Record every TMDb title lookup, and **answer the short rungs**.

        The stub deliberately returns a *found* result for the one-word rungs,
        because that is what the provider really does and it is the only shape
        that makes the stored-title assertions real pins. A stub answering
        `no_match` everywhere leaves the row filed under `queries[0]` either
        way, so the title assertion would pass against the bug and only the
        call-count assertion would ever bite — `G46`'s own note, and a defect
        this fixture had until the mutation check caught it.

        Patched on `tmdb` — the module that **defines** the symbol, not the
        router that reaches it (`G37`). A plain function, not an `AsyncMock`
        over the module: `tmdb` mixes async `lookup_by_title` with sync
        `image_url`, so a blanket `AsyncMock()` would silently turn the sync
        half into un-awaited coroutines (`G56`).
        """
        calls = []

        async def _lookup_by_title(query, key, client):
            calls.append(query)
            wrong = self._WRONG_FILM.get(query)
            if wrong is None:
                return provider_result.no_match("tmdb")
            return provider_result.found(
                "tmdb",
                {"title": wrong, "description": "Not this item at all.",
                 "publish_year": 2016, "cover_url": None},
            )

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)
        return calls

    def _scan(self, monkeypatch, editor_client, title, hint="auto", category=None):
        async def _lookup(code, client):
            return provider_result.found(
                "upcitemdb",
                {"title": title, "category": category, "brand": None, "images": []},
            )

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)
        return editor_client.post(
            "/api/scan", data={"isbn": DVD_UPC, "media_type": hint}
        )

    def _stored(self, db):
        return db.execute("SELECT * FROM items WHERE upc IS NOT NULL").fetchone()

    def test_a_console_is_filed_under_its_own_title_and_tmdb_is_never_asked(
        self, editor_client, db, monkeypatch, tmdb_calls
    ):
        """The reported barcode. Both halves, per `G46`."""
        resp = self._scan(monkeypatch, editor_client, "PlayStation 5 Console")

        assert resp.status_code == 200
        assert tmdb_calls == [], f"a console was searched on TMDb: {tmdb_calls}"
        row = self._stored(db)
        assert row is not None
        assert row["title"] == "PlayStation 5 Console"
        assert row["source"] == "upc"
        assert row["media_type"] == "dvd"

    def test_the_same_scan_under_an_explicit_dvd_hint_behaves_identically(
        self, editor_client, db, monkeypatch, tmdb_calls
    ):
        """Dan's decision, 2026-08-29: the skip overrides the dropdown.

        A hint asserts what the item *is*; it asserts nothing about whether a
        film search on a title containing "Console" will match. Honouring it
        here would leave the reported failure reachable for anyone not on Auto.
        """
        self._scan(monkeypatch, editor_client, "PlayStation 5 Console", hint="dvd")

        assert tmdb_calls == []
        assert self._stored(db)["title"] == "PlayStation 5 Console"

    def test_a_controller_is_the_same_defect_and_the_same_fix(
        self, editor_client, db, monkeypatch, tmdb_calls
    ):
        """The adjacent instance the issue never reported.

        Its ladder is `['Nintendo Switch Pro Controller', 'Nintendo Switch
        Pro', 'Nintendo']`, and "Nintendo" is a rung a film provider answers.
        """
        self._scan(monkeypatch, editor_client, "Nintendo Switch Pro Controller")

        assert tmdb_calls == []
        assert self._stored(db)["title"] == "Nintendo Switch Pro Controller"

    def test_the_card_says_nothing_was_looked_up_not_that_nothing_matched(
        self, editor_client, monkeypatch, tmdb_calls
    ):
        """`no_match` would be a lie in the register of a fact — #42/#44's defect."""
        resp = self._scan(monkeypatch, editor_client, "PlayStation 5 Console")

        assert "no film or game lookup was attempted" in resp.text
        assert "no TMDb match for this barcode" not in resp.text
        # The card and the row must agree — the whole defect was a card that
        # argued with the title beside it.
        assert "Street Fighter" not in resp.text

    def test_the_card_says_it_once_not_twice(
        self, editor_client, monkeypatch, tmdb_calls
    ):
        """Test drive, #43 Observation 1.

        On Auto the filed `dvd` differs from the `auto` hint, so
        `detect_overrode` is true and the template used to render
        `detect_reason` directly beneath the `no_lookup` arm — two amber
        paragraphs both saying the title named console hardware, seven lines
        of them at 390px. The arm now carries the correction clause itself
        and the template suppresses `detect_reason` for this one state.
        """
        resp = self._scan(monkeypatch, editor_client, "PlayStation 5 Console")

        assert resp.text.count("names console hardware") == 1
        # `detect_reason`'s own wording, which must not appear beside the arm.
        assert "not a film or a game" not in resp.text
        # ...but the correction the suppressed line used to carry survives.
        assert "Change the type on the item if that's wrong" in resp.text

    def test_the_correction_clause_survives_an_explicit_hint(
        self, editor_client, monkeypatch, tmdb_calls
    ):
        """The reason the clause moved into the arm rather than the arm going.

        Under an explicit `dvd` hint `detect_overrode` is false, so
        `detect_reason` never rendered here even before the suppression. Had
        the fix simply dropped the arm's explanation and leaned on
        `detect_reason`, this path would say a lookup was skipped and never
        say why or what to do about it.
        """
        resp = self._scan(
            monkeypatch, editor_client, "PlayStation 5 Console", hint="dvd"
        )

        assert "names console hardware" in resp.text
        assert "Change the type on the item if that's wrong" in resp.text

    def test_a_non_hardware_override_still_renders_the_detect_reason(
        self, editor_client, monkeypatch, tmdb_calls
    ):
        """The suppression is scoped to `no_lookup`, not to overrides at large.

        A `[DVD]`-tagged title scanned as a game is a `detected` signal: its
        enrichment notice explains the *lookup*, and the override is a
        separate fact the card still has to state.
        """
        resp = self._scan(
            monkeypatch, editor_client, "Tom Jones [DVD]", hint="video_game"
        )

        assert "filed as DVD / Blu-ray" in resp.text

    def test_a_hardware_detection_never_reaches_the_game_branch(
        self, editor_client, monkeypatch, tmdb_calls
    ):
        """What makes the design's unreachability argument a pin.

        The skip lives in the film branch because a hardware detection always
        resolves to `dvd` — the platform loop it suppresses is the only thing
        that could have produced `video_game`. If that ever stops being true,
        this fails rather than the skip silently not applying (`G47`).
        """
        called = []
        real = items_common._scan_upc_game

        async def _spy(*args, **kwargs):
            called.append(True)
            return await real(*args, **kwargs)

        monkeypatch.setattr(items_common, "_scan_upc_game", _spy)
        self._scan(monkeypatch, editor_client, "PlayStation 5 Console")

        assert called == [], "a hardware detection reached the game branch"

    def test_a_genuine_disc_still_climbs_the_ladder_and_still_enriches(
        self, editor_client, db, monkeypatch
    ):
        """The regression guard, and why the issue's own remedy was rejected.

        The issue proposed refusing to descend the ladder for *any* tier-4
        scan. Probe 1 measured what that costs: `Blade Runner 2049 4-Disc
        Ultimate Collector Edition` is a genuine disc whose useful rung is the
        **second**, so the cap would have traded one visible wrong title for a
        silent class of missing enrichment. It must still reach rung 2 and
        still file the TMDb metadata.
        """
        calls = []

        async def _lookup_by_title(query, key, client):
            calls.append(query)
            if query == "Blade Runner 2049":
                return provider_result.found(
                    "tmdb",
                    {"title": "Blade Runner 2049", "description": "A blade runner.",
                     "publish_year": 2017, "cover_url": None},
                )
            return provider_result.no_match("tmdb")

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)
        self._scan(
            monkeypatch, editor_client,
            "Blade Runner 2049 4-Disc Ultimate Collector Edition",
        )

        assert "Blade Runner 2049" in calls, f"never reached rung 2: {calls}"
        row = self._stored(db)
        assert row["title"] == "Blade Runner 2049"
        assert row["source"] == "tmdb"


class TestATaggedHardwareTitleStillAsksNobody:
    """T2, issue #43's own follow-on (`GOTCHAS.md` G68).

    `_match_title_markers` used to guard only its platform loop with
    `_is_hardware_title` — the medium arm (`CD-ROM`), the format arm
    (`[DVD]`/`DVD`/`Blu-ray`) and the audio arm (`CD`) ran unguarded. So a
    title that names *both* console hardware *and* carries one of those
    tags — "PlayStation 5 Wireless Headset CD-ROM" is the shape that shipped
    the bug — still let tier 2 decide on the tag alone: `video_game`,
    `detected` for the `CD-ROM` case, which `UPC_METADATA_PROVIDERS` maps to
    IGDB, so a real IGDB search went out for a headset. T1 moved the guard to
    `_match_title_markers`'s first statement, an unconditional `return None`,
    so tier 2 now declines every one of these titles outright and the tier-4
    hardware arm answers instead — the same `dvd`/`hardware` verdict a bare
    "PlayStation 5 Console" already gets in `TestARecognisedHardwareScanAsksNobody`
    above.

    Sibling to that class, not a subclass of it — subclassing would re-collect
    its tests under this class's name too, which is not what "reuse the
    shapes" means. `_scan`, `_stored` and the `tmdb_calls` fixture (including
    `_WRONG_FILM`) are copied here verbatim; that class's own tests are
    untouched.
    """

    # Copied from `TestARecognisedHardwareScanAsksNobody._WRONG_FILM`.
    _WRONG_FILM = {
        "PlayStation": "PlayStation: Makers & Gamers - Street Fighter",
        "Nintendo": "Nintendo Quest",
        "Xbox": "Xbox: The Console Wars",
    }

    # A confident wrong game IGDB would answer for a short rung, on the same
    # pattern as `TestAnAutoScannedCDIsFiledAsACDAndAsksNobody._WRONG_GAME`
    # below. "Astro's Playroom" — checked: zero substring overlap, either
    # direction, against every title this class scans ("PlayStation 5
    # Wireless Headset CD-ROM"/"DVD"/"CD", "Console Wars [DVD]", "Myst
    # CD-ROM"), so a card carrying it is unambiguous evidence IGDB was
    # reached (`G46`).
    _WRONG_GAME = {
        "igdb_id": 4242,
        "title": "Astro's Playroom",
        "description": "Not this item at all.",
        "publisher": "Nobody",
        "publish_year": 2020,
        "cover_url": None,
        "developer": "Nobody",
    }

    @pytest.fixture
    def tmdb_calls(self, monkeypatch):
        """Copied from `TestARecognisedHardwareScanAsksNobody.tmdb_calls` —
        see that fixture's docstring for why it must answer the short rungs
        rather than `no_match` everywhere (`G46`)."""
        calls = []

        async def _lookup_by_title(query, key, client):
            calls.append(query)
            wrong = self._WRONG_FILM.get(query)
            if wrong is None:
                return provider_result.no_match("tmdb")
            return provider_result.found(
                "tmdb",
                {"title": wrong, "description": "Not this item at all.",
                 "publish_year": 2016, "cover_url": None},
            )

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)
        return calls

    @pytest.fixture
    def igdb_calls(self, monkeypatch):
        """Record every IGDB title search, and answer a confident wrong game
        — never `no_match` — on the same `G46` reasoning `tmdb_calls` above
        documents: a stub that answers `no_match` everywhere would leave a
        hardware title's stored fallback title identical to what the fix
        files, so the call-count assertion would be the only thing that
        could ever go red.

        A plain `async def`, not an `AsyncMock` over the module: `igdb` mixes
        async `search_games` with sync `image_url`/`_escape`/`_parse_game`,
        so a blanket mock would turn the sync half into un-awaited coroutines
        (`G56`). Patched onto `igdb` — the module that *defines* the symbol,
        not `items_common`, which only holds a reference to it (`G37`).

        The payload is a **list**, matching the real
        `igdb.search_games` signature and return shape
        (`app/services/igdb.py:170` — "the router unwraps `[0]` itself",
        G45) — the same shape
        `TestAnAutoScannedCDIsFiledAsACDAndAsksNobody.game_calls` uses below.
        """
        calls = []

        async def _search_games(title, client_id, client_secret, client, platform=None, limit=10):
            calls.append(title)
            return provider_result.found("igdb", [dict(self._WRONG_GAME)])

        monkeypatch.setattr(igdb, "search_games", _search_games)
        _set_igdb_creds(monkeypatch)
        return calls

    def _scan(self, monkeypatch, editor_client, title, hint="auto", category=None):
        async def _lookup(code, client):
            return provider_result.found(
                "upcitemdb",
                {"title": title, "category": category, "brand": None, "images": []},
            )

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)
        return editor_client.post(
            "/api/scan", data={"isbn": DVD_UPC, "media_type": hint}
        )

    def _stored(self, db):
        return db.execute("SELECT * FROM items WHERE upc IS NOT NULL").fetchone()

    def test_a_cd_rom_tagged_hardware_title_never_reaches_igdb(
        self, editor_client, db, monkeypatch, tmdb_calls, igdb_calls
    ):
        """The row that was red before T1.

        Before T1, only the platform loop was guarded, so tier 2 still fired
        on the bare "CD-ROM" medium marker: `video_game`/`detected`, which
        `UPC_METADATA_PROVIDERS` sends to IGDB. Both providers are stubbed
        here — TMDb as a negative control, since a fix that merely rerouted
        the branch without truly skipping the ladder could still reach it.
        """
        called = []
        real = items_common._scan_upc_game

        async def _spy(*args, **kwargs):
            called.append(True)
            return await real(*args, **kwargs)

        monkeypatch.setattr(items_common, "_scan_upc_game", _spy)

        resp = self._scan(
            monkeypatch, editor_client, "PlayStation 5 Wireless Headset CD-ROM",
        )

        assert resp.status_code == 200
        assert igdb_calls == [], f"a hardware title was searched on IGDB: {igdb_calls}"
        assert tmdb_calls == []
        assert called == [], "a hardware detection reached the game branch"

        row = self._stored(db)
        assert row is not None
        assert row["media_type"] == "dvd"
        assert row["source"] == "upc"
        assert row["title"] == "PlayStation 5 Wireless Headset CD-ROM"
        assert row["description"] is None

        assert "no film or game lookup was attempted" in resp.text
        assert "Astro's Playroom" not in resp.text

    @pytest.mark.parametrize("title,stored", [
        # "DVD" is a `_NOISE_PHRASES` member, stripped from anywhere in the
        # title rather than only as a trailing "- DVD" suffix, so the DVD row
        # files without its tag. "CD" is in no noise or platform-suffix list,
        # so the CD row keeps its own. Both are literals on purpose: comparing
        # against `search_queries(title)[0]` would assert the implementation
        # against itself and follow the ladder if it ever changed (`G31` —
        # whose value is the pin actually reading?).
        ("PlayStation 5 Wireless Headset DVD", "PlayStation 5 Wireless Headset"),
        ("PlayStation 5 Wireless Headset CD", "PlayStation 5 Wireless Headset CD"),
    ])
    def test_a_dvd_or_cd_tagged_hardware_title_never_reaches_tmdb(
        self, editor_client, db, monkeypatch, tmdb_calls, title, stored
    ):
        """The sibling rows: a `[DVD]`/`DVD`/`Blu-ray` format tag or a bare
        `CD` audio tag on a hardware title used to let tier 2's format/audio
        arms decide `detected` on their own, ahead of the hardware verdict.

        The stored title is the *cleaned* scanned title, never a provider's
        answer — that is the pin. It is not always the raw scanned string
        byte-for-byte: `clean_title` strips the bare word "DVD" as retail
        noise wherever it appears, so the DVD row files without its tag while
        the CD row keeps its own. Expected values are literals in the
        parametrise list above; see the note there.
        """
        resp = self._scan(monkeypatch, editor_client, title)

        assert resp.status_code == 200
        assert tmdb_calls == [], f"a hardware title was searched on TMDb: {tmdb_calls}"

        row = self._stored(db)
        assert row is not None
        assert row["media_type"] == "dvd"
        assert row["source"] == "upc"
        assert row["title"] == stored

        assert "no film or game lookup was attempted" in resp.text
        assert "Shelf has no metadata source for this format yet" not in resp.text
        assert "filed as DVD / Blu-ray" not in resp.text

    def test_a_format_tag_on_hardware_never_earns_detected_even_under_an_explicit_hint(
        self, editor_client, monkeypatch, tmdb_calls
    ):
        """Dan's decision, 2026-08-30: a format tag on a hardware listing
        never earned `detected`, hint or no hint.

        Under the explicit `dvd` hint the resolved type and the hint already
        agree (`dvd == dvd`), so this alone can't distinguish "the hardware
        arm decided" from "tier 2's own format arm decided and happened to
        land on the same value" — except that only the hardware arm's card
        text says so, and only the hardware arm skips TMDb. If the guard did
        not sit above tier 2, "PlayStation 5 Wireless Headset DVD"'s own
        "DVD" tag would let tier 2 answer `detected` before the hint is ever
        consulted, and this scan would climb the ladder.
        """
        resp = self._scan(
            monkeypatch, editor_client, "PlayStation 5 Wireless Headset DVD",
            hint="dvd",
        )

        assert tmdb_calls == []
        assert "names console hardware" in resp.text

    def test_a_non_hardware_format_tag_still_reaches_tmdb(
        self, editor_client, db, monkeypatch, tmdb_calls
    ):
        """Regression guard: the guard did not widen at the route. A film
        with its own `[DVD]` tag and no hardware word — `Console Wars`
        contains the hardware word "console" but names no platform, so
        `_is_hardware_title` is false — must still climb the ladder."""
        resp = self._scan(monkeypatch, editor_client, "Console Wars [DVD]")

        assert resp.status_code == 200
        assert tmdb_calls, "a non-hardware DVD never reached TMDb"
        assert self._stored(db)["media_type"] == "dvd"

    def test_a_non_hardware_medium_tag_still_reaches_igdb(
        self, editor_client, db, monkeypatch, igdb_calls
    ):
        """Regression guard, the medium arm's twin: `Myst CD-ROM` names no
        hardware word at all, so it was never affected by the bug and must
        not be affected by the fix either."""
        resp = self._scan(monkeypatch, editor_client, "Myst CD-ROM")

        assert resp.status_code == 200
        assert igdb_calls, "a non-hardware CD-ROM never reached IGDB"
        assert self._stored(db)["media_type"] == "video_game"


class TestABrandNamedHardwareScanAsksNobody:
    """Roadmap residual (ii): a hardware word conjoined with a *brand* rather
    than a platform name (`app/services/detect.py`'s `_HARDWARE_BRANDS`).

    Three shapes, all filed `dvd`/`hardware` and none of them asking a
    provider anything, per `_is_hardware_title`'s conjunction:

    - **ii-a, short brand, no tag** — `Sony PULSE 3D Wireless Headset`. Its
      own ladder stops at `Sony PULSE 3D` (`Sony` alone is 4 characters,
      below `MIN_SOLO_WORD`), so before the brand table existed this rung
      still cleared TMDb's confident-wrong-hit floor.
    - **ii-b, long brand, ladder descends to a bare one-word rung** —
      `Logitech G Pro X Gaming Headset`. Its ladder is `['Logitech G Pro X
      Gaming Headset', 'Logitech G Pro', 'Logitech']` — `Logitech` alone is
      8 characters, clears `MIN_SOLO_WORD` legally, and is exactly the kind
      of one-word rung #43's `PlayStation` was.
    - **ii-c, tagged** — a medium tag (`CD-ROM`) or a format tag (`[DVD]`)
      alongside the brand and a hardware word, mirroring
      `TestATaggedHardwareTitleStillAsksNobody` above but for a brand
      instead of a platform marker.

    **Row 6 is a negative control, not a positive one.** `Turtle Beach
    [DVD]` carries the brand `Turtle Beach` and no hardware word at all
    (`_is_hardware_title` requires the conjunction — a bare brand decides
    nothing, per that predicate's own docstring: `Astro Boy`, `Turtle
    Beach` and `The Corsair` are films). It exists to pin that a brand
    *alone*, with no `console`/`controller`/`headset` beside it, suppresses
    nothing — the row must still climb the ladder and still take TMDb's
    confident wrong answer, exactly as it did before `_HARDWARE_BRANDS`
    existed. Without this row, a bug that suppressed the ladder for *any*
    row carrying a listed brand word would pass every other test in this
    class silently.

    Sibling to `TestARecognisedHardwareScanAsksNobody` and
    `TestATaggedHardwareTitleStillAsksNobody`, not a subclass — subclassing
    would re-collect their tests under this class's name too. `_scan`,
    `_stored` and the call-recording fixtures are copied here verbatim (with
    the `tmdb_calls` fixture reshaped, see below), and those classes' own
    tests are untouched.
    """

    # The wrong film TMDb answers, and the wrong game IGDB answers, for
    # *every* query this class's stub sees — reshaped from the sibling
    # classes' per-rung dict (`_WRONG_FILM = {"PlayStation": ..., ...}`)
    # because that shape does not fit here. `Logitech G Pro X Gaming
    # Headset` descends to `Logitech G Pro` and then the bare one-word rung
    # `Logitech`; `Sony PULSE 3D Wireless Headset` stops at the three-word
    # rung `Sony PULSE 3D` and never reaches a one-word rung at all (`Sony`
    # is 4 characters, below `MIN_SOLO_WORD`). A dict keyed on one-word
    # rungs would answer `no_match` for every rung either row actually
    # sends, and a miss files `queries[0]` — the *same* value the fix
    # files — so the stored-title assertion would never move (`G46`: "the
    # stub must answer, or the stored-field pin is asserting against a
    # branch it does not mean to"). Answering unconditionally is also
    # realistic: a real title search does not require the query to be a
    # single word to return a confident hit for the wrong work.
    _WRONG_FILM = "Midnight in the Garden"
    _WRONG_GAME = {
        "igdb_id": 4343,
        "title": "Rocket Rabbit Racing",
        "description": "Not this item at all.",
        "publisher": "Nobody",
        "publish_year": 2019,
        "cover_url": None,
        "developer": "Nobody",
    }

    # Checked (a throwaway script run over `upcitemdb.search_queries`, not
    # asserted here): `_WRONG_FILM` and `_WRONG_GAME["title"]` share zero
    # substring overlap, in either direction, with any of this class's five
    # scanned titles (`Logitech G Pro X Gaming Headset`, `Sony PULSE 3D
    # Wireless Headset`, `...CD-ROM`, `...[DVD]`, `Turtle Beach [DVD]`) or
    # with any rung their `search_queries` ladders produce — every rung of a
    # title's ladder is itself a substring of that title, so checking the
    # five full titles and their ladders covers both. `G46`'s CD instance
    # (`Rumours` colliding with `Fleetwood Mac - Rumours - CD`) is the shape
    # this check exists to catch.

    @pytest.fixture
    def tmdb_calls(self, monkeypatch):
        """Record every TMDb title lookup, and answer **any** query with the
        same confident wrong film — see the `_WRONG_FILM` comment above for
        why a per-rung dict does not fit this class's two ladders.

        Patched on `tmdb` — the module that **defines** the symbol (`G37`).
        A plain `async def`, not an `AsyncMock` over the module: `tmdb`
        mixes async `lookup_by_title` with sync `image_url`, and a blanket
        mock would silently turn the sync half into un-awaited coroutines
        (`G56`).
        """
        calls = []

        async def _lookup_by_title(query, key, client):
            calls.append(query)
            return provider_result.found(
                "tmdb",
                {"title": self._WRONG_FILM, "description": "Not this item at all.",
                 "publish_year": 2016, "cover_url": None},
            )

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)
        return calls

    @pytest.fixture
    def igdb_calls(self, monkeypatch):
        """Record every IGDB title search, and answer any query with the
        same confident wrong game — never `no_match`, same `G46` reasoning
        as `tmdb_calls` above.

        Payload is a **list**, matching the real `igdb.search_games` return
        shape (`app/services/igdb.py:170` — the router unwraps `[0]`
        itself, `G45`). Patched on `igdb`, the defining module (`G37`); a
        plain `async def`, not an `AsyncMock` (`G56` — `igdb` mixes async
        `search_games` with sync `image_url`/`_escape`/`_parse_game`).
        """
        calls = []

        async def _search_games(title, client_id, client_secret, client, platform=None, limit=10):
            calls.append(title)
            return provider_result.found("igdb", [dict(self._WRONG_GAME)])

        monkeypatch.setattr(igdb, "search_games", _search_games)
        _set_igdb_creds(monkeypatch)
        return calls

    def _scan(self, monkeypatch, editor_client, title, hint="auto", category=None):
        async def _lookup(code, client):
            return provider_result.found(
                "upcitemdb",
                {"title": title, "category": category, "brand": None, "images": []},
            )

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)
        return editor_client.post(
            "/api/scan", data={"isbn": DVD_UPC, "media_type": hint}
        )

    def _stored(self, db):
        return db.execute("SELECT * FROM items WHERE upc IS NOT NULL").fetchone()

    def test_a_long_brand_named_headset_never_reaches_tmdb(
        self, editor_client, db, monkeypatch, tmdb_calls
    ):
        """ii-b: the ladder descends all the way to a bare one-word rung
        (`Logitech`) that a film provider answers confidently — the same
        shape `PlayStation` was in #43, this time gated by a brand rather
        than a platform marker."""
        resp = self._scan(monkeypatch, editor_client, "Logitech G Pro X Gaming Headset")

        assert resp.status_code == 200
        assert tmdb_calls == [], f"a hardware title was searched on TMDb: {tmdb_calls}"

        row = self._stored(db)
        assert row is not None
        assert row["title"] == "Logitech G Pro X Gaming Headset"
        assert row["media_type"] == "dvd"
        assert row["source"] == "upc"
        assert row["description"] is None

        assert "no film or game lookup was attempted" in resp.text
        assert self._WRONG_FILM not in resp.text

    def test_a_short_brand_named_headset_never_reaches_tmdb(
        self, editor_client, db, monkeypatch, tmdb_calls
    ):
        """ii-a: the ladder's shortest rung (`Sony PULSE 3D`) never even
        gets down to a bare one-word rung — `Sony` alone is 4 characters,
        below `MIN_SOLO_WORD` — so the un-floored rung was never the risk
        here. The brand conjunction alone must still suppress the search."""
        resp = self._scan(monkeypatch, editor_client, "Sony PULSE 3D Wireless Headset")

        assert resp.status_code == 200
        assert tmdb_calls == [], f"a hardware title was searched on TMDb: {tmdb_calls}"

        row = self._stored(db)
        assert row is not None
        assert row["title"] == "Sony PULSE 3D Wireless Headset"
        assert row["media_type"] == "dvd"
        assert row["source"] == "upc"
        assert row["description"] is None

        assert "no film or game lookup was attempted" in resp.text
        assert self._WRONG_FILM not in resp.text

    def test_a_cd_rom_tagged_brand_headset_never_reaches_igdb(
        self, editor_client, db, monkeypatch, tmdb_calls, igdb_calls
    ):
        """ii-c, the medium arm: a `CD-ROM` tag on a brand-named headset used
        to let tier 2's medium arm decide `video_game`/`detected` on its
        own, ahead of the hardware verdict — the same defect
        `TestATaggedHardwareTitleStillAsksNobody` pins for a platform
        marker, here for a brand. Both providers are stubbed — TMDb as a
        negative control, since a fix that merely rerouted the branch
        without truly skipping the ladder could still reach it.
        """
        called = []
        real = items_common._scan_upc_game

        async def _spy(*args, **kwargs):
            called.append(True)
            return await real(*args, **kwargs)

        monkeypatch.setattr(items_common, "_scan_upc_game", _spy)

        resp = self._scan(
            monkeypatch, editor_client, "Sony PULSE 3D Wireless Headset CD-ROM",
        )

        assert resp.status_code == 200
        assert called == [], "a hardware detection reached the game branch"
        assert igdb_calls == [], f"a hardware title was searched on IGDB: {igdb_calls}"
        assert tmdb_calls == []

        row = self._stored(db)
        assert row is not None
        assert row["media_type"] == "dvd"
        assert row["source"] == "upc"
        assert row["description"] is None

        assert "no film or game lookup was attempted" in resp.text
        assert self._WRONG_GAME["title"] not in resp.text

    def test_a_format_tagged_brand_headset_never_reaches_tmdb(
        self, editor_client, db, monkeypatch, tmdb_calls
    ):
        """ii-c, the format arm: a `[DVD]` tag on a brand-named headset used
        to let tier 2's format arm decide `dvd`/`detected` on its own,
        ahead of the hardware verdict. The card must not carry the format
        arm's own wording (`'... format tag ...'`) — the confident false
        claim the design calls out: a fix that let tier 2 decide first
        would print that reason line beside the wrong film's title.
        """
        resp = self._scan(
            monkeypatch, editor_client, "Sony PULSE 3D Wireless Headset [DVD]",
        )

        assert resp.status_code == 200
        assert tmdb_calls == [], f"a hardware title was searched on TMDb: {tmdb_calls}"

        row = self._stored(db)
        assert row is not None
        assert row["media_type"] == "dvd"
        assert row["source"] == "upc"
        assert row["description"] is None

        assert "no film or game lookup was attempted" in resp.text
        assert "format tag" not in resp.text

    def test_the_same_scan_under_an_explicit_dvd_hint_behaves_identically(
        self, editor_client, db, monkeypatch, tmdb_calls
    ):
        """ii-b under a `dvd` hint: hardware outranks the dropdown. Dan's
        decision, 2026-08-29 (mirrored from
        `TestARecognisedHardwareScanAsksNobody.test_the_same_scan_under_an_explicit_dvd_hint_behaves_identically`) —
        a hint asserts what the item *is*, not that a film search on a
        title containing "Gaming Headset" will match.
        """
        resp = self._scan(
            monkeypatch, editor_client, "Logitech G Pro X Gaming Headset", hint="dvd",
        )

        assert resp.status_code == 200
        assert tmdb_calls == [], f"a hardware title was searched on TMDb: {tmdb_calls}"

        row = self._stored(db)
        assert row is not None
        assert row["title"] == "Logitech G Pro X Gaming Headset"
        assert row["media_type"] == "dvd"
        assert row["source"] == "upc"
        assert row["description"] is None

        assert "no film or game lookup was attempted" in resp.text
        assert self._WRONG_FILM not in resp.text

    def test_a_brand_alone_with_no_hardware_word_still_reaches_tmdb(
        self, editor_client, db, monkeypatch, tmdb_calls
    ):
        """Negative control (see the class docstring). `Turtle Beach [DVD]`
        carries the brand `Turtle Beach` and no `console`/`controller`/
        `headset` word, so `_is_hardware_title`'s conjunction is false and
        tier 2's own format arm decides normally — the row must still climb
        the ladder and still take TMDb's confident wrong answer, exactly as
        every other DVD-tagged title in this suite does. Without this row,
        a defect that suppressed the ladder for any title merely
        *containing* a listed brand word — hardware or not — would pass
        every other test here silently.
        """
        resp = self._scan(monkeypatch, editor_client, "Turtle Beach [DVD]")

        assert resp.status_code == 200
        assert tmdb_calls, "a non-hardware brand-named DVD never reached TMDb"

        row = self._stored(db)
        assert row is not None
        assert row["title"] == self._WRONG_FILM
        assert row["media_type"] == "dvd"
        assert row["source"] == "tmdb"


class TestAnAutoScannedCDIsFiledAsACDAndAsksNobody:
    """T2 (issue #43's follow-on) — `detect`'s audio and music-CD arms
    (`app/services/detect.py` tier 2/3; see `TestAMusicDiscIsDetectedAsACD`
    in `tests/test_detect.py`) resolve a scanned album to `cd` before
    `_scan_upc` ever reaches the metadata fork, so `no_metadata_provider`
    (`items_common.py:558`) declines the TMDb ladder the same way a
    recognised console does above.

    `G46`'s stub warning applies exactly as it did for #43: a TMDb stub that
    answers `no_match` for every query leaves a CD's stored title identical
    to what the fix files (`queries[0]` either way), so the stored-field pin
    means nothing unless the stub would actually lie. Every stub below
    answers a confident, real-looking wrong film — never `no_match` — on
    every query it is asked. None of the six observed CD titles or the six
    game titles contains the wrong film's title as a substring, so a card
    that carries it is unambiguous evidence TMDb was reached.
    """

    _WRONG_FILM = {
        "title": "Paris, Texas",
        "description": "Not this item at all.",
        "publish_year": 1984,
        "cover_url": None,
    }

    _WRONG_GAME = {
        "igdb_id": 999,
        "title": "Not The Right Game",
        "description": "Not this item at all.",
        "publisher": "Nobody",
        "publish_year": 1999,
        "cover_url": None,
        "developer": "Nobody",
    }

    _OBSERVED_CD_RECORDS = [
        pytest.param(
            "Fleetwood Mac - Rumours - CD",
            "Media > Music & Sound Recordings > Music CDs",
            id="rumours",
        ),
        pytest.param(
            "The Beatles - Abbey Road - CD",
            "Media > Music & Sound Recordings > Music CDs",
            id="abbey_road",
        ),
        pytest.param(
            "Clockcleaner - Nevermind - Rock - CD",
            "Media > Music & Sound Recordings > Music CDs",
            id="nevermind",
        ),
        pytest.param(
            "The Eagles - Hotel California - Music & Performance - CD",
            "Media > Music & Sound Recordings > Music CDs",
            id="hotel_california",
        ),
        pytest.param(
            "Miles Davis Kind of Blue Audio CD", "Media",
            id="kind_of_blue",
        ),
        pytest.param(
            "Born in the USA",
            "Media > Music & Sound Recordings > Music CDs",
            id="born_in_the_usa",
        ),
    ]

    @pytest.fixture
    def cd_tmdb_calls(self, monkeypatch):
        """Own stub (not the shared `providers` fixture — G46's note names
        that fixture by line range specifically because it answers
        `no_match` everywhere and would blind this pin). Records every
        query and answers every one with a confident wrong film.
        """
        calls = []

        async def _lookup_by_title(query, key, client):
            calls.append(query)
            return provider_result.found("tmdb", dict(self._WRONG_FILM))

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)
        return calls

    def _scan_cd(self, monkeypatch, editor_client, title, category, hint="auto"):
        async def _lookup(code, client):
            return provider_result.found(
                "upcitemdb",
                {"title": title, "category": category, "brand": None, "images": []},
            )

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)
        return editor_client.post(
            "/api/scan", data={"isbn": DVD_UPC, "media_type": hint}
        )

    def _stored(self, db):
        return db.execute("SELECT * FROM items WHERE upc IS NOT NULL").fetchone()

    @pytest.mark.parametrize("title, category", _OBSERVED_CD_RECORDS)
    def test_a_scanned_cd_is_filed_as_a_cd_and_tmdb_is_never_asked(
        self, editor_client, db, monkeypatch, cd_tmdb_calls, title, category
    ):
        resp = self._scan_cd(monkeypatch, editor_client, title, category)

        assert resp.status_code == 200
        row = self._stored(db)
        assert row is not None
        assert row["media_type"] == "cd"
        assert row["source"] == "upc"
        assert row["description"] is None
        assert row["publish_year"] is None
        assert row["title"] == upcitemdb.search_queries(title)[0]
        assert cd_tmdb_calls == []
        assert "Shelf has no metadata source for this format yet" in resp.text
        assert self._WRONG_FILM["title"] not in resp.text

    def test_a_deliberate_dvd_hint_still_loses_to_the_title_tag(
        self, editor_client, db, monkeypatch, cd_tmdb_calls
    ):
        """A title tag beats a deliberate hint — the existing tier-2 rule
        (`test_a_platform_marker_beats_a_format_tag_in_the_same_title` and
        friends), not a new one T2 introduces."""
        resp = self._scan_cd(
            monkeypatch, editor_client,
            "Fleetwood Mac - Rumours - CD",
            "Media > Music & Sound Recordings > Music CDs",
            hint="dvd",
        )

        assert resp.status_code == 200
        row = self._stored(db)
        assert row["media_type"] == "cd"
        assert cd_tmdb_calls == []
        # detect_overrode is set (hint 'dvd' != resolved 'cd'), so the card
        # carries the detect reason. Jinja HTML-escapes the quotes around
        # the marker, so match around them rather than the literal apostrophe.
        assert "Title carries a" in resp.text and "audio tag" in resp.text

    @pytest.fixture
    def game_calls(self, monkeypatch):
        """Own stub for the CD-ROM/PC-CD platform-marker games: IGDB
        answers a confident hit on the first rung (which also stops the
        ladder before a bare 'Command' is ever formed), TMDb answers a
        confident wrong film on anything it is asked — the point is that it
        is never asked at all.
        """
        calls = {"tmdb": [], "igdb": []}

        async def _lookup_by_title(query, key, client):
            calls["tmdb"].append(query)
            return provider_result.found("tmdb", dict(self._WRONG_FILM))

        async def _search_games(query, cid, secret, client, platform=None, limit=10):
            calls["igdb"].append(query)
            return provider_result.found("igdb", [dict(self._WRONG_GAME)])

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        monkeypatch.setattr(igdb, "search_games", _search_games)
        _set_tmdb_key(monkeypatch)
        _set_igdb_creds(monkeypatch)
        return calls

    def _scan_game(self, monkeypatch, editor_client, title):
        async def _lookup(code, client):
            return provider_result.found(
                "upcitemdb",
                {"title": title, "category": None, "brand": None, "images": []},
            )

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)
        return editor_client.post(
            "/api/scan", data={"isbn": GAME_UPC, "media_type": "auto"}
        )

    @pytest.mark.parametrize("title", [
        "Myst PC CD-ROM", "The Sims 2 PC CD-ROM Deluxe",
        "Command & Conquer Red Alert (PC CD-ROM)", "Baldur's Gate II PC CD ROM",
        "Myst CD-ROM", "Command & Conquer (CD-ROM)",
    ])
    def test_a_pc_cd_rom_title_is_a_game_and_tmdb_is_never_asked(
        self, editor_client, db, monkeypatch, game_calls, title
    ):
        resp = self._scan_game(monkeypatch, editor_client, title)

        assert resp.status_code == 200
        # Select the scanned row, not rows that already match the property
        # being asserted — a `WHERE media_type = 'video_game'` pin fails with
        # "row is None" whether the item was misfiled or never stored at all.
        row = db.execute("SELECT * FROM items WHERE upc IS NOT NULL").fetchone()
        assert row is not None
        assert row["media_type"] == "video_game"
        assert game_calls["igdb"], "IGDB was never asked"
        assert game_calls["tmdb"] == []

    def test_a_disc_bundle_still_reaches_tmdb(
        self, editor_client, db, monkeypatch
    ):
        """The regression guard for the arm order at the `/api/scan` level:
        format beats audio, so a bundle that carries both a format tag and
        'CD' — and whose upcitemdb category names music CDs — must still
        resolve to `dvd` and still climb the TMDb ladder.
        """
        async def _lookup(code, client):
            return provider_result.found(
                "upcitemdb",
                {"title": "Purple Rain [DVD/CD Combo]",
                 "category": "Media > Music & Sound Recordings > Music CDs",
                 "brand": None, "images": []},
            )

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)

        calls = []

        async def _lookup_by_title(query, key, client):
            calls.append(query)
            return provider_result.no_match("tmdb")

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        _set_tmdb_key(monkeypatch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "auto"})

        assert resp.status_code == 200
        row = db.execute("SELECT * FROM items WHERE upc IS NOT NULL").fetchone()
        assert row["media_type"] == "dvd"
        assert calls, "the disc bundle never reached TMDb"


def _set_tmdb_key(monkeypatch, key="0123456789abcdef0123456789abcdef"):
    """Configure a TMDb key by env var — get_setting reads SECRET_ENV_VARS, so
    this needs no settings row and no encryption round-trip."""
    monkeypatch.setenv("TMDB_API_KEY", key)


def _set_igdb_creds(monkeypatch):
    monkeypatch.setenv("IGDB_CLIENT_ID", "cid")
    monkeypatch.setenv("IGDB_CLIENT_SECRET", "secret")


class TestUpcBranchesRefuseBadValues:
    """The UPC halves of the scan card go through the same funnel (#54,
    plan-review R1a). A stale location or unknown platform used to be a
    foreign-key 500 / a silent NULL; both are the `error` card now."""

    def _gone_location(self, db):
        loc_id = db.execute("INSERT INTO locations (name) VALUES ('Gone')").lastrowid
        db.execute("DELETE FROM locations WHERE id = ?", (loc_id,))
        db.commit()
        return loc_id

    def test_dvd_add_with_a_stale_location_renders_the_error_card(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(GOODFELLAS)
        monkeypatch.delenv("TMDB_API_KEY", raising=False)
        loc_id = self._gone_location(db)

        resp = editor_client.post(
            "/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd", "location_id": str(loc_id)}
        )

        assert resp.status_code == 200
        assert "data-scan-error" in resp.text
        assert f"Location {loc_id} not found" in resp.text
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 0
        assert db.execute(
            "SELECT result FROM scan_log ORDER BY id DESC LIMIT 1"
        ).fetchone()["result"] == "error"

    def test_game_add_with_an_unknown_platform_renders_the_error_card(
        self, editor_client, db, monkeypatch, stub_upc
    ):
        stub_upc(MARIO)

        async def _search_games(query, cid, secret, client, platform=None, limit=10):
            return provider_result.no_match("igdb")

        monkeypatch.setattr(igdb, "search_games", _search_games)
        _set_igdb_creds(monkeypatch)

        resp = editor_client.post(
            "/api/scan", data={"isbn": GAME_UPC, "media_type": "video_game", "platform": "ps9"}
        )

        assert resp.status_code == 200
        assert "data-scan-error" in resp.text
        assert "ps9" in resp.text
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 0
