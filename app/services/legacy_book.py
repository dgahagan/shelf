"""Conservative support for legacy price-point UPC-A + 5 book barcodes.

Before Bookland EAN became the normal book barcode, some Scholastic books
carried a shared price-point UPC-A and a five-digit title supplement.  The
UPC identifies a publisher/price family; the supplement supplies the title
number.  Treating that UPC as an ordinary product barcode can therefore file
the wrong book.

This module recognizes only the evidence-backed Scholastic form and produces
the small set of checksum-valid ISBN candidates implied by its known ISBN
prefixes.  The caller verifies those candidates through Shelf's normal
metadata cascade and must not choose between multiple verified matches by
guesswork.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.services import isbn as isbn_svc
from app.services import upc as upc_svc


@dataclass(frozen=True, slots=True)
class LegacyBookBarcode:
    """The parsed identity of a supported legacy barcode."""

    upc: str
    supplement: str
    isbn10_prefixes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LegacyBookMatch:
    """One candidate positively verified by the normal metadata cascade."""

    isbn13: str
    metadata: dict[str, Any]
    source: str
    hc_ids: dict[str, Any]
    cascade: Any


ResolutionOutcome = Literal[
    "not_legacy",
    "found",
    "not_found",
    "ambiguous",
    "inconclusive",
]


@dataclass(frozen=True, slots=True)
class LegacyBookResolution:
    outcome: ResolutionOutcome
    candidates: tuple[str, ...] = ()
    matches: tuple[LegacyBookMatch, ...] = ()
    isbn13: str | None = None
    metadata: dict[str, Any] | None = None
    source: str = "manual"
    hc_ids: dict[str, Any] | None = None
    cascade: Any = None


# This is intentionally a small, evidence-backed map.  A new publisher must
# be added only after confirming how its supplement maps to ISBN digits;
# inferring a global prefix rule would recreate the wrong-book failure this
# module is designed to prevent.
_PUBLISHER_PREFIXES: dict[str, tuple[str, ...]] = {
    "078073": ("0590", "0439"),
}


def parse(raw: str) -> LegacyBookBarcode | None:
    """Parse a supported UPC-A + 5 barcode, or return ``None``.

    A few scanners emit the UPC-A as its exact leading-zero EAN-13 form.  A
    non-zero EAN-13 prefix is not equivalent and is deliberately rejected.
    ``normalize_barcode`` provides the same separator-tolerant behavior as
    the rest of Shelf's scan paths.
    """

    digits = upc_svc.normalize_barcode(raw)
    if len(digits) == 18 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 17:
        return None

    upc = digits[:12]
    supplement = digits[12:]
    if not upc_svc.validate_upc(upc):
        return None

    prefixes = _PUBLISHER_PREFIXES.get(upc[:6])
    if not prefixes:
        return None
    return LegacyBookBarcode(upc, supplement, prefixes)


def mapping_key(raw: str) -> str | None:
    """Return the canonical 17-digit identity shared by supported forms."""

    barcode = parse(raw)
    return None if barcode is None else barcode.upc + barcode.supplement


def _isbn10_check_digit(body9: str) -> str:
    """Return the ISBN-10 check character for nine numeric body digits."""

    total = sum(int(digit) * (10 - index) for index, digit in enumerate(body9))
    check = (11 - (total % 11)) % 11
    return "X" if check == 10 else str(check)


def isbn13_candidates(raw: str) -> tuple[str, ...]:
    """Generate only checksum-valid ISBN-13 candidates implied by ``raw``."""

    barcode = parse(raw)
    if barcode is None:
        return ()

    candidates: list[str] = []
    for prefix in barcode.isbn10_prefixes:
        body9 = prefix + barcode.supplement
        if len(body9) != 9 or not body9.isdigit():
            continue
        isbn10 = body9 + _isbn10_check_digit(body9)
        if not isbn_svc.validate_isbn10(isbn10):
            continue
        isbn13 = isbn_svc.isbn10_to_isbn13(isbn10)
        if isbn13 and isbn13 not in candidates:
            candidates.append(isbn13)
    return tuple(candidates)


LookupResult = tuple[dict[str, Any] | None, str, dict[str, Any], Any]
Lookup = Callable[[str], Awaitable[LookupResult]]


async def resolve(raw: str, lookup: Lookup) -> LegacyBookResolution:
    """Verify every candidate without converting provider uncertainty to a miss.

    Exactly one positive result is enough for automatic resolution only when
    every other candidate was checked and reported an ordinary miss.  A
    transport, rate-limit, or credential failure on any unchecked candidate
    makes the result inconclusive.  Multiple positive results remain
    explicitly ambiguous so the user can choose the physical book.
    """

    candidates = isbn13_candidates(raw)
    if not candidates:
        return LegacyBookResolution("not_legacy")

    matches: list[LegacyBookMatch] = []
    inconclusive = False
    for candidate in candidates:
        metadata, source, hc_ids, cascade = await lookup(candidate)
        if metadata:
            matches.append(
                LegacyBookMatch(
                    isbn13=candidate,
                    metadata=metadata,
                    source=source,
                    hc_ids=hc_ids,
                    cascade=cascade,
                )
            )
            continue

        outcome = getattr(cascade, "outcome", "no_match")
        if outcome not in {"no_match", "no_credential"}:
            inconclusive = True

    verified = tuple(matches)
    if inconclusive:
        return LegacyBookResolution(
            "inconclusive", candidates=candidates, matches=verified
        )
    if len(matches) > 1:
        return LegacyBookResolution(
            "ambiguous", candidates=candidates, matches=verified
        )
    if not matches:
        return LegacyBookResolution("not_found", candidates=candidates)

    match = matches[0]
    return LegacyBookResolution(
        "found",
        candidates=candidates,
        matches=verified,
        isbn13=match.isbn13,
        metadata=match.metadata,
        source=match.source,
        hc_ids=match.hc_ids,
        cascade=match.cascade,
    )
