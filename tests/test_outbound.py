"""Tests for the shared outbound politeness layer (app/services/outbound.py).

Nothing here touches the network and nothing here actually waits: every test
patches `outbound._sleep` so the recorded delays are asserted instead of
slept. `app.config.HOST_RATE_LIMITS` is overridden per-host with
`monkeypatch.setitem`, which works regardless of how the module binds it.
"""

import asyncio
import logging

import httpx
import pytest

import app.config
from app.services import outbound


@pytest.fixture
def recorded_sleeps(monkeypatch):
    """Patch out the module's sleep, recording what it was asked to wait."""
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(outbound, "_sleep", fake_sleep)
    return sleeps


class StubResponse:
    """Minimal httpx.Response stand-in for the paths fetch() inspects."""

    def __init__(self, status_code=200, headers=None, url="https://example.test/x"):
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url


class RecordingClient:
    """AsyncClient stand-in: replays a script and records every call."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        item = self._script.pop(0) if self._script else StubResponse()
        if isinstance(item, Exception):
            raise item
        return item


# --------------------------------------------------------------------------
# acquire()
# --------------------------------------------------------------------------


def test_acquire_unknown_host_does_not_sleep(recorded_sleeps):
    """A host with no table entry is unpaced — no lock, no wait."""
    asyncio.run(outbound.acquire("unlisted.example"))
    assert recorded_sleeps == []


def test_acquire_first_call_is_immediate(monkeypatch, recorded_sleeps):
    monkeypatch.setitem(app.config.HOST_RATE_LIMITS, "h.test", 0.05)
    asyncio.run(outbound.acquire("h.test"))
    assert recorded_sleeps == []


def test_acquire_second_call_waits_the_interval(monkeypatch, recorded_sleeps):
    monkeypatch.setitem(app.config.HOST_RATE_LIMITS, "h.test", 0.05)

    async def scenario():
        await outbound.acquire("h.test")
        await outbound.acquire("h.test")

    asyncio.run(scenario())
    assert len(recorded_sleeps) == 1
    assert 0 < recorded_sleeps[0] <= 0.05


def test_acquire_serialises_concurrent_callers_for_one_host(monkeypatch):
    """Race pin: the lock must be held *across* the sleep.

    The old read-sleep-write shape let two concurrent callers both read the
    same stale timestamp and then fire together — and counting sleeps does
    not catch it, because both callers do sleep. The property that actually
    separates the two shapes is whether the second caller observes the
    *first caller's updated* timestamp: under a real lock it does and waits a
    further full interval, without one it reads the stale value and returns
    early.

    Driven by a fake monotonic clock that the patched sleep advances, so the
    assertion is on ordering rather than on wall-clock timing.
    """
    monkeypatch.setitem(app.config.HOST_RATE_LIMITS, "h.test", 0.05)

    clock = [0.0]

    class FakeTime:
        @staticmethod
        def monotonic():
            return clock[0]

    async def fake_sleep(delay):
        clock[0] += delay
        await asyncio.sleep(0)  # yield: give a non-locking impl its chance

    monkeypatch.setattr(outbound, "time", FakeTime)
    monkeypatch.setattr(outbound, "_sleep", fake_sleep)

    async def scenario():
        # Prime the limiter so both racers below have to wait.
        await outbound.acquire("h.test")
        returned_at = []

        async def caller():
            await outbound.acquire("h.test")
            returned_at.append(clock[0])

        await asyncio.gather(caller(), caller())
        return returned_at

    returned_at = asyncio.run(scenario())

    assert len(returned_at) == 2
    # Serialised: the two requests are a full interval apart. If the lock is
    # dropped across the sleep both callers return at the same instant.
    assert abs(returned_at[1] - returned_at[0]) >= 0.05


def test_acquire_different_hosts_do_not_block_each_other(monkeypatch, recorded_sleeps):
    monkeypatch.setitem(app.config.HOST_RATE_LIMITS, "a.test", 0.05)
    monkeypatch.setitem(app.config.HOST_RATE_LIMITS, "b.test", 0.05)

    async def scenario():
        await outbound.acquire("a.test")
        await outbound.acquire("b.test")

    asyncio.run(scenario())
    assert recorded_sleeps == []


def test_reset_clears_the_registry(monkeypatch, recorded_sleeps):
    monkeypatch.setitem(app.config.HOST_RATE_LIMITS, "h.test", 0.05)
    asyncio.run(outbound.acquire("h.test"))
    assert outbound._hosts
    outbound.reset()
    assert outbound._hosts == {}
    # After a reset the next call is "first" again.
    asyncio.run(outbound.acquire("h.test"))
    assert recorded_sleeps == []


# --------------------------------------------------------------------------
# fetch() — retry contract
# --------------------------------------------------------------------------


def test_fetch_returns_success_without_retrying(recorded_sleeps):
    client = RecordingClient([StubResponse(200)])
    resp = asyncio.run(outbound.fetch(client, "GET", "https://example.test/x"))
    assert resp.status_code == 200
    assert len(client.calls) == 1
    assert recorded_sleeps == []


def test_fetch_retries_connect_error_then_succeeds(recorded_sleeps):
    client = RecordingClient([httpx.ConnectError("boom"), StubResponse(200)])
    resp = asyncio.run(outbound.fetch(client, "GET", "https://example.test/x"))
    assert resp.status_code == 200
    assert len(client.calls) == 2
    assert len(recorded_sleeps) == 1


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_fetch_retries_transient_statuses(status, recorded_sleeps):
    client = RecordingClient([StubResponse(status), StubResponse(200)])
    resp = asyncio.run(outbound.fetch(client, "GET", "https://example.test/x"))
    assert resp.status_code == 200
    assert len(client.calls) == 2


@pytest.mark.parametrize("status", [400, 404, 500])
def test_fetch_does_not_retry_permanent_statuses(status, recorded_sleeps):
    client = RecordingClient([StubResponse(status), StubResponse(200)])
    resp = asyncio.run(outbound.fetch(client, "GET", "https://example.test/x"))
    assert resp.status_code == status
    assert len(client.calls) == 1
    assert recorded_sleeps == []


def test_fetch_does_not_retry_403(recorded_sleeps):
    """Open Library returns 403 when the covers rate limit is exceeded.

    It is permanent for the window, so retrying only deepens the hole — and
    because `_download` reads a non-200 as "no cover", the miss is silent.
    That is why `covers.openlibrary.org`'s interval has to be right.
    """
    client = RecordingClient([StubResponse(403), StubResponse(200)])
    resp = asyncio.run(outbound.fetch(client, "GET", "https://covers.openlibrary.org/x"))
    assert resp.status_code == 403
    assert len(client.calls) == 1
    assert recorded_sleeps == []


def test_fetch_gives_up_and_returns_last_transient_response(recorded_sleeps):
    client = RecordingClient([StubResponse(503), StubResponse(503), StubResponse(503)])
    resp = asyncio.run(outbound.fetch(client, "GET", "https://example.test/x", retries=2))
    assert resp.status_code == 503
    assert len(client.calls) == 3  # initial + 2 retries
    assert len(recorded_sleeps) == 2


def test_fetch_reraises_the_original_exception_unchanged(recorded_sleeps):
    """Callers catch httpx exception types by class — never wrap them."""
    err = httpx.ConnectError("still down")
    client = RecordingClient([err, err, err])
    with pytest.raises(httpx.ConnectError) as excinfo:
        asyncio.run(outbound.fetch(client, "GET", "https://example.test/x", retries=2))
    assert excinfo.value is err
    assert len(client.calls) == 3


def test_fetch_honours_numeric_retry_after_without_jitter(recorded_sleeps):
    client = RecordingClient(
        [StubResponse(429, headers={"Retry-After": "2"}), StubResponse(200)]
    )
    asyncio.run(outbound.fetch(client, "GET", "https://example.test/x"))
    assert recorded_sleeps == [2.0]


@pytest.mark.parametrize("status", [429, 503])
def test_fetch_does_not_retry_a_retry_after_beyond_the_ceiling(status, recorded_sleeps):
    """Issue #47: a stated wait past the ceiling is a spent quota, not a blip.

    Capping it did not shorten the wait, it discarded the one piece of
    information saying a retry is pointless — `api.upcitemdb.com` sent
    `Retry-After: 4274.5` and one lookup cost 60.4s, a four-rung scan 240.25s.
    The response now comes back on the first answer.
    """
    client = RecordingClient(
        [StubResponse(status, headers={"Retry-After": "9999"}), StubResponse(200)]
    )
    resp = asyncio.run(outbound.fetch(client, "GET", "https://example.test/x"))
    assert resp.status_code == status
    assert len(client.calls) == 1
    assert recorded_sleeps == []


def test_fetch_serves_a_retry_after_at_the_ceiling(recorded_sleeps):
    """Exactly RETRY_AFTER_MAX is still a wait we are willing to serve."""
    client = RecordingClient(
        [StubResponse(429, headers={"Retry-After": "30"}), StubResponse(200)]
    )
    resp = asyncio.run(outbound.fetch(client, "GET", "https://example.test/x"))
    assert resp.status_code == 200
    assert recorded_sleeps == [outbound.RETRY_AFTER_MAX]
    assert len(client.calls) == 2


def test_fetch_stops_on_a_beyond_ceiling_retry_after_on_the_final_attempt(
    caplog, recorded_sleeps
):
    """The header is read before the `not last` test, so the last attempt sees it too."""
    client = RecordingClient(
        [StubResponse(503), StubResponse(503, headers={"Retry-After": "9999"})]
    )
    with caplog.at_level(logging.WARNING, logger="app.services.outbound"):
        resp = asyncio.run(
            outbound.fetch(client, "GET", "https://example.test/x", retries=1)
        )
    assert resp.status_code == 503
    assert len(client.calls) == 2
    assert len(recorded_sleeps) == 1  # the headerless first response backed off
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "not retrying" in warnings[0].getMessage()


def test_fetch_warns_once_naming_the_host_when_a_quota_is_spent(caplog, recorded_sleeps):
    """A self-hoster must be able to tell from the default log level."""
    client = RecordingClient(
        [StubResponse(429, headers={"Retry-After": "4274.5"}), StubResponse(200)]
    )
    with caplog.at_level(logging.WARNING, logger="app.services.outbound"):
        asyncio.run(outbound.fetch(client, "GET", "https://example.test/x"))
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "example.test" in message
    assert "not retrying" in message
    assert "https://example.test/x" not in message  # the host, not the query string


@pytest.mark.parametrize(
    "headers", [{"Retry-After": "2"}, {}], ids=["within-ceiling", "headerless"]
)
def test_fetch_stays_at_debug_for_a_servable_wait(headers, caplog, recorded_sleeps):
    client = RecordingClient([StubResponse(429, headers=headers), StubResponse(200)])
    with caplog.at_level(logging.WARNING, logger="app.services.outbound"):
        asyncio.run(outbound.fetch(client, "GET", "https://example.test/x"))
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
    assert len(recorded_sleeps) == 1


def test_fetch_ignores_http_date_retry_after(recorded_sleeps):
    """A date-form Retry-After falls back to our own backoff, not a crash."""
    client = RecordingClient(
        [
            StubResponse(503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
            StubResponse(200),
        ]
    )
    asyncio.run(outbound.fetch(client, "GET", "https://example.test/x"))
    assert len(recorded_sleeps) == 1
    assert 0 < recorded_sleeps[0] <= outbound.BACKOFF_MAX


@pytest.mark.parametrize("raw", ["nan", "NaN", "-nan"], ids=["nan", "NaN", "-nan"])
def test_fetch_ignores_a_nan_retry_after(raw, recorded_sleeps):
    """A non-numeric-but-float()-able header must not reach `_sleep`.

    `float("nan")` passes `float()`, then fails *every* comparison: it slips
    the `< 0` guard in `_retry_after_seconds` and the caller's
    `> RETRY_AFTER_MAX` ceiling test alike, so the raw NaN becomes the delay.
    `asyncio.sleep(nan)` then raises, and callers without a broad `except` —
    `googlebooks.lookup`, `items_catalog.py` — turn that into an HTTP 500.

    Asserted on the delay, never on the exception text: which loop raises and
    what it says both move with the runtime. The shipping 3.12 image gave
    uvloop's `cannot convert float NaN to integer` while its plain asyncio
    returned normally; under Python 3.14 with uvloop 0.22.1 both raise
    `Invalid delay: NaN`. A pin worded against either message proves nothing
    on the other interpreter.
    """
    client = RecordingClient(
        [StubResponse(503, headers={"Retry-After": raw}), StubResponse(200)]
    )
    resp = asyncio.run(outbound.fetch(client, "GET", "https://example.test/x"))
    assert resp.status_code == 200
    assert len(client.calls) == 2
    assert len(recorded_sleeps) == 1
    delay = recorded_sleeps[0]
    assert delay == delay, "NaN reached _sleep"  # NaN is the only value that fails this
    assert 0 < delay <= outbound.BACKOFF_MAX * (1 + outbound.JITTER)


def test_fetch_backoff_grows_and_is_capped(recorded_sleeps):
    client = RecordingClient([StubResponse(503)] * 6)
    asyncio.run(outbound.fetch(client, "GET", "https://example.test/x", retries=5))
    assert len(recorded_sleeps) == 5
    assert all(d <= outbound.BACKOFF_MAX * (1 + outbound.JITTER) for d in recorded_sleeps)
    # Jitter is +/-25%, so successive uncapped delays still separate cleanly.
    assert recorded_sleeps[1] > recorded_sleeps[0]


# --------------------------------------------------------------------------
# fetch() — timeout opt-in (keeps scan latency at one HTTP_TIMEOUT)
# --------------------------------------------------------------------------


def test_fetch_does_not_retry_timeouts_by_default(recorded_sleeps):
    """Request-path callers must pay one timeout budget, not three."""
    err = httpx.ReadTimeout("slow")
    client = RecordingClient([err, StubResponse(200)])
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(outbound.fetch(client, "GET", "https://example.test/x"))
    assert len(client.calls) == 1
    assert recorded_sleeps == []


def test_fetch_retries_timeouts_when_opted_in(recorded_sleeps):
    client = RecordingClient([httpx.ReadTimeout("slow"), StubResponse(200)])
    resp = asyncio.run(
        outbound.fetch(client, "GET", "https://example.test/x", retry_timeouts=True)
    )
    assert resp.status_code == 200
    assert len(client.calls) == 2


def test_fetch_opted_in_timeout_still_reraises_unchanged(recorded_sleeps):
    err = httpx.ConnectTimeout("never")
    client = RecordingClient([err, err, err])
    with pytest.raises(httpx.ConnectTimeout) as excinfo:
        asyncio.run(
            outbound.fetch(
                client, "GET", "https://example.test/x", retries=2, retry_timeouts=True
            )
        )
    assert excinfo.value is err
    assert len(client.calls) == 3


# --------------------------------------------------------------------------
# fetch() — pass-through and host resolution
# --------------------------------------------------------------------------


def test_fetch_passes_kwargs_through_and_consumes_its_own(recorded_sleeps):
    client = RecordingClient([StubResponse(200)])
    asyncio.run(
        outbound.fetch(
            client,
            "POST",
            "https://example.test/x",
            retries=3,
            retry_timeouts=True,
            params={"q": "dune"},
            headers={"User-Agent": "Shelf/1.0"},
            follow_redirects=True,
            timeout=10,
            content=b"body",
        )
    )
    method, url, kwargs = client.calls[0]
    assert method == "POST"
    assert url == "https://example.test/x"
    assert kwargs == {
        "params": {"q": "dune"},
        "headers": {"User-Agent": "Shelf/1.0"},
        "follow_redirects": True,
        "timeout": 10,
        "content": b"body",
    }
    assert "retries" not in kwargs
    assert "retry_timeouts" not in kwargs


def test_fetch_acquires_the_url_host_once_per_attempt(monkeypatch, recorded_sleeps):
    seen: list[str] = []

    async def fake_acquire(host):
        seen.append(host)

    monkeypatch.setattr(outbound, "acquire", fake_acquire)
    client = RecordingClient([StubResponse(503), StubResponse(200)])
    asyncio.run(outbound.fetch(client, "GET", "https://covers.openlibrary.org/b/isbn/x.jpg"))
    assert seen == ["covers.openlibrary.org", "covers.openlibrary.org"]


# --------------------------------------------------------------------------
# is_rate_limited — "should the user be told to come back?"
# --------------------------------------------------------------------------


class TestIsRateLimited:
    """The predicate every metadata client consumes to report a spent quota.

    None of these need `recorded_sleeps`: the predicate makes no request and
    never sleeps. It reads a response the caller already holds.
    """

    def test_a_bare_429_is_rate_limited(self):
        """429 with no Retry-After at all — the shape that motivates this.

        Measured (design plan Probe 1): Google Books 429s this workstation
        with no `Retry-After` and no `X-RateLimit-*` header, 3/3. A
        header-keyed predicate would answer False for the one provider
        actually rate-limiting us.
        """
        assert outbound.is_rate_limited(StubResponse(429)) is True

    def test_a_429_with_a_huge_retry_after_is_still_rate_limited(self):
        """The header is not consulted in either direction."""
        resp = StubResponse(429, headers={"Retry-After": "9999"})
        assert outbound.is_rate_limited(resp) is True

    def test_a_200_is_not_rate_limited(self):
        assert outbound.is_rate_limited(StubResponse(200)) is False

    @pytest.mark.parametrize("status", [200, 403, 500, 502, 503, 504])
    def test_non_429_statuses_are_not_rate_limited(self, status):
        """503 is False on purpose — do not "fix" this to match RETRY_STATUSES.

        `RETRY_STATUSES` answers "is another attempt worth making?" and
        includes 502/503/504, which are gateway and outage failures. This
        predicate answers "should the user be told to come back later?", and
        telling a user their scan was rate-limited when the provider is simply
        down sends them to do the wrong thing. 403 stays out because Open
        Library returns it both for a spent covers quota and for a plain
        authorization failure.
        """
        assert outbound.is_rate_limited(StubResponse(status)) is False

    def test_rate_limited_statuses_are_a_strict_subset_of_retryable(self):
        """Structural pin: every rate-limited response is also retryable.

        A transport failure is not a response, so it can never reach this
        predicate — the only inputs are responses `fetch` (or a bare
        `client.get`) already returned. What must hold is that nothing can be
        called rate-limited without also being worth retrying, and that the
        two sets stay distinct.
        """
        assert outbound.RATE_LIMIT_STATUSES < outbound.RETRY_STATUSES

        import inspect

        params = list(inspect.signature(outbound.is_rate_limited).parameters)
        assert params == ["resp"]


# --------------------------------------------------------------------------
# Posture tripwire
# --------------------------------------------------------------------------


def test_module_contains_no_private_ip_validation():
    """GOTCHAS G12: this module throttles, it never validates.

    Shelf is self-hosted — RFC1918/loopback blocking broke real deployments
    once already and was reverted. A security pass pattern-matching
    "server fetches a URL" must not re-add it here.
    """
    import inspect

    source = inspect.getsource(outbound)
    body = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    for needle in ("is_private", "ip_address", "getaddrinfo"):
        assert needle not in body, f"{needle} found in outbound.py — see GOTCHAS G12"


def test_openlibrary_covers_interval_is_at_least_three_seconds():
    """Open Library caps non-ID-keyed cover requests at 100/IP/5min (403)."""
    assert app.config.HOST_RATE_LIMITS["covers.openlibrary.org"] >= 3.0
