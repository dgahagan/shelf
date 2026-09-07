"""Media-type detection for a freshly scanned item.

Pure functions only — no I/O, no `httpx`, no DB, no imports from
`app.routers`. `app.config` is fine to import; it is pure data.

Four tiers, tried in order, each one only allowed to act on evidence it
actually has:

1. Barcode prefix — an ISBN (978/979 EAN-13, or ISBN-10) is a book-family
   item. A UPC/EAN that is *not* an ISBN carries no format information by
   itself (a UPC is issued to the retail product, not to "books" or
   "discs"), so it falls through to the next tier instead of deciding here.
2. Title markers, in four arms: platform names (PS5, Nintendo Switch,
   PC CD, ...) say video_game; retail format tags ([DVD], Blu-ray, ...) say
   dvd; software-medium tags (CD-ROM) say video_game; audio tags (Audio CD,
   Compact Disc, CD) say cd. All four run only when `_is_hardware_title` is
   false: this tier **declines to decide a hardware title at all**, because a
   format, medium or audio word on a hardware listing is a shelf-listing
   artifact rather than evidence about the object (`G68`). The order — platform, format, medium, audio —
   is load-bearing at every seam and measured at each. Platform before
   format so a game whose title happens to carry a format word in its own
   subtitle (a DVD-ROM PC game) still resolves as a game. Format before
   medium *and* audio because every film title in the probe corpus carrying
   "CD" is a disc bundle that also carries a format tag ("Purple Rain
   [DVD/CD Combo]"), so format-first files all three as discs where either
   later arm would file a concert Blu-ray as a game or a CD. Medium before
   audio because the bare "CD" audio marker matches *inside* "CD-ROM".
3. Category — confirmatory only, and only for video_game and cd. See the two
   prohibitions above `_PLATFORM_MARKERS` below; nothing here may decide dvd,
   and no category *naming a platform* may decide video_game. Both admitted
   categories name the medium itself.
4. No signal, in three parts. **First**, recognised hardware: a title
   carrying a hardware word *and* a platform marker or a hardware brand
   (`_is_hardware_title`) is a console, controller or headset. Because tier 2 declines such a title
   outright, this arm is the only thing that decides one — unless tier 3's
   medium-naming category fires, which still outranks it. That is a weaker answer than a
   detection and a stronger one than nothing — it says what the item is
   *not*, which is enough for a caller to decline a film search it would
   otherwise lose (`signal="hardware"`). It sits above the hint branch
   deliberately: a dropdown choice asserts what the item *is*, not that a
   film search on a title containing "Console" will match. Then a deliberate
   non-book hint (`cd`, `dvd`, `video_game`) stands (`signal="hinted"`);
   otherwise resolve to a concrete `MEDIA_TYPES` member anyway and say in
   the reason that it is a fallback, never a detection (`signal="none"`).
   The `media_type` is **always** a `MEDIA_TYPES` member — never `"auto"`,
   never an unchecked hint string.

The return is a `Detection`, not a bare tuple: the tier that decided is worth
more to a caller than the verdict alone. `_scan_upc` reads `signal` to skip
the TMDb ladder for hardware, because that ladder's shortest rung answers
"PlayStation" with a confident match for a different film (`G46` — missing
enrichment is recoverable, wrong enrichment is not).

G46 (see GOTCHAS.md): this module reads the *raw* scanned title, never a
shortened search-query rung. `app/services/upcitemdb.py`'s `search_queries`
ladder strips exactly the platform/format markers tier 2 matches on — by the
time a title reaches its shortest rung, "[DVD]" and "(PC DVD)" are already
gone. Callers must pass the title as scanned, not a ladder rung.
"""

import re
from dataclasses import dataclass
from typing import Literal

from app.config import MEDIA_TYPES

# How detection reached its verdict. Callers branch on what the answer is
# *worth*, never on which tier produced it — a tier number is a fact about
# this module's internal order and would invite `tier >= 4` comparisons that
# silently acquire meaning when a tier is inserted.
Signal = Literal["detected", "hinted", "hardware", "none"]

# The same names as `Signal`, in the same order, as a runtime value. Keep it
# and the `Literal` in step — a test pins that they agree.
SIGNALS: tuple[str, ...] = ("detected", "hinted", "hardware", "none")


@dataclass(frozen=True, slots=True)
class Detection:
    """What detection decided, and how much that verdict is worth.

    Deliberately **no** `__bool__`, unlike `ProviderResult`. That class raises
    because every pre-existing caller was written `if result:` and a dataclass
    is always truthy. Every caller here *unpacks a tuple*, which a dataclass
    refuses loudly on its own — a raising `__bool__` would defend nothing.
    """

    media_type: str   # always a MEDIA_TYPES member, as today
    reason: str       # the card's prose, as today
    signal: str       # a SIGNALS member


# Hints that mean "this is some kind of book" and are honoured as-is when the
# barcode is an ISBN. Not every MEDIA_TYPES key is book-family — dvd, cd and
# video_game are physical/digital media, not books, even though they are
# valid hints on a non-ISBN scan.
_BOOK_FAMILY_HINTS = frozenset({"book", "kids_book", "audiobook", "ebook", "comic"})

# --- Tier 2: title markers -------------------------------------------------
#
# Platform names checked first, format tags second, audio tags third — "Alice
# Madness Returns (PC DVD)" is a game whose own title carries the string
# "DVD"; checking format first would file it as a disc. "Purple Rain [DVD/CD
# Combo]" is a disc bundle whose own title carries the string "CD"; checking
# audio before format would file it as an album. Each arm is checked after
# the one that can be wrong about it.
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
# has no hardware word, so its platform marker fires normally. The same
# predicate also reads `_HARDWARE_BRANDS` below, so a headset that names its
# maker rather than its platform is recognised the same way.
_PLATFORM_MARKERS = [
    "Nintendo Switch", "Wii U", "Nintendo 3DS",
    "PlayStation 5", "PlayStation 4", "PlayStation 3", "PlayStation",
    "PS5", "PS4", "PS3",
    "Xbox Series X", "Xbox One", "Xbox 360", "Xbox",
    "PC DVD", "PC CD",
]

_HARDWARE_TERMS = ["console", "controller", "headset"]

# Peripheral makers. A *brand* table, deliberately not named `*_MARKERS`: the
# suite sweeps every `*_MARKERS` attribute as a tier-2 deciding table, and a
# brand decides nothing on its own — it is only ever the second half of the
# conjunction in `_is_hardware_title`. `_contains_marker` is case-insensitive,
# bounds the match on non-alphanumerics and `re.escape`s the token, so the
# space in "Turtle Beach" and the leading digit in "8BitDo" need no special
# case. `Sony` is the one film-industry name here; see the predicate's
# docstring for why it stays.
_HARDWARE_BRANDS = [
    "Logitech", "SteelSeries", "Corsair", "Thrustmaster", "Turtle Beach",
    "HyperX", "Razer", "Astro", "Nacon", "PowerA", "Scuf", "8BitDo", "Sony",
]


def _is_hardware_title(title: str) -> bool:
    """A hardware word *conjoined with* a platform marker or a hardware brand.

    A hardware word alone is not enough — `Console Wars`, `Air Traffic
    Controller` and `The Controller 2019` are films. The conjunction is what
    lets this predicate gate *all* of tier 2 without catching those three: a
    bare hardware-word test would suppress `Console Wars [DVD]`'s own format
    tag and file a film with no detection at all. Narrow enough to act on at
    tier 4, and narrow enough to decline every tier-2 arm on.

    The second half is a platform name *or* a listed brand, because a
    peripheral usually names its maker and not its platform: `Sony PULSE 3D
    Wireless Headset`, `Logitech G Pro X Gaming Headset`. A brand alone is
    not enough either — `Astro Boy`, `Turtle Beach` and `The Corsair` are
    films — so the conjunction still gates. `Sony` is the only film-industry
    name in the table and the conjunction is what defends it: `Sony Pictures
    Classics Presents Whiplash` carries no hardware word and is False.

    Recall is bounded by the brand table. A hardware listing whose brand is
    not listed still falls through as before — filed as `dvd` with the honest
    tier-4 fallback reason, and searched — which is no worse than it was.
    Revisit trigger: a reported wrong film or game match on a hardware title
    whose brand is not in the table.

    Reading `_PLATFORM_MARKERS` means every token added there widens this
    predicate too — a new platform token is never a purely tier-2 decision.
    `CD-ROM` is deliberately *not* in that table (it has its own arm below),
    so `CD-ROM drive` plus a hardware word is not recognised here; that is
    an accepted false negative of the same kind as an unlisted brand, and
    the price of keeping a medium out of the platform arm.
    """
    if not any(_contains_marker(title, term) for term in _HARDWARE_TERMS):
        return False
    return (
        any(_contains_marker(title, marker) for marker in _PLATFORM_MARKERS)
        or any(_contains_marker(title, brand) for brand in _HARDWARE_BRANDS)
    )

_FORMAT_MARKERS = [
    "[DVD]", "Blu-ray", "Bluray", "4K Ultra HD", "4K UHD", "UHD", "DVD",
]

# Software-medium tags, checked *after* format tags and *before* audio ones.
#
# "CD-ROM" names a medium, not a platform, and the distinction is the whole
# reason this arm exists rather than a fifth entry in `_PLATFORM_MARKERS`. A
# platform token is allowed to beat a format tag — "Alice Madness Returns (PC
# DVD)" is a game whose title carries "DVD". A medium token is not: a film
# bundle can carry one alongside its own format tag ("Terminator 2 [DVD]
# (includes bonus CD-ROM)"), and with "CD-ROM" in the platform table that row
# filed as a *video game*, with a confident reason, ahead of its own "[DVD]".
#
# So it sits below format for the same measured reason `_AUDIO_MARKERS` does
# — a bundle names its own format — and above audio because `_contains_marker`
# treats the hyphen as a word boundary, so the bare "CD" audio marker matches
# *inside* "CD-ROM". Without this arm a bare `Myst CD-ROM` would reach the
# audio loop and file a PC game as a music album.
_MEDIUM_MARKERS = ["CD-ROM"]

# Audio tags, checked *after* format and medium tags. Measured (design plan, probe 3):
# every film title carrying "CD" is a disc bundle that also carries a format
# marker — "Purple Rain [DVD/CD Combo]", "The Bodyguard Blu-ray + Soundtrack
# CD" — so format-first files all three as discs, and audio-first would file a
# concert Blu-ray as a CD. The same argument that puts platform before format,
# one rung down. Most specific first, so the reason names the fuller tag when
# the title has it.
#
# "CD" also matches *inside* "CD-ROM" — `_contains_marker` treats the hyphen as
# a word boundary — which is why `_MEDIUM_MARKERS` above runs first.
_AUDIO_MARKERS = ["Audio CD", "Compact Disc", "CD"]


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


def _match_title_markers(title: str) -> Detection | None:
    """Tier 2. A `Detection`, or None if the title says nothing.

    Typed as the record rather than a tuple because `detect_media_type`
    returns this value through unchanged — a helper still handing back a bare
    pair would slip a tuple out through a signature promising a `Detection`,
    and nothing on the call path would notice.
    """
    # G68. The predicate answers "is this input the kind of thing a
    # title-marker lookup is for?", and the answer is no for every arm below,
    # not just the platform loop it used to wrap. A format, medium or audio
    # word on a hardware listing is a shelf-listing artifact, not evidence
    # that the object is media — "PlayStation 5 Wireless Headset CD-ROM" is a
    # headset, and while only the platform loop was guarded it filed as a
    # *video game* and sent a real IGDB request. First statement,
    # deliberately: an arm added below cannot sit above it, so the next arm
    # inherits the guard instead of the hole. Tier 3 and then the tier-4
    # hardware arm answer.
    if _is_hardware_title(title):
        return None
    for marker in _PLATFORM_MARKERS:
        if _contains_marker(title, marker):
            return Detection("video_game", (
                f"Title names the {marker} platform — filed as Video Game."
            ), "detected")
    for marker in _FORMAT_MARKERS:
        if _contains_marker(title, marker):
            return Detection("dvd", (
                f"Title carries a '{marker}' format tag — filed as DVD / Blu-ray."
            ), "detected")
    for marker in _MEDIUM_MARKERS:
        if _contains_marker(title, marker):
            return Detection("video_game", (
                f"Title carries a '{marker}' software-medium tag — "
                f"filed as Video Game."
            ), "detected")
    for marker in _AUDIO_MARKERS:
        if _contains_marker(title, marker):
            return Detection("cd", (
                f"Title carries a '{marker}' audio tag — filed as CD."
            ), "detected")
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


def _category_decides_cd(category: str) -> bool:
    """Tier 3's second admitted category, and it passes the same test.

    "Media > Music & Sound Recordings > Music CDs" names the *medium itself*
    — the "Software > Video Game Software" shape, not the "Electronics >
    Video Game Consoles" shape — so it is safe to decide alone and breaches
    neither prohibition above `_PLATFORM_MARKERS`: it decides `cd`, not
    `dvd`, and it names no platform. Measured over the probe corpus: true for
    5 of 6 real CD retail records, false for both sampled disc categories
    (Electronics > Video > Televisions), for Software > Video Game Software,
    for Electronics > Video Game Consoles, and for Media > Books.

    Deciding alone is the point. `Born in the USA` carries no title token at
    all and is 1 of the 6 observed records, so a confirmatory rule requiring
    a title tag too would miss it for no gain — the category has zero
    measured false positives.
    """
    return "music cd" in category.lower()


def detect_media_type(
    barcode_type: str, hint: str, title: str | None, category: str | None,
) -> Detection:
    """Return a `Detection`. `reason` is what the card shows.

    `signal` says how much the verdict is worth: `detected` if a tier 1-3
    rule fired, `hardware` if the title names console hardware, `hinted` if
    the user's non-book dropdown choice stood, `none` if nothing at all did.

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
            return Detection(hint, (
                f"Hint '{MEDIA_TYPES[hint]}' confirmed by ISBN barcode."
            ), "detected")
        if hint is not None:
            return Detection("book", (
                f"ISBN barcodes are books — overriding the "
                f"'{MEDIA_TYPES[hint]}' hint to Book."
            ), "detected")
        return Detection("book", "ISBN barcode — filed as Book.", "detected")

    # Tier 2: title markers.
    if title:
        matched = _match_title_markers(title)
        if matched is not None:
            return matched

    # Tier 3: category. Two admitted values, both naming a medium rather than
    # a shelf (see the two helpers' docstrings, and the prohibitions above
    # `_PLATFORM_MARKERS`). Reached only when tier 2 found nothing.
    if category and _category_decides_video_game(category):
        return Detection("video_game", (
            f"Category '{category}' names video game software — filed as "
            f"Video Game."
        ), "detected")
    if category and _category_decides_cd(category):
        return Detection("cd", (
            f"Category '{category}' names music CDs — filed as CD."
        ), "detected")

    # Tier 4, first: Shelf recognised the item, and recognised it as
    # something it has no media type for. That is a weaker answer than a
    # detection and a stronger one than nothing — it says what the item is
    # *not*, which is enough for a caller to decline a film search it would
    # otherwise lose. It sits above the hint branch deliberately: a dropdown
    # choice asserts what the item is, not that a film search on a title
    # containing "Console" will match.
    if title and _is_hardware_title(title):
        return Detection("dvd", (
            "Title names console hardware, not a film or a game — filed as "
            "DVD / Blu-ray. Change it on the item if that's wrong."
        ), "hardware")

    # Tier 4: no signal. Still resolve to a concrete MEDIA_TYPES member, and
    # say plainly whether this is the user's own answer or a fallback.
    #
    # A hint that reached this line survived tier 1, and on a non-ISBN barcode
    # nothing here contradicts it. The design's §1 rule is that a *book-family*
    # hint is wrong on a UPC — not that every hint is. So a deliberate "CD",
    # "DVD / Blu-ray" or "Video Game" choice stands, and only a book-family or
    # absent hint falls through to the fallback below.
    #
    # This is still load-bearing for CDs, for a narrower reason than it once
    # was. Tier 2 now reads an audio tag out of the retail title and tier 3
    # reads a "Music CDs" category, so a CD is usually detected — but where
    # the record names neither, the dropdown is the only evidence there is.
    # Discarding it here would silently refile those albums as DVDs — a
    # regression, not a detection, and invisible until a user noticed their
    # music shelf had turned into films.
    #
    # G57: the question this branch exists to answer is "which MEDIA_TYPES
    # values can detection never produce?", and every one of them must survive
    # a no-signal outcome. Re-asked after the CD arms landed, the answer is
    # now the book family only — `book`, `kids_book`, `audiobook`, `ebook`,
    # `comic` — which is exactly `_BOOK_FAMILY_HINTS`, and those are wrong on
    # a non-ISBN barcode for a different reason (tier 1's).
    if hint is not None and hint not in _BOOK_FAMILY_HINTS:
        return Detection(hint, (
            f"Nothing in the barcode or the product record said otherwise — "
            f"kept your '{MEDIA_TYPES[hint]}' choice."
        ), "hinted")

    if barcode_type == "upc":
        return Detection("dvd", (
            "UPC barcode carried no usable title or category signal — filed "
            "as DVD / Blu-ray. Change it on the item if that's wrong."
        ), "none")
    return Detection("dvd", (
        "Couldn't tell from the barcode — filed as DVD / Blu-ray. Change it "
        "on the item if that's wrong."
    ), "none")
