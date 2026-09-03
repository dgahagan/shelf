"""The media-typed metadata lookup (`app/services/title_lookup.py`).

Providers are stubbed on the modules that **define** them (G37) — the helper
calls `tmdb.lookup_by_title` / `igdb.search_games` through the module object
precisely so those stubs land, and the ~50 pre-existing stubs in
`tests/test_scan_upc_enrichment.py` keep landing after the ladders moved onto
this helper.

Stubs are hand-written `async def` functions, never a bare `AsyncMock()` — both
client modules mix sync and async helpers, and a blanket mock makes the sync
ones return coroutines (G56).
"""

import pytest

from app.services import igdb, provider_result, tmdb, title_lookup


TMDB_CREDS = {"tmdb_api_key": "key-123"}
IGDB_CREDS = {"igdb_client_id": "id-123", "igdb_client_secret": "secret-123"}
ALL_CREDS = {**TMDB_CREDS, **IGDB_CREDS}

FILM = {
    "title": "Mad Max: Fury Road",
    "description": "A war rig runs for the green place.",
    "publish_year": 2015,
    "cover_url": "https://image.tmdb.org/t/p/w500/abc.jpg",
}
GAME = {
    "igdb_id": 1029,
    "title": "Super Mario Odyssey",
    "description": "Mario travels the globe.",
    "publisher": "Nintendo",
    "developer": "Nintendo EPD",
    "publish_year": 2017,
    "cover_url": "https://images.igdb.com/igdb/image/upload/t_cover_big/abc.jpg",
    "platform_names": ["Nintendo Switch"],
    "series_name": "Super Mario",
}


class _Recorder:
    """Records every call a stub received, so "no call at all" is assertable."""

    def __init__(self):
        self.calls = []

    def __len__(self):
        return len(self.calls)


@pytest.fixture
def tmdb_stub(monkeypatch):
    rec = _Recorder()

    def install(result):
        async def _lookup_by_title(title, api_key, client):
            rec.calls.append({"title": title, "api_key": api_key})
            return result

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        return rec

    install.calls = rec.calls
    return install


@pytest.fixture
def igdb_stub(monkeypatch):
    rec = _Recorder()

    def install(result):
        async def _search_games(
            title, client_id, client_secret, client, platform=None, limit=10,
        ):
            rec.calls.append({
                "title": title, "client_id": client_id,
                "client_secret": client_secret, "platform": platform, "limit": limit,
            })
            return result

        monkeypatch.setattr(igdb, "search_games", _search_games)
        return rec

    install.calls = rec.calls
    return install


@pytest.fixture
def no_provider_calls(monkeypatch):
    """Both clients wired to fail loudly, so "made no call" is a real assertion."""

    async def _boom_tmdb(*args, **kwargs):
        raise AssertionError("tmdb.lookup_by_title was called")

    async def _boom_igdb(*args, **kwargs):
        raise AssertionError("igdb.search_games was called")

    monkeypatch.setattr(tmdb, "lookup_by_title", _boom_tmdb)
    monkeypatch.setattr(igdb, "search_games", _boom_igdb)


class TestTheProviderMapMovedButDidNotChange:
    """The map is declared here now; `items_common` re-exports it (G67 budget).

    The same shape `tests/test_scan_upc_enrichment.py` pins through
    `items_common`, asserted at its new home.
    """

    def test_the_map_is_exactly_dvd_and_video_game(self):
        assert set(title_lookup.UPC_METADATA_PROVIDERS) == {"dvd", "video_game"}
        assert title_lookup.UPC_METADATA_PROVIDERS["dvd"] == "tmdb"
        assert title_lookup.UPC_METADATA_PROVIDERS["video_game"] == "igdb"

    def test_the_router_re_export_is_the_same_object(self):
        """`items_common.UPC_METADATA_PROVIDERS` is this map, not a copy — a
        copy would drift and both would keep looking correct in isolation.
        `tests/test_scan_upc_enrichment.py` still reads the router's name."""
        from app.routers import items_common

        assert items_common.UPC_METADATA_PROVIDERS is title_lookup.UPC_METADATA_PROVIDERS

    def test_the_service_imports_no_router(self):
        """A service-to-router import at module level would be a cycle: the
        router imports this module. Asserted on the source, because the cycle
        only bites at import time and the suite has already imported both."""
        from pathlib import Path

        source = Path(title_lookup.__file__).read_text()
        code = [
            line for line in source.splitlines()
            if line.startswith(("import ", "from "))
        ]
        assert not [line for line in code if "app.routers" in line], code


class TestRequiredCredentials:
    """Mirrors `covers.required_credentials`, resolved through the metadata map."""

    def test_dvd_needs_the_tmdb_key(self):
        assert title_lookup.required_credentials("dvd") == ("tmdb_api_key",)

    def test_video_game_needs_both_igdb_keys(self):
        assert title_lookup.required_credentials("video_game") == (
            "igdb_client_id", "igdb_client_secret",
        )

    def test_a_type_with_no_metadata_provider_needs_nothing(self):
        assert title_lookup.required_credentials("cd") == ()
        assert title_lookup.required_credentials("book") == ()

    def test_none_needs_nothing(self):
        assert title_lookup.required_credentials(None) == ()

    def test_the_key_map_is_not_redeclared_here(self):
        """Read from `covers.CREDENTIAL_KEYS`, so the covers dispatch and this
        one cannot disagree about what "configured" means (G49)."""
        from app.services import covers

        assert title_lookup.required_credentials("video_game") == covers.CREDENTIAL_KEYS["igdb"]


class TestADvdGoesToTmdb:
    async def test_it_calls_tmdb_once_and_returns_the_dict_untouched(self, tmdb_stub):
        rec = tmdb_stub(provider_result.found("tmdb", FILM))

        result = await title_lookup.lookup_by_title(
            "Mad Max Fury Road", "dvd", client=None, creds=ALL_CREDS,
        )

        assert len(rec) == 1
        assert rec.calls[0] == {"title": "Mad Max Fury Road", "api_key": "key-123"}
        assert result.found
        assert result.payload == FILM

    async def test_a_miss_rides_through(self, tmdb_stub):
        tmdb_stub(provider_result.no_match("tmdb", status=200))

        result = await title_lookup.lookup_by_title(
            "Nothing At All", "dvd", client=None, creds=ALL_CREDS,
        )

        assert result.outcome == "no_match"
        assert result.provider == "tmdb"


class TestAGameGoesToIgdbAndIsUnwrapped:
    """G45: `igdb.search_games` answers a **list**; no caller may meet one."""

    async def test_it_asks_for_one_result_and_returns_the_dict_not_the_list(self, igdb_stub):
        rec = igdb_stub(provider_result.found("igdb", [GAME, {"title": "Runner up"}]))

        result = await title_lookup.lookup_by_title(
            "Super Mario Odyssey", "video_game", client=None, creds=ALL_CREDS,
        )

        assert len(rec) == 1
        assert rec.calls[0]["limit"] == 1
        assert rec.calls[0]["client_id"] == "id-123"
        assert rec.calls[0]["client_secret"] == "secret-123"
        assert result.found
        assert isinstance(result.payload, dict)
        assert result.payload == GAME

    async def test_a_found_carrying_an_empty_list_is_a_miss_not_an_index_error(self, igdb_stub):
        """The shape that would raise `IndexError` inside a bulk confirm."""
        igdb_stub(provider_result.found("igdb", []))

        result = await title_lookup.lookup_by_title(
            "Super Mario Odyssey", "video_game", client=None, creds=ALL_CREDS,
        )

        assert result.outcome == "no_match"
        assert result.provider == "igdb"
        assert result.payload is None

    async def test_the_platform_filter_is_passed_through(self, igdb_stub):
        rec = igdb_stub(provider_result.found("igdb", [GAME]))

        await title_lookup.lookup_by_title(
            "Super Mario Odyssey", "video_game", client=None,
            platform="switch", creds=ALL_CREDS,
        )

        assert rec.calls[0]["platform"] == "switch"

    async def test_no_platform_is_the_default(self, igdb_stub):
        rec = igdb_stub(provider_result.found("igdb", [GAME]))

        await title_lookup.lookup_by_title(
            "Super Mario Odyssey", "video_game", client=None, creds=ALL_CREDS,
        )

        assert rec.calls[0]["platform"] is None


class TestAnUnmappedMediaTypeAsksNobody:
    """A CD has no music provider (roadmap 6b) and a book has its own path."""

    async def test_a_cd_is_a_miss_with_no_call(self, no_provider_calls):
        result = await title_lookup.lookup_by_title(
            "Kind of Blue", "cd", client=None, creds=ALL_CREDS,
        )

        assert result.outcome == "no_match"
        assert result.provider == "none"

    async def test_a_book_is_a_miss_with_no_call(self, no_provider_calls):
        result = await title_lookup.lookup_by_title(
            "Dune", "book", client=None, creds=ALL_CREDS,
        )

        assert result.outcome == "no_match"

    async def test_an_unknown_type_is_a_miss_with_no_call(self, no_provider_calls):
        result = await title_lookup.lookup_by_title(
            "Anything", "not_a_media_type", client=None, creds=ALL_CREDS,
        )

        assert result.outcome == "no_match"


class TestMissingCredentialsMeanNoCall:
    """Not an error: the row is filed title-only, which is today's behaviour."""

    async def test_a_missing_tmdb_key(self, no_provider_calls):
        result = await title_lookup.lookup_by_title(
            "Mad Max Fury Road", "dvd", client=None, creds=IGDB_CREDS,
        )

        assert result.outcome == "no_credential"
        assert result.provider == "tmdb"

    async def test_an_empty_tmdb_key_counts_as_missing(self, no_provider_calls):
        result = await title_lookup.lookup_by_title(
            "Mad Max Fury Road", "dvd", client=None, creds={"tmdb_api_key": ""},
        )

        assert result.outcome == "no_credential"

    async def test_one_missing_igdb_key_is_enough(self, no_provider_calls):
        result = await title_lookup.lookup_by_title(
            "Super Mario Odyssey", "video_game", client=None,
            creds={"igdb_client_id": "id-123"},
        )

        assert result.outcome == "no_credential"
        assert result.provider == "igdb"

    async def test_the_other_missing_igdb_key_is_enough(self, no_provider_calls):
        result = await title_lookup.lookup_by_title(
            "Super Mario Odyssey", "video_game", client=None,
            creds={"igdb_client_secret": "secret-123"},
        )

        assert result.outcome == "no_credential"

    async def test_both_missing(self, no_provider_calls):
        result = await title_lookup.lookup_by_title(
            "Super Mario Odyssey", "video_game", client=None, creds={},
        )

        assert result.outcome == "no_credential"


class TestEveryNonFoundOutcomeRidesThroughUnchanged:
    """The helper reports what the provider said; it never downgrades it."""

    @pytest.mark.parametrize("outcome", ["rejected", "rate_limited", "transport_failed"])
    async def test_a_film_outcome(self, tmdb_stub, outcome):
        stubbed = {
            "rejected": provider_result.rejected("tmdb", status=401),
            "rate_limited": provider_result.rate_limited("tmdb"),
            "transport_failed": provider_result.transport_failed("tmdb"),
        }[outcome]
        tmdb_stub(stubbed)

        result = await title_lookup.lookup_by_title(
            "Mad Max Fury Road", "dvd", client=None, creds=ALL_CREDS,
        )

        assert result.outcome == outcome
        assert result.provider == "tmdb"

    @pytest.mark.parametrize("outcome", ["rejected", "rate_limited", "transport_failed"])
    async def test_a_game_outcome(self, igdb_stub, outcome):
        stubbed = {
            "rejected": provider_result.rejected("igdb", status=400),
            "rate_limited": provider_result.rate_limited("igdb"),
            "transport_failed": provider_result.transport_failed("igdb"),
        }[outcome]
        igdb_stub(stubbed)

        result = await title_lookup.lookup_by_title(
            "Super Mario Odyssey", "video_game", client=None, creds=ALL_CREDS,
        )

        assert result.outcome == outcome
        assert result.provider == "igdb"


class TestTheDocstringsNeverRaisesIsEarned:
    """G66: the failure contract is asserted, not just written down.

    The clients do not raise today. This pins what happens if one ever does —
    a bulk confirm must not turn a provider bug into a 500.
    """

    async def test_a_raising_film_client_answers_transport_failed(self, monkeypatch):
        async def _raise(title, api_key, client):
            raise RuntimeError("boom")

        monkeypatch.setattr(tmdb, "lookup_by_title", _raise)

        result = await title_lookup.lookup_by_title(
            "Mad Max Fury Road", "dvd", client=None, creds=ALL_CREDS,
        )

        assert result.outcome == "transport_failed"
        assert result.provider == "tmdb"

    async def test_a_raising_game_client_answers_transport_failed(self, monkeypatch):
        async def _raise(title, client_id, client_secret, client, platform=None, limit=10):
            raise RuntimeError("boom")

        monkeypatch.setattr(igdb, "search_games", _raise)

        result = await title_lookup.lookup_by_title(
            "Super Mario Odyssey", "video_game", client=None, creds=ALL_CREDS,
        )

        assert result.outcome == "transport_failed"
        assert result.provider == "igdb"

    async def test_an_unindexable_payload_answers_transport_failed(self, igdb_stub):
        """`found` carrying something that is neither empty nor indexable."""
        igdb_stub(provider_result.found("igdb", 17))

        result = await title_lookup.lookup_by_title(
            "Super Mario Odyssey", "video_game", client=None, creds=ALL_CREDS,
        )

        assert result.outcome == "transport_failed"


class TestNoLogLineCarriesAUrl:
    """G76: TMDb's v3 key rides in `?api_key=`, and only the `httpx` logger
    carries `RedactQueryFilter`. Anything this module logs is unredacted."""

    async def test_a_raising_client_logs_no_url(self, monkeypatch, caplog):
        async def _raise(title, api_key, client):
            raise RuntimeError("https://api.themoviedb.org/3/search/movie?api_key=key-123")

        monkeypatch.setattr(tmdb, "lookup_by_title", _raise)

        with caplog.at_level("DEBUG", logger="app.services.title_lookup"):
            await title_lookup.lookup_by_title(
                "Mad Max Fury Road", "dvd", client=None, creds=ALL_CREDS,
            )

        for record in caplog.records:
            assert "http" not in record.getMessage()
            assert "api_key" not in record.getMessage()
