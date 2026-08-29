"""UPC Item DB client — barcode → retail product, plus retail-title normalisation.

This is the one place the UPC Item DB endpoint lives. It used to have two
independent copies: one buried in the TMDb client (a UPC product lookup has no
business living there) and one raw, unpaced `client.get` in the scan router,
which ignored the `api.upcitemdb.com: 1.0` interval `app/config.py` declares.

Retail titles are not search queries. Four live lookups produced:

    Alice Madness Returns (PC DVD)
    Tom & Jerry: Lost Dragon / Giant Adventure [DVD]
    Super Mario: Odyssey - Nintendo Switch
    Goodfellas [DVD]  Feature Thriller Drama  Action  Suspense  Drama …

Stripping the bracketed tag is not enough — the last row keeps nine appended
retail category keywords that no bracket-or-suffix rule removes. So `clean_title`
strips, and `search_queries` is willing to *shorten*: a ladder of progressively
shorter leading fragments, tried in order until a provider answers. Both the
film path (TMDb) and the game path (IGDB) climb this same ladder.

`clean_title` and `search_queries` are pure — no I/O, so they test offline.
"""

import logging
import re

import httpx

from app.services import outbound, provider_result

logger = logging.getLogger(__name__)

UPC_LOOKUP_URL = "https://api.upcitemdb.com/prod/trial/lookup"

# Platform / format tokens that appear as a trailing " - <token>" suffix.
_PLATFORM_SUFFIXES = [
    "Nintendo Switch", "Switch",
    "PlayStation 5", "PlayStation 4", "PlayStation 3",
    "PS5", "PS4", "PS3",
    "Xbox Series X", "Xbox One", "Xbox 360",
    "Wii U", "Wii",
    "3DS", "PC",
    "Blu-ray", "DVD", "4K", "UHD",
]

# Retail noise that can appear anywhere in the title, as whole words.
_NOISE_PHRASES = [
    "Special Edition", "Collector's Edition", "Anniversary Edition",
    "Combo Pack", "Digital Copy", "Steelbook",
    "Widescreen", "Fullscreen",
    "Blu-ray", "Bluray", "DVD", "4K", "UHD",
]

_BRACKETED = re.compile(r"[\[(][^\])]*[\])]")
_TRAILING_PLATFORM = re.compile(
    r"\s+[-–]\s*(?:" + "|".join(re.escape(p) for p in _PLATFORM_SUFFIXES) + r")\s*$",
    re.IGNORECASE,
)
_NOISE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(n) for n in _NOISE_PHRASES) + r")(?!\w)",
    re.IGNORECASE,
)
_SEGMENT_BREAK = re.compile(r":|\s+-\s+|\s+–\s+|\s+/\s+")

_LEADING_ARTICLES = {"the", "a", "an"}

# Shortest a *shortened* rung may be. A one-word provider search does not fail,
# it matches a different work — "Tom" and "Super" both return a confident wrong
# answer that the scan tail files as fact. Below the floor the ladder simply
# stops, and the item is filed title-only, which is what happened before the
# ladder existed. Missing enrichment is recoverable; wrong enrichment is not.
MIN_SOLO_WORD = 7

# Trailing joiners a shortened fragment can end on ("Tom & Jerry:" from a
# three-word cut of "Tom & Jerry: Lost Dragon"). Every rung is tidied, not just
# the first — otherwise the ladder sends a query with dangling punctuation and
# a near-duplicate rung survives deduplication. Brackets are here because
# `_BRACKETED` stops at the first closer, so a nested tag such as
# "Movie (Director's Cut [Special])" leaves its outer partner behind.
_EDGE_PUNCT = " -–:/,;.&()[]"


def clean_title(raw: str) -> str:
    """Strip retail format tags, platform suffixes and edition noise from a title.

    Matching is case-insensitive; surviving words keep their original case.
    Returns "" when nothing is left (a format-only title such as "[DVD]").
    """
    if not raw:
        return ""
    text = _BRACKETED.sub(" ", raw)
    # Applied repeatedly: "Zelda - Switch - DVD" sheds one suffix per pass.
    while True:
        stripped = _TRAILING_PLATFORM.sub("", text)
        if stripped == text:
            break
        text = stripped
    text = _NOISE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(_EDGE_PUNCT)


def search_queries(raw: str) -> list[str]:
    """A ladder of progressively shorter queries for one retail title.

    Rung 1 is the cleaned title; rung 2 its leading segment before the first
    ':', ' - ', ' – ' or ' / '; rung 3 its first three words; rung 4 its first
    word (its first two when that word is an article), and only when that is
    at least `MIN_SOLO_WORD` characters. Deduplicated in order, empties dropped
    — so at most four requests per provider, and an empty or format-only title
    yields [] rather than a query nobody should send.
    """
    base = clean_title(raw)
    if not base:
        return []

    rungs = [base, _SEGMENT_BREAK.split(base)[0]]

    words = base.split()
    if len(words) > 3:
        rungs.append(" ".join(words[:3]))
    if words:
        take = 2 if words[0].lower() in _LEADING_ARTICLES and len(words) > 1 else 1
        head = " ".join(words[:take])
        # `head == base` is the whole title, not a shortening, so the floor
        # does not apply to it — it dedupes away a moment later anyway.
        if head == base or len(head) >= MIN_SOLO_WORD:
            rungs.append(head)

    seen: set[str] = set()
    ladder = []
    for rung in rungs:
        rung = rung.strip(_EDGE_PUNCT)
        if rung and rung not in seen:
            seen.add(rung)
            ladder.append(rung)
    return ladder


async def lookup(upc: str, client: httpx.AsyncClient) -> provider_result.ProviderResult:
    """Look up a barcode, returning a `ProviderResult` (`provider="upcitemdb"`).

    `found`'s payload is `{"title", "category", "brand", "images"}` — the
    whole useful part of the response, not just the title.

    `no_match` for every failure that means "no such record": a non-200, a
    malformed body, an empty `items` list. That is the contract the bare catch
    existed for — an unresolvable UPC must reach the scan page's "not found"
    manual-add form rather than an error.

    **A transport failure is not one of those.** `httpx.TimeoutException` and
    `httpx.NetworkError` are recorded as `transport_failed` rather than folded
    into `no_match`, so `_scan_upc` can render the connectivity card and log
    the scan as `error`. Offline is not "no such record" (GOTCHAS G47).

    `rate_limited` for a 429 — a rate-limited product lookup is not "unknown
    barcode" either.
    """
    try:
        resp = await outbound.fetch(
            client, "GET", UPC_LOOKUP_URL, params={"upc": upc}, timeout=10,
        )
        classified = provider_result.classify_response("upcitemdb", resp)
        if classified is not None:
            logger.debug("UPC Item DB lookup failed: HTTP %d", resp.status_code)
            return classified
        items = resp.json().get("items", [])
        if not items:
            return provider_result.no_match("upcitemdb", status=resp.status_code)
        item = items[0]
        return provider_result.found("upcitemdb", {
            "title": item.get("title"),
            "category": item.get("category"),
            "brand": item.get("brand"),
            "images": item.get("images") or [],
        }, status=resp.status_code)
    except (httpx.TimeoutException, httpx.NetworkError):
        # Offline is not "no such record". `_scan_upc` renders the
        # connectivity card and logs the scan as `error` off this outcome;
        # telling a self-hoster with broken DNS that the disc was not found
        # sends them looking for the wrong thing, and makes the scan log
        # agree with the wrong story. GOTCHAS G47.
        return provider_result.transport_failed("upcitemdb")
    except Exception:
        logger.debug("UPC Item DB lookup error", exc_info=True)
        return provider_result.no_match("upcitemdb")
