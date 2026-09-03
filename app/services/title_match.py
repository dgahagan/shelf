"""Title-agreement guard for printed-ISBN lookups.

An OCR'd ISBN can survive its own check digit (some transpositions do), and a
checksum-valid wrong ISBN that exists in a catalogue names a *different book*.
Before intake trusts a cascade result keyed on a printed ISBN, it asks this
module whether the catalogue title agrees with the title on the row.

The **behaviour matrix in design plan section 3, step 4 is the specification**
(mirrored case-for-case in `tests/test_title_match.py`); the thresholds and
rules below are implementation constants and may change freely as long as
every matrix row still holds.

Two properties are fixed by the design and must not be relaxed:

- **Fail closed.** When the helper cannot tell, it rejects. A false reject
  costs one enrichment and lands the row exactly where today's weak path
  would; a false accept inserts the wrong book.
- **Titles only.** Subtitles are not consulted as a separate field and authors
  are never compared here — author matching lives in `app.services.authors`
  (G22).
"""

import difflib
import re
import unicodedata

# A trailing "(...)" or a ": subtitle" tail — series/edition decoration that
# one side may carry and the other may not.
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)\s*$")
_SUBTITLE = re.compile(r"^(.*?):\s.*$")

# The alternative-title tail: "The Hobbit, or There and Back Again",
# "Moby-Dick; or, The Whale". Back covers print the long form and catalogues
# store the short one, so without this a correctly-read printed ISBN is
# rejected purely because the cover omitted the colon _SUBTITLE wants.
# The tail must be **two or more words** so that ordinary titles built around
# the word "or" ("Do or Die", "Now or Never") keep their whole selves.
_ALT_TITLE = re.compile(r"^(.+?)[,;]?\s+or,?\s+(?:\S+\s+)+\S+$", re.IGNORECASE)

# Absorbs a one-character OCR slip. Well above the same-series pairs the
# matrix requires rejected (Frog and Toad ~0.70, Dune/Dune Messiah 0.50).
SIMILARITY = 0.90


def _strip_decoration(title: str) -> str:
    """Drop one trailing parenthetical, ': subtitle', or ' or ...' tail."""
    stripped = _PARENTHETICAL.sub("", title)
    if stripped != title:
        return stripped
    match = _SUBTITLE.match(title) or _ALT_TITLE.match(title)
    return match.group(1) if match else title


def _normalize(title: str) -> str:
    """Casefold, drop everything outside [a-z0-9 ], collapse whitespace.

    Punctuation vanishes, so "Philosophers" == "Philosopher's" and curly
    quotes are equivalent to straight ones; double and trailing spaces
    collapse. CJK titles normalize to the empty string, which the caller
    treats as "cannot tell" and rejects.
    """
    return " ".join(re.sub(r"[^a-z0-9 ]", "", title.casefold()).split())


def _numeric_tokens(normalized: str) -> set[str]:
    return {tok for tok in normalized.split() if tok.isdigit()}


def titles_agree(row_title: str, catalog_title: str | None) -> bool:
    """True when the catalogue title plausibly names the book on the row.

    Decoration is ignored only when **exactly one** side carries it: two
    decorated titles are compared whole, because two titles that agree only
    on a shared prefix are two books in one series and must reject.
    """
    try:
        if not isinstance(row_title, str) or not isinstance(catalog_title, str):
            return False

        row, catalog = row_title, catalog_title
        row_bare, catalog_bare = _strip_decoration(row), _strip_decoration(catalog)
        row_decorated = row_bare != row
        catalog_decorated = catalog_bare != catalog
        if row_decorated != catalog_decorated:
            row, catalog = row_bare, catalog_bare

        a, b = _normalize(row), _normalize(catalog)
        if not a or not b:
            return False
        if a == b:
            return True

        a_nums, b_nums = _numeric_tokens(a), _numeric_tokens(b)
        if a_nums and b_nums and a_nums != b_nums:
            return False

        return difflib.SequenceMatcher(None, a, b).ratio() >= SIMILARITY
    except Exception:  # pragma: no cover - fail closed on anything unexpected
        return False


# Roman numerals up to xii, mapped to their arabic form so "Modern Warfare II"
# and "Modern Warfare 2" normalize identically. Whole-token only.
_ROMAN = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6",
    "vii": "7", "viii": "8", "ix": "9", "x": "10", "xi": "11", "xii": "12",
}


def _fold(s: str) -> str:
    """Drop combining diacritics (NFKD decompose, then strip combiners)."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def titles_match_exactly(row_title: str, catalog_title: str | None) -> bool:
    """True when the catalogue title is the row title, exactly, after folding.

    This is the **retail** guard for the TMDb/IGDB lookups intake runs on
    rows typed as DVD or Video Game — it is variant E' from the design's
    probe, measured against 31 retail pairs. It deliberately does **not**
    call `_strip_decoration`: every relaxation that keeps that strip also
    keeps the two false accepts the guard exists to prevent — 'Dune' reads
    as a match for *Dune: Part Two* once decoration is stripped (raw ratio
    0.47), and 'ALIEN' matches *Aliens* at 0.91, over threshold. Both must
    stay rejected, so this compares titles whole: casefolded, punctuation
    and diacritics dropped, and roman numerals mapped to arabic, but with
    no notion of a "bare" title underneath the decorated one.

    `titles_agree` above is the **book** guard and is deliberately left
    untouched — it still strips decoration, because a printed-ISBN lookup's
    failure mode (a false reject) is cheaper than intake's (a wrong film
    accepted into a bulk-confirmed batch with no per-row card to catch it).
    """
    try:
        if not isinstance(row_title, str) or not isinstance(catalog_title, str):
            return False

        def norm(title: str) -> str:
            normalized = _normalize(_fold(title))
            return " ".join(_ROMAN.get(tok, tok) for tok in normalized.split())

        a, b = norm(row_title), norm(catalog_title)
        return bool(a) and bool(b) and a == b
    except Exception:  # pragma: no cover - fail closed on anything unexpected
        return False
