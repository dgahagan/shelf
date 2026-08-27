"""Media-type detection for a freshly scanned item.

Pure functions only — no I/O, no `httpx`, no DB, no imports from
`app.routers`. `app.config` is fine to import; it is pure data.

Four tiers, tried in order, each one only allowed to act on evidence it
actually has:

1. Barcode prefix — an ISBN (978/979 EAN-13, or ISBN-10) is a book-family
   item. A UPC/EAN that is *not* an ISBN carries no format information by
   itself (a UPC is issued to the retail product, not to "books" or
   "discs"), so it falls through to the next tier instead of deciding here.
2. Title markers — platform names (PS5, Nintendo Switch, ...) say
   video_game; retail format tags ([DVD], Blu-ray, ...) say dvd. Platform is
   checked before format so a game whose title happens to carry a format
   word in its own subtitle (a DVD-ROM PC game) still resolves as a game.
3. Category — confirmatory only, and only for video_game. See the two
   prohibitions above `_PLATFORM_MARKERS` below; nothing here may decide dvd.
4. No signal — a deliberate non-book hint (`cd`, `dvd`, `video_game`) is
   the only evidence left and stands; otherwise resolve to a concrete
   `MEDIA_TYPES` member anyway and say in the reason that it is a fallback,
   never a detection. The return is **always** a `MEDIA_TYPES` member — never
   `"auto"`, never an unchecked hint string.

G46 (see GOTCHAS.md): this module reads the *raw* scanned title, never a
shortened search-query rung. `app/services/upcitemdb.py`'s `search_queries`
ladder strips exactly the platform/format markers tier 2 matches on — by the
time a title reaches its shortest rung, "[DVD]" and "(PC DVD)" are already
gone. Callers must pass the title as scanned, not a ladder rung.
"""

import re

from app.config import MEDIA_TYPES

# Hints that mean "this is some kind of book" and are honoured as-is when the
# barcode is an ISBN. Not every MEDIA_TYPES key is book-family — dvd, cd and
# video_game are physical/digital media, not books, even though they are
# valid hints on a non-ISBN scan.
_BOOK_FAMILY_HINTS = frozenset({"book", "kids_book", "audiobook", "ebook", "comic"})

# --- Tier 2: title markers -------------------------------------------------
#
# Platform names checked first, format tags second — "Alice Madness Returns
# (PC DVD)" is a game whose own title carries the string "DVD"; checking
# format first would file it as a disc.
#
# Two prohibitions, both learned from the probe sample, both apply to the
# *category* tier (3) below, not to this title-marker tier:
#   - No category value ever decides dvd. 2 of 2 discs in the sample were
#     categorised as Electronics > Video > Televisions.
#   - No category that names a *platform* (e.g. "Electronics > Video Game
#     Consoles") ever decides video_game. A platform category describes the
#     shelf the product sits on, not what the product is — it held a Switch
#     cartridge and a PlayStation 5 console in the same sample.
#
# The second prohibition creates a title-tier problem too: "PlayStation" is
# a plausible platform marker for a game's title, but "PlayStation 5
# Console" is not a game — it is the console itself. Resolution: a small set
# of hardware words (_HARDWARE_TERMS) suppresses the platform check for that
# title. "PlayStation 5 Console" hits "console" and is excluded from the
# platform match entirely, so it falls through this tier with no verdict and
# lands on the tier-4 fallback (dvd, honestly labelled) rather than on
# video_game — Shelf has no hardware media type, so "not a game" is as far
# as detection can honestly go. "The Legend of Zelda ... - Nintendo Switch"
# has no hardware word, so its platform marker fires normally.
_PLATFORM_MARKERS = [
    "Nintendo Switch", "Wii U", "Nintendo 3DS",
    "PlayStation 5", "PlayStation 4", "PlayStation 3", "PlayStation",
    "PS5", "PS4", "PS3",
    "Xbox Series X", "Xbox One", "Xbox 360", "Xbox",
    "PC DVD",
]

_HARDWARE_TERMS = ["console", "controller", "headset"]

_FORMAT_MARKERS = [
    "[DVD]", "Blu-ray", "Bluray", "4K Ultra HD", "4K UHD", "UHD", "DVD",
]


def _contains_marker(text: str, marker: str) -> bool:
    """Case-insensitive substring match with word-ish boundaries.

    Plain `in` would let a short token like "PS4" or "UHD" fire on a
    coincidental substring inside a longer word; the lookaround here
    requires the character on either side of the match (if any) to be
    non-alphanumeric, which also does the right thing for punctuation-only
    markers like "[DVD]".
    """
    pattern = r"(?<![A-Za-z0-9])" + re.escape(marker.lower()) + r"(?![A-Za-z0-9])"
    return re.search(pattern, text.lower()) is not None


def _match_title_markers(title: str) -> tuple[str, str] | None:
    """Tier 2. Returns (media_type, reason), or None if the title says nothing."""
    is_hardware = any(_contains_marker(title, term) for term in _HARDWARE_TERMS)
    if not is_hardware:
        for marker in _PLATFORM_MARKERS:
            if _contains_marker(title, marker):
                return "video_game", (
                    f"Title names the {marker} platform — filed as Video Game."
                )
    for marker in _FORMAT_MARKERS:
        if _contains_marker(title, marker):
            return "dvd", (
                f"Title carries a '{marker}' format tag — filed as DVD / Blu-ray."
            )
    return None


def _category_decides_video_game(category: str) -> bool:
    """Tier 3. The only category string allowed to decide anything on its own.

    "Software > Video Game Software" names the software category itself, not
    a shelf a console could also sit on, so it is safe to decide alone. A
    console/platform category ("Electronics > Video Game Consoles") is
    deliberately absent from this check — see the prohibitions above the
    marker tables. Because tier 2 already returns before tier 3 ever runs,
    there is no code path left where a console category could "confirm" an
    existing video_game verdict; by the time tier 3 runs, tier 2 found
    nothing, so a console category here would be deciding alone, which is
    exactly what it must never do.
    """
    return "video game software" in category.lower()


def detect_media_type(
    barcode_type: str, hint: str, title: str | None, category: str | None,
) -> tuple[str, str]:
    """Return (media_type, reason). `reason` is what the card shows.

    `media_type` is always a member of `app.config.MEDIA_TYPES` — never
    `"auto"`, never a hint passed through unchecked. That is the point of
    this function: `insert_item` validates field *names*, not values, so an
    unvalidated `"auto"` reaching it would land in the `items` table with no
    `CHECK` to catch it.

    `hint` is whatever the scan form sent — `"auto"`, `""`, `None`, or a
    `MEDIA_TYPES` key. Any value that is not a `MEDIA_TYPES` key is treated
    as no hint at all, at every tier below, not just the fallback.

    `title` must be the raw scanned title, not a shortened search-query rung
    (see the G46 note in the module docstring).
    """
    hint = hint if hint in MEDIA_TYPES else None

    # Tier 1: barcode prefix. Only an ISBN decides anything at this tier —
    # a UPC carries no book/disc distinction of its own, so it falls through
    # to the title/category tiers instead (and a book-family hint on a UPC
    # is not evidence about the barcode; it is discarded here too).
    if barcode_type == "isbn":
        if hint in _BOOK_FAMILY_HINTS:
            return hint, (
                f"Hint '{MEDIA_TYPES[hint]}' confirmed by ISBN barcode."
            )
        if hint is not None:
            return "book", (
                f"ISBN barcodes are books — overriding the "
                f"'{MEDIA_TYPES[hint]}' hint to Book."
            )
        return "book", "ISBN barcode — filed as Book."

    # Tier 2: title markers.
    if title:
        matched = _match_title_markers(title)
        if matched is not None:
            return matched

    # Tier 3: category, confirmatory-only in practice (see docstring above).
    if category and _category_decides_video_game(category):
        return "video_game", (
            f"Category '{category}' names video game software — filed as "
            f"Video Game."
        )

    # Tier 4: no signal. Still resolve to a concrete MEDIA_TYPES member, and
    # say plainly whether this is the user's own answer or a fallback.
    #
    # A hint that reached this line survived tier 1, and on a non-ISBN barcode
    # nothing here contradicts it. The design's §1 rule is that a *book-family*
    # hint is wrong on a UPC — not that every hint is. So a deliberate "CD",
    # "DVD / Blu-ray" or "Video Game" choice stands, and only a book-family or
    # absent hint falls through to the fallback below.
    #
    # This is load-bearing for CDs in particular: Shelf has no CD detection at
    # all (no code path anywhere reads or writes one from a barcode), so the
    # dropdown is the *only* evidence a CD will ever have. Discarding it here
    # would silently refile every scanned album as a DVD — a regression, not a
    # detection, and invisible until a user noticed their music shelf had
    # turned into films.
    if hint is not None and hint not in _BOOK_FAMILY_HINTS:
        return hint, (
            f"Nothing in the barcode or the product record said otherwise — "
            f"kept your '{MEDIA_TYPES[hint]}' choice."
        )

    if barcode_type == "upc":
        return "dvd", (
            "UPC barcode carried no usable title or category signal — filed "
            "as DVD / Blu-ray. Change it on the item if that's wrong."
        )
    return "dvd", (
        "Couldn't tell from the barcode — filed as DVD / Blu-ray. Change it "
        "on the item if that's wrong."
    )
