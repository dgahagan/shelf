"""Registry of national metadata providers keyed by ISBN-13 registration-group
prefix, plus a shared MARC-language <-> ISO 639-1 mapper.

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

from app.services import dnb

# ISBN-13 prefix (unhyphenated, longest-match wins) -> provider module.
PREFIX_PROVIDERS: dict[str, ModuleType] = {
    "9783": dnb,  # 978-3: German-language registration group
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


# MARC bibliographic language code -> ISO 639-1. Where MARC has multiple
# codes for one language (e.g. "ger"/"deu"), both map to the same ISO code.
_MARC_TO_ISO639_1: dict[str, str] = {
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

# Reverse mapping, ISO 639-1 -> MARC. Where multiple MARC codes map to one
# ISO code, this keeps the FIRST one listed above (dict insertion order is
# preserved, and later duplicate ISO keys are skipped).
_ISO_TO_MARC: dict[str, str] = {}
for _marc, _iso in _MARC_TO_ISO639_1.items():
    _ISO_TO_MARC.setdefault(_iso, _marc)


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
    if code in _MARC_TO_ISO639_1:
        return _MARC_TO_ISO639_1[code]
    # BCP-47: take the primary subtag (before any "-").
    primary = code.split("-", 1)[0]
    if len(primary) == 2:
        return primary
    return code


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
