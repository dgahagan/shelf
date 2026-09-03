"""Format-independent bibliographic normalization helpers, shared by the
MARC21, flat-JSON and Dublin Core national-bibliography providers.

Stdlib only (`re`, `unicodedata`) — nothing from `app.` here, so the import
chain `national` -> `dnb` -> `bib_normalize` stays acyclic.

Every function is total over any `str`: no exceptions, `None`/empty input
maps to the type's own empty value (`""` or `None`) rather than raising, and
every string result comes back NFC-normalized.
"""

import re
import unicodedata

# A `<...>` or `(...)` qualifier group is only a *date* qualifier — and safe
# to strip unconditionally — when its content contains a run of 3-4 digits.
# An unconditional `(...)` strip would also eat a parenthetical that is part
# of the name itself (e.g. a pseudonym note); requiring digits is what keeps
# this to the form SBN's `autorePrincipale` actually carries.
_DATE_QUALIFIER = re.compile(r"\s*[<(][^<>()]*\d{3,4}[^<>()]*[>)]")

# Trailing punctuation/whitespace stripped off a publisher name after cutting
# an ISBD imprint segment at its year. "©" (U+00A9) is routine on Italian
# imprints (SBN's `pubblicazione`, e.g. "66thand2nd, ©2019") and must go too.
_PUBLISHER_TRIM = " \t\n\r[](),;:.©"

# MARC non-sorting markers. DNB brackets the article of a title and the
# particle of a name in C1 control characters — U+0098 START OF STRING and
# U+009C STRING TERMINATOR ("\x98Der\x9c Kontrabaß", "Wolfgang \x98von\x9c
# Goethe"). They survive NFC, render as boxes, and split the words on either
# side for LIKE search. The whole C1 block goes: nothing bibliographic lives
# there. C0 is left alone — \t and \n are whitespace, and strip() owns them.
_C1_CONTROLS = re.compile(r"[\x80-\x9f]")


def nfc(s: str) -> str:
    """Normalize to NFC and strip surrounding whitespace.

    Unconditional: MARC21-xml text arrives decomposed (NFD) — "Köhlmeier" is
    "o" + combining diaeresis — so stored text would otherwise diverge from
    the same name coming from a source that hands over precomposed text.
    MARC non-sorting markers (see `_C1_CONTROLS`) are dropped first, so
    "\x98Der\x9c Kontrabaß" comes back as "Der Kontrabaß". Every helper below
    routes its output through this.
    """
    if not s:
        return ""
    return unicodedata.normalize("NFC", _C1_CONTROLS.sub("", s).strip())


def invert_name(name: str) -> str:
    """"Lastname, Firstname" -> "Firstname Lastname" display order.

    Strips a date qualifier first (see `_DATE_QUALIFIER`), collapses
    whitespace, then partitions on the *first* comma. Falls back to the
    surname alone when the given-name half is empty, and to the whole
    (stripped) string when there is no comma at all — that fallback pair
    matches `dnb._invert_name`'s existing behaviour and must not change.
    """
    if not name:
        return ""
    stripped = _DATE_QUALIFIER.sub("", name)
    collapsed = re.sub(r"\s+", " ", stripped).strip()
    last, sep, first = collapsed.partition(",")
    if not sep:
        return nfc(collapsed)
    first = first.strip()
    last = last.strip()
    return nfc(f"{first} {last}" if first else last)


def strip_responsibility(title: str) -> str:
    """The text before the first ISBD ' / ' statement-of-responsibility
    delimiter; the whole title when there is none. Does not also split on
    ' : ' — that separator is part of the title proper (place : publisher
    lives in the imprint field, not here)."""
    if not title:
        return ""
    idx = title.find(" / ")
    return nfc(title if idx == -1 else title[:idx])


def split_title(s: str) -> tuple[str, str | None]:
    """Split one ISBD flat-JSON title string (e.g. SBN's `titolo`, "La quarta
    rivoluzione : sei lezioni sul futuro del libro / Gino Roncaglia") into
    (title, subtitle).

    SBN hands over title, subtitle and statement-of-responsibility all in one
    string, unlike DNB's MARC 245 `$a`/`$b`, which already arrive as separate
    subfields. Algorithm: run `strip_responsibility` first to drop everything
    from the first ' / ' (this is also where NFC comes from — see `nfc` — so
    this function builds no string outside that call), then partition what's
    left on the *first* ' : '. That is deliberately the opposite of
    `split_publication`, which cuts an imprint at its *last* ' : ' (place :
    publisher); here the first ' : ' is the one separating title from
    subtitle. Returns (title, None) when there is no ' : ', and ("", None)
    for empty/falsy input. A subtitle that is empty or whitespace-only after
    stripping comes back as None, never "".
    """
    if not s:
        return ("", None)
    stripped = strip_responsibility(s)
    title, sep, subtitle = stripped.partition(" : ")
    if not sep:
        return (title.strip(), None)
    subtitle = subtitle.strip()
    return (title.strip(), subtitle or None)


def first_year(s: str) -> int | None:
    """The first 4-digit run in `s`, or None. Deliberately no plausibility
    range — this is `dnb.py`'s existing pattern, which is what lets
    "[2018]" read as 2018."""
    if not s:
        return None
    m = re.search(r"(\d{4})", s)
    return int(m.group(1)) if m else None


def leading_int(s: str) -> int | None:
    """The integer at the very start of `s` (leading whitespace allowed), or
    None. Anchored at the start on purpose — "ca. 300 S." has no *leading*
    integer, even though it has one further in, so this returns None for it.
    This is `dnb.py`'s existing pattern, unchanged."""
    if not s:
        return None
    m = re.match(r"\s*(\d+)", s)
    return int(m.group(1)) if m else None


def split_publication(s: str) -> tuple[str | None, int | None]:
    """Split one ISBD imprint string (e.g. "Roma : Laterza, 2010, stampa
    2011") into (publisher, year).

    Algorithm: NFC the input; take the segment after the *last* ' : '
    (ISBD place-publisher separator), or the whole string when there is no
    ' : '; find the first year in that segment; cut the segment at that
    year and strip trailing "[](),;:." and whitespace to get the publisher.
    Cutting at the *first* year rather than at the last comma is what makes
    "Roma : Laterza, 2010, stampa 2011" answer ("Laterza", 2010) instead of
    treating "stampa 2011" as the publisher's tail.
    """
    if not s:
        return (None, None)
    normalized = nfc(s)
    idx = normalized.rfind(" : ")
    segment = normalized[idx + 3 :] if idx != -1 else normalized
    year_match = re.search(r"(\d{4})", segment)
    if year_match is None:
        publisher = segment.rstrip(_PUBLISHER_TRIM)
        return (publisher or None, None)
    publisher = segment[: year_match.start()].rstrip(_PUBLISHER_TRIM)
    return (publisher or None, int(year_match.group(1)))


# MARC bibliographic language code -> ISO 639-1. Where MARC has multiple
# codes for one language (e.g. "ger"/"deu"), both map to the same ISO code.
MARC_TO_ISO639_1: dict[str, str] = {
    "ger": "de",
    "deu": "de",
    "eng": "en",
    "fre": "fr",
    "fra": "fr",
    "spa": "es",
    "ita": "it",
    "dut": "nl",
    "nld": "nl",
    "por": "pt",
    "swe": "sv",
    "dan": "da",
    "jpn": "ja",
    "rus": "ru",
    "chi": "zh",
    "zho": "zh",
    "kor": "ko",
    "pol": "pl",
    "cze": "cs",
    "ces": "cs",
    "nor": "no",
}


def to_iso639_1(code: str | None) -> str | None:
    """Map a language code to ISO 639-1.

    Accepts MARC bibliographic codes ("ger"), Open Library language keys
    ("/languages/ger"), and BCP-47 tags ("de-DE", "de"). Unmappable codes
    are returned lowercased, not dropped. None/empty input returns None.
    """
    if not code:
        return None
    code = code.strip()
    if not code:
        return None
    if code.startswith("/languages/"):
        code = code[len("/languages/"):]
    code = code.lower()
    if code in MARC_TO_ISO639_1:
        return MARC_TO_ISO639_1[code]
    # BCP-47: take the primary subtag (before any "-").
    primary = code.split("-", 1)[0]
    if len(primary) == 2:
        return primary
    return code
