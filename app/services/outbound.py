"""Shared politeness layer for outbound metadata and cover requests.

Two pieces, both used by the metadata/cover clients in this package:

- `acquire(host)` — a per-host rate limiter. Holds a lock *across* the sleep
  so two concurrent callers for the same host serialise rather than both
  reading a stale timestamp and firing together.
- `fetch(client, method, url, ...)` — `acquire()` plus bounded retries for
  transient failures, with jittered backoff and `Retry-After` support.

**This module throttles; it does not validate.** It must never grow
RFC1918/loopback/DNS checks — Shelf is self-hosted and LAN endpoints are the
normal case (GOTCHAS G12). Cover-download URL validation lives in
`covers._download`, which checks the *final* URL after redirects (G11).

LAN-facing clients (`audiobookshelf.py`, `notify.py`, `vision.py`) are
deliberately not routed through here: throttling a user's own server is
wrong, and their endpoints are user-supplied rather than public APIs.
"""

import asyncio
import logging
import random
import time

import httpx

import app.config

logger = logging.getLogger(__name__)

# Statuses worth another attempt. 403 is deliberately absent: Open Library
# returns it when the covers rate limit is exceeded, and retrying makes that
# worse rather than better (app/config.py HOST_RATE_LIMITS).
RETRY_STATUSES = frozenset({429, 502, 503, 504})

BACKOFF_BASE = 0.5  # seconds; attempt n waits BACKOFF_BASE * 2**n
BACKOFF_MAX = 8.0  # cap on our own computed backoff
# The longest server-stated wait we will serve; a Retry-After beyond it ends
# the attempt (the server is reporting a spent quota, not a blip).
RETRY_AFTER_MAX = 30.0
JITTER = 0.25  # +/- fraction applied to computed backoff, not to Retry-After

# host -> (lock, last-request monotonic timestamp). Created lazily: an
# asyncio.Lock binds to the running loop the first time it is awaited on, and
# the test suite runs a fresh loop per test, so a registry built at import
# time (or carried across tests) would bind locks to a dead loop.
# tests/conftest.py resets this between tests (GOTCHAS G13).
_hosts: dict[str, list] = {}

# Indirection so tests can patch the waiting out. Never call asyncio.sleep
# directly in this module.
_sleep = asyncio.sleep


def reset() -> None:
    """Drop the per-host limiter registry. Called by the test fixture."""
    _hosts.clear()


def _interval(host: str) -> float:
    """Minimum seconds between requests to `host`; 0.0 when unpaced.

    Resolved at call time via the module, not a from-import, so tests can
    override single entries (CLAUDE.md "Config import trap").
    """
    return app.config.HOST_RATE_LIMITS.get(host, 0.0)


async def acquire(host: str) -> None:
    """Wait until it is polite to make a request to `host`.

    Unpaced hosts return immediately without taking a lock. For paced hosts
    the lock is held across the sleep, so concurrent callers queue behind
    each other instead of racing on a stale timestamp.
    """
    interval = _interval(host)
    if interval <= 0:
        return

    entry = _hosts.get(host)
    if entry is None:
        entry = [asyncio.Lock(), 0.0]
        _hosts[host] = entry

    async with entry[0]:
        wait = interval - (time.monotonic() - entry[1])
        if wait > 0:
            await _sleep(wait)
        entry[1] = time.monotonic()


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """Numeric `Retry-After` in seconds, uncapped; None if absent, negative, NaN or a date.

    The caller decides whether the number is worth waiting for.
    """
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None  # HTTP-date form — fall back to our own backoff
    # NaN passes float(), fails every comparison, and so slips both the `< 0`
    # guard and the caller's ceiling test — landing in `_sleep(nan)`, which
    # raises under uvloop. `seconds != seconds` is the NaN test.
    if seconds < 0 or seconds != seconds:
        return None
    return seconds


def _backoff(attempt: int) -> float:
    delay = min(BACKOFF_BASE * (2**attempt), BACKOFF_MAX)
    return delay * (1 + random.uniform(-JITTER, JITTER))


async def fetch(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    retries: int = 2,
    retry_timeouts: bool = False,
    **kwargs,
) -> httpx.Response:
    """Rate-limited request with bounded retries on transient failures.

    Transient = `httpx.ConnectError`, a response in `RETRY_STATUSES`, and —
    only when `retry_timeouts=True` — `httpx.TimeoutException`.

    Timeouts are opt-in because a read timeout costs the caller's whole
    `timeout=` budget per attempt, and several callers sit on the
    `POST /api/scan` request path where that budget is HTTP_TIMEOUT (15s).
    Retrying timeouts there would triple the worst-case scan latency that
    issue #27 is about. `covers._download` — the queue worker's path, off
    the request path — opts in; request-path lookups do not. `ConnectError`
    fails fast and is retried everywhere.

    A response in `RETRY_STATUSES` carrying a numeric `Retry-After` within
    `RETRY_AFTER_MAX` sleeps exactly that long; one **beyond** the ceiling is
    not transient at all — the server is reporting a spent quota, so the
    response is returned at once (logged at WARNING) rather than retried.

    On the final transient failure the original exception is re-raised
    unchanged, or the last transient response returned — callers already
    branch on `except httpx.TimeoutException` / `status_code != 200`, and
    wrapping would break those.

    `retries` and `retry_timeouts` are consumed here; every other keyword is
    passed through to `client.request` untouched.
    """
    host = httpx.URL(url).host
    transient_excs: tuple[type[Exception], ...] = (httpx.ConnectError,)
    if retry_timeouts:
        transient_excs += (httpx.TimeoutException,)

    for attempt in range(retries + 1):
        last = attempt == retries
        await acquire(host)
        try:
            resp = await client.request(method, url, **kwargs)
        except transient_excs as exc:
            if last:
                raise
            delay = _backoff(attempt)
            logger.debug(
                "outbound: %s %s failed (%s), retrying in %.2fs",
                method,
                url,
                type(exc).__name__,
                delay,
            )
            await _sleep(delay)
            continue

        if resp.status_code in RETRY_STATUSES:
            retry_after = _retry_after_seconds(resp)
            if retry_after is not None and retry_after > RETRY_AFTER_MAX:
                # The server has said how long the wait is, and it is longer
                # than we are willing to serve. That is a statement that a
                # retry is pointless (a spent daily quota), not a transient —
                # return the response now instead of sleeping the cap and
                # asking again. Same reasoning as 403's absence above.
                logger.warning(
                    "outbound: %s returned HTTP %d asking for a %.0fs wait, "
                    "beyond the %.0fs ceiling — not retrying",
                    host,
                    resp.status_code,
                    retry_after,
                    RETRY_AFTER_MAX,
                )
                return resp
            if not last:
                delay = retry_after if retry_after is not None else _backoff(attempt)
                logger.debug(
                    "outbound: %s %s returned %d, retrying in %.2fs",
                    method,
                    url,
                    resp.status_code,
                    delay,
                )
                await _sleep(delay)
                continue

        return resp

    raise AssertionError("unreachable")  # pragma: no cover


# Statuses that mean "we asked too often", as distinct from RETRY_STATUSES,
# which means "another attempt is worth making". The two questions are not the
# same question and the sets are deliberately different: 502/504 are gateway
# failures, not quotas, and a card saying "rate-limited — try again shortly"
# for a provider outage tells the user to do the wrong thing. 503 is out for
# the same reason. 403 is out because Open Library returns it both for a spent
# covers quota and for a plain authorization failure (see the comment above
# RETRY_STATUSES) — revisit trigger is in the design plan's `### 1`.
RATE_LIMIT_STATUSES = frozenset({429})


def is_rate_limited(resp: httpx.Response) -> bool:
    """True when the provider refused this request for rate/quota reasons.

    Keyed on the **status**, never on `Retry-After`. Measured: Google Books
    429s this workstation with no `Retry-After` and no `X-RateLimit-*` header,
    3/3 (the design plan's Probe 1), so a header-keyed predicate would answer
    False for the one provider actually rate-limiting us.

    Deliberately *not* the same test `fetch` applies at RETRY_AFTER_MAX. That
    one asks "is another attempt worth making?" and for a headerless 429 the
    answer is still yes. This one asks "should the user be told to come back?"
    """
    return resp.status_code in RATE_LIMIT_STATUSES
