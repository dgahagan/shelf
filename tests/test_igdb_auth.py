"""Twitch rejecting the IGDB credential pair is a distinct signal (issue #42).

The bug this pins: `_get_token` wrapped its request *and* its non-200 branch in
one `except Exception: return None`, so IGDB answered a rejected Client ID /
Secret with exactly what it answers for "no such game" — an empty list. A scan
of a game whose Twitch credentials had been revoked was filed as a bare title
under the "no match" copy, with nothing in the log above DEBUG.

`search_games` now raises `IgdbAuthError`; the other three entry points each
decide for themselves, because their return shapes and their callers' handlers
differ (GOTCHAS G45, G49).
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import covers, igdb


CLIENT_ID = "abcdef0123456789"
CLIENT_SECRET = "secret0123456789"


class StubResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data
        self.text = text
        self.headers = {}

    def json(self):
        return self._json


@pytest.fixture(autouse=True)
def _clear_token_cache():
    """G13: `_token_cache` is module-level and keyed on the credential pair.

    conftest resets it between tests, but these tests reuse one pair across
    many cases in a single file, so a cached token from an earlier case would
    short-circuit the request the next case is about.
    """
    igdb._token_cache.clear()
    yield
    igdb._token_cache.clear()


@pytest.fixture
def fake_fetch():
    # G37: patch on the module that *defines* fetch, which is what igdb.py
    # resolves through `from app.services import outbound`.
    with patch("app.services.outbound.fetch", new=AsyncMock()) as m:
        yield m


TOKEN_OK = StubResponse(200, json_data={"access_token": "tok", "expires_in": 3600})


class TestGetTokenAuthSignal:
    """The raise has to escape `_get_token`'s own handler, or nothing changes."""

    async def test_the_auth_statuses_are_the_ones_twitch_answers(self):
        """Pinned as a literal, not read from the constant.

        The two tests below used to parametrize over `igdb._AUTH_STATUSES`, so
        narrowing the tuple shrank the test set with it and the suite stayed
        green on a real regression. 400 is the member Twitch actually answers
        for a bogus client_id/secret pair (measured 2026-08-27), so it is the
        one a narrowing would drop first.
        """
        assert igdb._AUTH_STATUSES == (400, 401, 403)

    @pytest.mark.parametrize("status", (400, 401, 403))
    async def test_a_rejected_credential_raises(self, status, fake_fetch):
        fake_fetch.return_value = StubResponse(status)
        with pytest.raises(igdb.IgdbAuthError):
            await igdb._get_token(CLIENT_ID, CLIENT_SECRET, object())

    @pytest.mark.parametrize("status", (400, 401, 403))
    async def test_a_rejected_credential_raises_out_of_search_games(self, status, fake_fetch):
        """The whole point: the signal reaches the scan path, not just the token call."""
        fake_fetch.return_value = StubResponse(status)
        with pytest.raises(igdb.IgdbAuthError):
            await igdb.search_games("Halo", CLIENT_ID, CLIENT_SECRET, object())

    async def test_a_500_from_the_token_endpoint_is_not_an_auth_failure(self, fake_fetch):
        """A provider outage is not a rejected key — `None`/`[]`, never a raise."""
        fake_fetch.return_value = StubResponse(500)
        assert await igdb._get_token(CLIENT_ID, CLIENT_SECRET, object()) is None
        assert await igdb.search_games("Halo", CLIENT_ID, CLIENT_SECRET, object()) == []

    async def test_a_transport_exception_is_not_an_auth_failure(self, fake_fetch):
        """A network blip is not a credential problem — the comment in the code says so."""
        fake_fetch.side_effect = RuntimeError("boom")
        assert await igdb._get_token(CLIENT_ID, CLIENT_SECRET, object()) is None
        assert await igdb.search_games("Halo", CLIENT_ID, CLIENT_SECRET, object()) == []

    async def test_the_rejection_is_logged_at_warning_and_names_the_status(
        self, fake_fetch, caplog
    ):
        """Half of #42 is what a log reader sees; DEBUG was invisible in practice."""
        fake_fetch.return_value = StubResponse(401)
        with caplog.at_level(logging.WARNING, logger="app.services.igdb"):
            with pytest.raises(igdb.IgdbAuthError):
                await igdb._get_token(CLIENT_ID, CLIENT_SECRET, object())
        assert "401" in caplog.text
        assert "rejected" in caplog.text.lower()


class TestTheOtherThreeEntryPointsAbsorbIt:
    """Three callers with three return shapes and no handlers (G45, G49).

    `search_games` is the only entry point that propagates. Each of these
    swallows the signal on purpose, and the reason differs per caller — so
    each gets its own pin rather than one shared assumption.
    """

    async def test_search_game_art_still_returns_an_empty_list(self, fake_fetch):
        """Its docstring says "never raises" and the cover picker depends on it."""
        fake_fetch.return_value = StubResponse(403)
        assert await igdb.search_game_art("Halo", CLIENT_ID, CLIENT_SECRET, object()) == []

    async def test_lookup_game_still_returns_none(self, fake_fetch):
        """`items_catalog.add_game_from_search` has no handler — this would be a 500."""
        fake_fetch.return_value = StubResponse(403)
        assert await igdb.lookup_game(1234, CLIENT_ID, CLIENT_SECRET, object()) is None

    async def test_test_credentials_returns_todays_exact_body(self, fake_fetch):
        """Asserted against the literal string: "same as today" *is* the acceptance."""
        fake_fetch.return_value = StubResponse(401)
        assert await igdb.test_credentials(CLIENT_ID, CLIENT_SECRET, object()) == {
            "ok": False,
            "message": "Authentication failed — check Client ID and Secret",
        }

    async def test_the_cover_picker_still_returns_an_empty_list(self, fake_fetch):
        """`covers.search_covers` says "Never raises" — plan 2 changes that, not this one."""
        fake_fetch.return_value = StubResponse(403)
        item = {"media_type": "video_game", "title": "Halo", "authors": "", "platform": "xbox"}
        result = await covers.search_covers(
            item, "Halo", object(),
            creds={"igdb_client_id": CLIENT_ID, "igdb_client_secret": CLIENT_SECRET},
        )
        assert result == []


class TestSearchGamesReportsARateLimit:
    async def test_a_429_calls_the_callback_once(self, fake_fetch):
        fake_fetch.return_value = StubResponse(429)
        igdb._token_cache[(CLIENT_ID, CLIENT_SECRET)] = ("tok", 1e12)
        seen = []
        assert await igdb.search_games(
            "Halo", CLIENT_ID, CLIENT_SECRET, object(), on_rate_limit=lambda: seen.append(1)
        ) == []
        assert seen == [1]

    async def test_a_hit_does_not_call_the_callback(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data=[])
        igdb._token_cache[(CLIENT_ID, CLIENT_SECRET)] = ("tok", 1e12)
        seen = []
        await igdb.search_games(
            "Halo", CLIENT_ID, CLIENT_SECRET, object(), on_rate_limit=lambda: seen.append(1)
        )
        assert seen == []

    async def test_the_callback_is_optional(self, fake_fetch):
        """Default `None` is what keeps `items_catalog` and every existing test identical."""
        fake_fetch.return_value = StubResponse(429)
        igdb._token_cache[(CLIENT_ID, CLIENT_SECRET)] = ("tok", 1e12)
        assert await igdb.search_games("Halo", CLIENT_ID, CLIENT_SECRET, object()) == []


class TestTheRoutesDoNotFiveHundred:
    """The three HTTP surfaces a rejected credential can reach."""

    @pytest.fixture(autouse=True)
    def _creds(self, db):
        db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("igdb_client_id", CLIENT_ID),
        )
        db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("igdb_client_secret", CLIENT_SECRET),
        )
        # G48: commit before the request — the route opens its own connection.
        db.commit()

    def test_game_title_search_renders_an_error_block_not_a_500(
        self, editor_client, fake_fetch
    ):
        fake_fetch.return_value = StubResponse(403)
        # GET, not POST — the plan says POST; the route is
        # `@router.get("/games/search")` (`app/routers/items_catalog.py:31`).
        resp = editor_client.get("/api/games/search", params={"q": "Halo", "platform": ""})
        assert resp.status_code == 200
        assert "IGDB rejected the configured credentials" in resp.text

    def test_add_game_renders_the_existing_failure_card_not_a_500(
        self, editor_client, fake_fetch
    ):
        fake_fetch.return_value = StubResponse(403)
        resp = editor_client.post("/api/games/add", data={"igdb_id": "1234", "platform": ""})
        assert resp.status_code == 200
        assert "Failed to fetch game details from IGDB" in resp.text

    def test_the_settings_key_test_returns_todays_json(self, admin_client, fake_fetch):
        fake_fetch.return_value = StubResponse(401)
        resp = admin_client.post(
            "/api/igdb/test-key", json={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "ok": False,
            "message": "Authentication failed — check Client ID and Secret",
        }


class TestStubbingTheModuleWholesale:
    """G56: `AsyncMock()` over a module poisons its sync helpers silently."""

    async def test_sync_helpers_must_be_magicmock(self, monkeypatch):
        stub = AsyncMock()
        stub.image_url = MagicMock(side_effect=lambda i, size="t_cover_big": f"https://x/{i}.jpg")
        stub.IgdbAuthError = igdb.IgdbAuthError
        monkeypatch.setattr(covers, "igdb", stub)
        # Assert on the *value*, not a count — a coroutine has a length too.
        assert covers.igdb.image_url("abc") == "https://x/abc.jpg"
