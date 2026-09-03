"""Registry of national metadata providers keyed by ISBN-13 registration-group
prefix, plus a shared MARC-language <-> ISO 639-1 mapper — a re-export of
`bib_normalize.to_iso639_1`/`MARC_TO_ISO639_1` plus the reverse direction
(`iso_to_marc`) that lives here.

Registry key format: unhyphenated digit prefixes of the ISBN-13, matching as
many leading digits of the registration group as the provider needs. For
example the German-language group "978-3" is keyed as "9783" (ISBNs are
always stored unhyphenated in this codebase, so the registry follows suit).
A future provider for a narrower 5-digit group (e.g. "978-3-86" for a
specific German sub-publisher range) can coexist with the 4-digit "9783" key
because `provider_for` resolves the *longest* matching prefix, not the first.
"""

from __future__ import annotations

from types import ModuleType

# to_iso639_1 lives in bib_normalize (format-independent, shared with the
# flat-JSON and Dublin Core providers). Re-exported here because the registry
# is what callers already reach for: intake.py, openlibrary.py, googlebooks.py.
from app.services.bib_normalize import MARC_TO_ISO639_1, to_iso639_1  # noqa: F401

from app.services import dnb, sbn

# ISBN-13 prefix (unhyphenated, longest-match wins) -> provider module.
#
# 97888/97912 are 5-digit keys, not 4 — app/database.py:118-127 (migration 23,
# "Backfill language from ISBN registration group") already keys these same
# two prefixes to 'it' at 5 digits, beside 5-digit 97884/97885/97887 and
# 4-digit 9783/9782. A 4-digit "9788" key here would also swallow 978-84
# (Spain), 978-85 (Brazil) and 978-80 (Czech/Slovak), routing their ISBNs to
# an Italian catalogue that answers numFound: 0 for all three.
PREFIX_PROVIDERS: dict[str, ModuleType] = {
    "9783": dnb,  # 978-3: German-language registration group
    "97888": sbn,  # 978-88: Italian registration group
    "97912": sbn,  # 979-12: Italian registration group (no ISBN-10 equivalent)
}


def provider_for(isbn13: str) -> ModuleType | None:
    """Return the national provider module for `isbn13`, using longest-prefix
    match over PREFIX_PROVIDERS. Returns None when nothing matches."""
    if not isbn13:
        return None
    best_key = None
    for key in PREFIX_PROVIDERS:
        if isbn13.startswith(key):
            if best_key is None or len(key) > len(best_key):
                best_key = key
    return PREFIX_PROVIDERS[best_key] if best_key is not None else None


# Reverse mapping, ISO 639-1 -> MARC. Where multiple MARC codes map to one
# ISO code, this keeps the FIRST one listed in MARC_TO_ISO639_1 (dict
# insertion order is preserved, and later duplicate ISO keys are skipped),
# so "de" reverses to "ger", not "deu".
_ISO_TO_MARC: dict[str, str] = {}
for _marc, _iso in MARC_TO_ISO639_1.items():
    _ISO_TO_MARC.setdefault(_iso, _marc)


def iso_to_marc(code: str | None) -> str | None:
    """Reverse of the MARC -> ISO 639-1 mapping above, returning the first
    MARC form listed for a given ISO 639-1 code. None/unmappable -> None."""
    if not code:
        return None
    return _ISO_TO_MARC.get(code.strip().lower())


# Ordered code -> English display name, for the settings search-language
# dropdown. Order is deliberate (display order in the UI).
SEARCH_LANGS: dict[str, str] = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "nl": "Dutch",
    "pt": "Portuguese",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "pl": "Polish",
    "cs": "Czech",
    "ja": "Japanese",
    "ru": "Russian",
    "zh": "Chinese",
    "ko": "Korean",
}
