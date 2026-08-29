"""Twitch rejecting the IGDB credential pair is a distinct signal (issue #42).

The bug this pins: `_get_token` wrapped its request *and* its non-200 branch in
one `except Exception: return None`, so IGDB answered a rejected Client ID /
Secret with exactly what it answers for "no such game" — an empty list. A scan
of a game whose Twitch credentials had been revoked was filed as a bare title
under the "no match" copy, with nothing in the log above DEBUG.

`_get_token` now answers with a `ProviderResult` and `search_games` returns the
non-`found` one as it stands, so a rejected credential, a spent Twitch quota
(#49 part 1 — a channel the token endpoint never had) and a dead socket each
keep their own outcome. The other three entry points project today's contracts
from that record, because their return shapes and their callers' handlers
differ (GOTCHAS G45, G49).
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import covers, igdb, provider_result


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
    """The rejection has to escape `_get_token`'s own parse handler, or nothing changes."""

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
    async def test_a_rejected_credential_is_rejected(self, status, fake_fetch):
        fake_fetch.return_value = StubResponse(status)
        result = await igdb._get_token(CLIENT_ID, CLIENT_SECRET, object())
        assert result.outcome == "rejected"
        assert result.status == status

    @pytest.mark.parametrize("status", (400, 401, 403))
    async def test_the_rejection_reaches_search_games(self, status, fake_fetch):
        """The whole point: the signal reaches the scan path, not just the token call."""
        fake_fetch.return_value = StubResponse(status)
        result = await igdb.search_games("Halo", CLIENT_ID, CLIENT_SECRET, object())
        assert result.outcome == "rejected"

    async def test_a_429_from_the_token_endpoint_is_rate_limited(self, fake_fetch):
        """Issue #49 part 1: the token exchange had no quota channel at all.

        `_get_token` returned a bare `str | None`, so a spent Twitch quota was
        the same `None` as a bad secret and reached the card as "no such game".
        """
        fake_fetch.return_value = StubResponse(429)
        assert (await igdb._get_token(
            CLIENT_ID, CLIENT_SECRET, object())).outcome == "rate_limited"
        assert (await igdb.search_games(
            "Halo", CLIENT_ID, CLIENT_SECRET, object())).outcome == "rate_limited"

    async def test_a_500_from_the_token_endpoint_is_not_an_auth_failure(self, fake_fetch):
        """A provider outage is not a rejected key — a miss, never a rejection."""
        fake_fetch.return_value = StubResponse(500)
        assert (await igdb._get_token(
            CLIENT_ID, CLIENT_SECRET, object())).outcome == "no_match"
        assert (await igdb.search_games(
            "Halo", CLIENT_ID, CLIENT_SECRET, object())).outcome == "no_match"

    async def test_a_transport_exception_is_not_an_auth_failure(self, fake_fetch):
        """A network blip is not a credential problem — the comment in the code says so."""
        fake_fetch.side_effect = RuntimeError("boom")
        assert (await igdb._get_token(
            CLIENT_ID, CLIENT_SECRET, object())).outcome == "transport_failed"
        assert (await igdb.search_games(
            "Halo", CLIENT_ID, CLIENT_SECRET, object())).outcome == "transport_failed"

    async def test_the_rejection_is_logged_at_warning_and_names_the_status(
        self, fake_fetch, caplog
    ):
        """Half of #42 is what a log reader sees; DEBUG was invisible in practice."""
        fake_fetch.return_value = StubResponse(401)
        with caplog.at_level(logging.WARNING, logger="app.services.igdb"):
            await igdb._get_token(CLIENT_ID, CLIENT_SECRET, object())
        assert "401" in caplog.text
        assert "rejected" in caplog.text.lower()


class TestTheOtherThreeEntryPointsAbsorbIt:
    """Three callers with three return shapes and no handlers (G45, G49).

    `search_games` is the only entry point that carries the outcome outward.
    Each of these projects today's contract from the record on purpose, and
    the reason differs per caller — so each gets its own pin rather than one
    shared assumption.
    """

    async def test_search_game_art_reports_the_rejection(self, fake_fetch):
        """Was pinned at `[]`; issue #49 is what opens its rejection channel.

        "Never raises" is the half of its contract that survives — what went
        is `[]`-for-everything, which is why a bad key looked like a game with
        no art in the cover picker.
        """
        fake_fetch.return_value = StubResponse(403)
        result = await igdb.search_game_art("Halo", CLIENT_ID, CLIENT_SECRET, object())
        assert result.outcome == "rejected"
        assert result.provider == "igdb"

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

    async def test_the_cover_picker_carries_the_rejection_out(self, fake_fetch):
        """The end of the chain this file has been pinning since issue #42.

        Twitch rejects the pair → `_get_token` says `rejected` → `search_game_art`
        returns it as it stands → `_igdb_candidates` propagates → `search_covers`
        hands it to the route, which projects it onto the picker's notice.
        Pinned at `[]` twice before ("plan 2 changes that" — this is plan 2).
        """
        fake_fetch.return_value = StubResponse(403)
        item = {"media_type": "video_game", "title": "Halo", "authors": "", "platform": "xbox"}
        result = await covers.search_covers(
            item, "Halo", object(),
            creds={"igdb_client_id": CLIENT_ID, "igdb_client_secret": CLIENT_SECRET},
        )
        assert result.outcome == "rejected"


class TestSearchGamesReportsARateLimit:
    """The *search* call's own 429, distinct from the token endpoint's above."""

    async def test_a_429_on_the_search_call_is_rate_limited(self, fake_fetch):
        fake_fetch.return_value = StubResponse(429)
        igdb._token_cache[(CLIENT_ID, CLIENT_SECRET)] = ("tok", 1e12)
        result = await igdb.search_games("Halo", CLIENT_ID, CLIENT_SECRET, object())
        assert result.outcome == "rate_limited"
        assert result.payload is None

    async def test_an_empty_result_set_is_a_miss_not_a_quota(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data=[])
        igdb._token_cache[(CLIENT_ID, CLIENT_SECRET)] = ("tok", 1e12)
        assert (await igdb.search_games(
            "Halo", CLIENT_ID, CLIENT_SECRET, object())).outcome == "no_match"

    async def test_a_401_on_the_search_call_is_a_rejection_that_evicts(self, fake_fetch):
        """Issue #49's remainder, now closed — this is the pin that went red.

        `_AUTH_STATUSES` applies to the *token* endpoint; the search leg has
        its own `_SEARCH_AUTH_STATUSES`. A 401 here means the token Twitch
        handed us is no longer honoured, so the cached copy is evicted: it is
        usually still unexpired, and without eviction the same dead bearer is
        re-presented until it ages out, with no way for the user to retry.
        """
        fake_fetch.return_value = StubResponse(401)
        igdb._token_cache[(CLIENT_ID, CLIENT_SECRET)] = ("tok", 1e12)
        result = await igdb.search_games("Halo", CLIENT_ID, CLIENT_SECRET, object())
        assert result.outcome == "rejected"
        assert (CLIENT_ID, CLIENT_SECRET) not in igdb._token_cache

    async def test_a_403_on_the_search_call_also_evicts(self, fake_fetch):
        fake_fetch.return_value = StubResponse(403)
        igdb._token_cache[(CLIENT_ID, CLIENT_SECRET)] = ("tok", 1e12)
        result = await igdb.search_games("Halo", CLIENT_ID, CLIENT_SECRET, object())
        assert result.outcome == "rejected"
        assert (CLIENT_ID, CLIENT_SECRET) not in igdb._token_cache

    async def test_a_400_on_the_search_call_is_a_miss_and_keeps_the_token(self, fake_fetch):
        """The one status that is in `_AUTH_STATUSES` and *not* in the search set.

        Twitch answers a bad client id with 400 on the **token** endpoint, so
        that set carries it. IGDB answers a malformed Apicalypse query with
        400 on **`/games`** — a Shelf bug in our own query builder, not a
        rejected credential. Classifying it as `rejected` would send the user
        to Settings to fix a key that works, and would throw away a perfectly
        good token on the way.
        """
        fake_fetch.return_value = StubResponse(400)
        igdb._token_cache[(CLIENT_ID, CLIENT_SECRET)] = ("tok", 1e12)
        result = await igdb.search_games("Halo", CLIENT_ID, CLIENT_SECRET, object())
        assert result.outcome == "no_match"
        assert igdb._token_cache[(CLIENT_ID, CLIENT_SECRET)] == ("tok", 1e12)

    async def test_a_429_keeps_the_token(self, fake_fetch):
        """Only a rejection evicts — a quota miss says nothing about the key."""
        fake_fetch.return_value = StubResponse(429)
        igdb._token_cache[(CLIENT_ID, CLIENT_SECRET)] = ("tok", 1e12)
        result = await igdb.search_games("Halo", CLIENT_ID, CLIENT_SECRET, object())
        assert result.outcome == "rate_limited"
        assert igdb._token_cache[(CLIENT_ID, CLIENT_SECRET)] == ("tok", 1e12)

    async def test_the_next_search_after_a_rejection_re_exchanges_the_token(
        self, fake_fetch
    ):
        """What the eviction is *for*, asserted end to end.

        Without it the second search reuses the cached bearer and never talks
        to Twitch again, so a user who fixes their app in the Twitch console
        stays broken until the process restarts.
        """
        igdb._token_cache[(CLIENT_ID, CLIENT_SECRET)] = ("dead-token", 1e12)
        fake_fetch.return_value = StubResponse(401)
        await igdb.search_games("Halo", CLIENT_ID, CLIENT_SECRET, object())

        calls = []

        async def _record(client, method, url, **kwargs):
            calls.append(url)
            if url == igdb.TWITCH_TOKEN_URL:
                return StubResponse(200, json_data={
                    "access_token": "fresh-token", "expires_in": 3600,
                })
            return StubResponse(200, json_data=[{"id": 1, "name": "Halo"}])

        fake_fetch.side_effect = _record
        result = await igdb.search_games("Halo", CLIENT_ID, CLIENT_SECRET, object())

        assert igdb.TWITCH_TOKEN_URL in calls, calls
        assert result.found
        assert igdb._token_cache[(CLIENT_ID, CLIENT_SECRET)][0] == "fresh-token"

    async def test_a_hit_carries_the_list_as_its_payload(self, fake_fetch):
        """G45: the payload stays a list — the router unwraps `[0]`, not this."""
        fake_fetch.return_value = StubResponse(200, json_data=[{"id": 1, "name": "Halo"}])
        igdb._token_cache[(CLIENT_ID, CLIENT_SECRET)] = ("tok", 1e12)
        result = await igdb.search_games("Halo", CLIENT_ID, CLIENT_SECRET, object())
        assert result.found
        assert isinstance(result.payload, list)
        assert result.payload[0]["title"] == "Halo"


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
        assert "IGDB rejected the configured key" in resp.text
        assert 'data-search-status="rejected"' in resp.text

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
        monkeypatch.setattr(covers, "igdb", stub)
        # Assert on the *value*, not a count — a coroutine has a length too.
        assert covers.igdb.image_url("abc") == "https://x/abc.jpg"
