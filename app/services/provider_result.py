"""What a metadata provider answered, as one returned value.

Every client in this package used to return a bare `dict | None` (or a bare
list) and report the *quality* of that answer somewhere else — as a raised
`TmdbAuthError`, as an `on_rate_limit()` callback, or as prose in a log line.
A caller that did not install all three channels silently downgraded a
rejected credential, a spent quota or a dead socket to "no match", which is
the same defect issues #20, #36, #42, #44, #45, #46, #47 and #50 were each
patched for one call site at a time.

A `ProviderResult` carries the answer and its quality together, so a new
caller gets the whole outcome by construction rather than by remembering to
wire three handlers.

**No truthiness.** `__bool__` raises. Every pre-existing caller was written as
`if metadata:` / `if result:`, and a dataclass is always truthy — so a silent
`no_match` would have read as a hit and filed garbage. Test `.found`.

`provider` is a display-free identifier (`"tmdb"`, `"openlibrary"`), never a
sentence: `fragments/scan_result.html` names providers itself, and a string
assembled here would be one interpolation away from the card (GOTCHAS G58).
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

import httpx

from app.services import outbound

Outcome = Literal[
    "found",
    "no_match",
    "no_credential",
    "rejected",
    "rate_limited",
    "transport_failed",
]

# The same names as `Outcome`, in the same order, as a runtime value. This
# tuple is the contract `scan_outcome.enrich_status` projects from and the
# tests pin against; keep it and the `Literal` in step.
OUTCOMES: tuple[str, ...] = (
    "found",
    "no_match",
    "no_credential",
    "rejected",
    "rate_limited",
    "transport_failed",
)

# Which outcome wins when a cascade produced several, most actionable first.
# `found` is handled ahead of this ladder (see `combine`), and
# `no_credential` sits last because a leg that was never asked should not
# speak over one that was asked and refused.
_PRECEDENCE: tuple[str, ...] = (
    "rejected",
    "rate_limited",
    "transport_failed",
    "no_match",
    "no_credential",
)


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """One provider's answer: what happened, who said so, and the payload."""

    outcome: str
    provider: str
    payload: Any = None
    status: int | None = None

    @property
    def found(self) -> bool:
        """True only for a real hit. This is the test callers must use."""
        return self.outcome == "found"

    def with_payload(self, payload: Any) -> "ProviderResult":
        """The same outcome, provider and status, carrying a new payload."""
        return replace(self, payload=payload)

    def __bool__(self) -> bool:
        raise TypeError("ProviderResult is not a boolean — test .found")


def found(provider: str, payload: Any, *, status: int | None = 200) -> ProviderResult:
    return ProviderResult("found", provider, payload, status)


def no_match(provider: str, *, status: int | None = None) -> ProviderResult:
    return ProviderResult("no_match", provider, None, status)


def no_credential(provider: str) -> ProviderResult:
    return ProviderResult("no_credential", provider, None, None)


def rejected(provider: str, *, status: int | None) -> ProviderResult:
    return ProviderResult("rejected", provider, None, status)


def rate_limited(provider: str, *, status: int | None = 429) -> ProviderResult:
    return ProviderResult("rate_limited", provider, None, status)


def transport_failed(provider: str) -> ProviderResult:
    return ProviderResult("transport_failed", provider, None, None)


def classify_response(
    provider: str,
    resp: httpx.Response,
    *,
    auth_statuses: tuple[int, ...] = (),
) -> ProviderResult | None:
    """Classify a response the client did not have to parse, or `None`.

    `None` means "200 — go read the body", because only the client knows
    whether its own payload counts as a hit or an empty answer.

    Auth outranks rate-limiting, matching the order `tmdb.py` applied when it
    still raised: IGDB answers a bad client id with 400 *and* Google Books
    answers a spent quota with 429, so a credential the user can fix is the
    more useful thing to say when a status could be read as both.

    The rate-limit test is `outbound.is_rate_limited`, never
    `outbound.RETRY_STATUSES` — those two ask different questions ("should the
    user be told to come back?" vs. "is another attempt worth making?") and
    conflating them would file a 503 as a spent quota (GOTCHAS G60).
    """
    if resp.status_code in auth_statuses:
        return rejected(provider, status=resp.status_code)
    if outbound.is_rate_limited(resp):
        return rate_limited(provider, status=resp.status_code)
    if resp.status_code != 200:
        return no_match(provider, status=resp.status_code)
    return None


def combine(results: Sequence[ProviderResult], *, provider: str) -> ProviderResult:
    """Fold one cascade's legs into the outcome the caller should report.

    Every winner is returned **as it stands**, keeping the leg's own provider
    and status — a hit so `source` names who answered, and a failure so the
    scan card can say *which* credential was rejected rather than leaving a
    hole where the provider name goes. `provider` names only the empty
    cascade, which has no leg to speak for it.

    A hit wins outright. Otherwise the most actionable failure wins, and
    `no_credential` is reported only when every leg was skipped for want of
    one — a single skipped leg beside a real miss is silent, because the user
    gains nothing from being told about a provider that was never going to be
    asked.
    """
    for result in results:
        if result.found:
            return result
    for candidate in _PRECEDENCE:
        for result in results:
            if result.outcome == candidate:
                return result
    return no_match(provider)
