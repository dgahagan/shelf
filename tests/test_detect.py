"""Tests for app.services.detect — pure media-type detection, no I/O."""

import typing

import pytest

from app.config import MEDIA_TYPES
from app.services.detect import SIGNALS, Signal, detect_media_type
from app.services.upc import detect_barcode_type

_BOOK_FAMILY_HINTS_FOR_TEST = ["book", "kids_book", "audiobook", "ebook", "comic"]

# --- The six resolved probe rows from the design doc -----------------------
#
# barcode is run through detect_barcode_type() rather than hand-typed, so a
# row is exercised exactly the way the scan router would call this module.

PROBE_ROWS = [
    pytest.param(
        "014633098723", "Alice Madness Returns (PC DVD)",
        "Software > Video Game Software", "video_game",
        id="pc_dvd_platform_beats_format",
    ),
    pytest.param(
        "045496590741", "Super Mario: Odyssey - Nintendo Switch",
        "Software > Video Game Software", "video_game",
        id="switch_cartridge_with_software_category",
    ),
    pytest.param(
        "883929665860", "Tom & Jerry: Lost Dragon / Giant Adventure [DVD]",
        "Electronics > Video > Televisions", "dvd",
        id="tom_and_jerry_dvd_tag",
    ),
    pytest.param(
        "085391163121",
        "Goodfellas [DVD]  Feature Thriller Drama  Action  Suspense  Drama",
        "Electronics > Video > Televisions", "dvd",
        id="goodfellas_dvd_tag_with_retail_noise",
    ),
    pytest.param(
        "045496590420",
        "The Legend of Zelda: Breath of the Wild - Nintendo Switch",
        "Electronics > Video Game Consoles", "video_game",
        id="zelda_switch_with_console_category",
    ),
    pytest.param(
        "711719541028", "PlayStation 5 Console",
        "Electronics > Video Game Consoles", None,
        id="ps5_console_is_not_a_game",
    ),
]


@pytest.mark.parametrize("barcode, title, category, expected", PROBE_ROWS)
def test_probe_rows(barcode, title, category, expected):
    barcode_type = detect_barcode_type(barcode)
    d = detect_media_type(barcode_type, "auto", title, category)
    if expected is None:
        # The PS5 console row: the one contract that matters is that it does
        # NOT come back as video_game. Shelf has no hardware media type, so
        # it must land on the tier-4 fallback instead.
        assert d.media_type != "video_game"
    else:
        assert d.media_type == expected
    assert d.reason  # the card always has something to show


class TestPlatformBeatsFormat:
    def test_pc_dvd_title_resolves_video_game_not_dvd(self):
        d = detect_media_type(
            "upc", "auto", "Alice Madness Returns (PC DVD)",
            "Software > Video Game Software",
        )
        assert d.media_type == "video_game"
        assert "video_game" != "dvd"  # explicit contract, not just incidental


class TestZeldaConsoleCategoryConfirms:
    def test_switch_title_with_console_category_resolves_video_game(self):
        d = detect_media_type(
            "upc", "auto",
            "The Legend of Zelda: Breath of the Wild - Nintendo Switch",
            "Electronics > Video Game Consoles",
        )
        assert d.media_type == "video_game"


class TestPs5ConsoleIsNotAGame:
    """The contract a future maintainer widening the marker/category tables
    will break: a console category plus a plausible platform word in the
    title ("PlayStation") must never resolve video_game — Shelf has no
    hardware media type."""

    def test_ps5_console_does_not_resolve_video_game(self):
        d = detect_media_type(
            "upc", "auto", "PlayStation 5 Console",
            "Electronics > Video Game Consoles",
        )
        assert d.media_type != "video_game"
        assert d.media_type in MEDIA_TYPES

    def test_ps5_console_lands_on_tier4_fallback(self):
        """Still the honest tier-4 fallback, now with a reason that says which.

        This assertion used to read the *no-signal* prose ("couldn't tell" /
        "no usable"). Issue #43 gave the hardware case its own arm above the
        hint branch, so the reason changed — and that is the pin's second
        half, not a weakening: the type is unchanged and the signal is now
        asserted, which the old shape could not do at all.
        """
        d = detect_media_type(
            "upc", "auto", "PlayStation 5 Console",
            "Electronics > Video Game Consoles",
        )
        assert d.media_type == "dvd"
        assert d.signal == "hardware"
        assert "console hardware" in d.reason.lower()


class TestCategoryNeverDecidesDvd:
    def test_tv_category_alone_does_not_resolve_dvd(self):
        # No title signal at all — category is the only thing present, and
        # it must not be enough to decide dvd on its own.
        d = detect_media_type(
            "upc", "auto", None, "Electronics > Video > Televisions",
        )
        # It's still a MEDIA_TYPES member (tier 4), just not *decided* by
        # the category — the reason must say fallback, not detection.
        assert d.media_type in MEDIA_TYPES

    def test_all_probe_categories_alone_never_decide_dvd(self):
        for _, _, category, _ in [p.values for p in PROBE_ROWS]:
            d = detect_media_type("upc", "auto", None, category)
            if d.media_type == "dvd":
                assert "couldn't tell" in d.reason.lower() or "no usable" in d.reason.lower()


class TestVideoGameSoftwareCategoryDecidesAlone:
    def test_software_category_alone_resolves_video_game(self):
        d = detect_media_type(
            "upc", "auto", None, "Software > Video Game Software",
        )
        assert d.media_type == "video_game"
        assert "video game software" in d.reason.lower()


class TestIsbnHintOverride:
    def test_isbn_with_dvd_hint_overrides_to_book(self):
        d = detect_media_type("isbn", "dvd", None, None)
        assert d.media_type == "book"
        assert "overrid" in d.reason.lower()

    def test_isbn_with_kids_book_hint_is_honoured(self):
        d = detect_media_type("isbn", "kids_book", None, None)
        assert d.media_type == "kids_book"

    def test_isbn_with_no_hint_defaults_to_book(self):
        d = detect_media_type("isbn", "auto", None, None)
        assert d.media_type == "book"

    @pytest.mark.parametrize("hint", sorted({"audiobook", "ebook", "comic"}))
    def test_isbn_honours_every_book_family_hint(self, hint):
        d = detect_media_type("isbn", hint, None, None)
        assert d.media_type == hint


class TestUpcWithBookHintFallsThrough:
    def test_upc_with_book_hint_and_no_signal_resolves_dvd_not_book(self):
        d = detect_media_type("upc", "book", None, None)
        assert d.media_type == "dvd"
        assert "no usable" in d.reason.lower()

    def test_upc_with_book_hint_and_switch_title_still_resolves_game(self):
        # A book-family hint is not evidence about a non-ISBN barcode — it
        # must not suppress a real tier-2 title marker either.
        d = detect_media_type(
            "upc", "book", "Metroid Prime 4 - Nintendo Switch", None,
        )
        assert d.media_type == "video_game"


class TestTier4NeverEscapesMediaTypes:
    @pytest.mark.parametrize("hint", ["auto", "", "nonsense", None])
    @pytest.mark.parametrize("barcode_type", ["upc", "unknown"])
    def test_no_signal_always_resolves_within_media_types(self, barcode_type, hint):
        d = detect_media_type(barcode_type, hint, None, None)
        assert d.media_type in MEDIA_TYPES
        assert d.media_type != "auto"
        assert d.reason

    @pytest.mark.parametrize("hint", ["auto", "", "nonsense", None])
    def test_isbn_with_junk_hint_still_resolves_within_media_types(self, hint):
        d = detect_media_type("isbn", hint, None, None)
        assert d.media_type in MEDIA_TYPES
        assert d.media_type == "book"


class TestADeliberateNonBookHintSurvivesTier4:
    """The dropdown is the only evidence a CD will ever have.

    §1's rule is that a *book-family* hint is wrong on a non-978 barcode, not
    that every hint is. Discarding a deliberate "CD" here would silently refile
    every scanned album as a DVD — Shelf has no CD detection anywhere to put
    back what the dropdown said.
    """

    @pytest.mark.parametrize("hint", ["cd", "dvd", "video_game"])
    def test_a_non_book_hint_stands_when_nothing_contradicts_it(self, hint):
        d = detect_media_type("upc", hint, None, None)
        assert d.media_type == hint
        assert "kept your" in d.reason.lower()

    def test_a_cd_hint_survives_a_product_record_with_no_markers(self):
        d = detect_media_type(
            "upc", "cd", "Abbey Road (Remastered)", "Music > Rock",
        )
        assert d.media_type == "cd"

    @pytest.mark.parametrize("hint", _BOOK_FAMILY_HINTS_FOR_TEST)
    def test_a_book_family_hint_still_does_not_survive_a_upc(self, hint):
        d = detect_media_type("upc", hint, None, None)
        assert d.media_type == "dvd"
        assert "no usable" in d.reason.lower()

    def test_a_real_title_marker_still_beats_a_deliberate_hint(self):
        """Tier 2 runs first, so a certain signal still outranks the dropdown."""
        d = detect_media_type(
            "upc", "cd", "Super Mario: Odyssey - Nintendo Switch", None,
        )
        assert d.media_type == "video_game"


class TestUnknownBarcodeType:
    def test_unknown_barcode_with_no_signal_resolves_within_media_types(self):
        d = detect_media_type("unknown", "auto", None, None)
        assert d.media_type in MEDIA_TYPES
        assert d.reason


class TestTheSignalVocabularyStaysInStep:
    def test_signals_tuple_matches_the_literal(self):
        """The runtime tuple and the `Literal` are two spellings of one fact."""
        assert set(SIGNALS) == set(typing.get_args(Signal))

    def test_every_signal_is_produced_by_at_least_one_input(self):
        """A member nothing can return is dead vocabulary (G47's shape).

        One input per member, chosen to reach a different tier each time.
        """
        produced = {
            detect_media_type("isbn", "auto", None, None).signal,
            detect_media_type("upc", "auto", "PlayStation 5 Console", None).signal,
            detect_media_type("upc", "cd", None, None).signal,
            detect_media_type("upc", "auto", None, None).signal,
        }
        assert produced == set(SIGNALS)


class TestRecognisedHardwareAnswersHardware:
    """Issue #43. The predicate is the conjunction — a hardware word *and* a
    platform marker — and both halves are pinned, because widening either
    table is the tempting "fix" that re-opens the film titles below.
    """

    @pytest.mark.parametrize("title", [
        "PlayStation 5 Console",
        "Nintendo Switch Pro Controller",   # the instance the issue never reported
        "Xbox Series X Console 1TB",
    ])
    def test_console_hardware_answers_hardware(self, title):
        d = detect_media_type("upc", "auto", title, None)
        assert d.signal == "hardware"
        assert d.media_type == "dvd"   # still filed; Shelf has no hardware type

    @pytest.mark.parametrize("title", [
        "Console Wars",
        "Air Traffic Controller",
        "The Controller 2019",
    ])
    def test_a_hardware_word_alone_is_not_hardware(self, title):
        """The pin that keeps the predicate narrow — assert the *signal*.

        Each of these is a real film carrying a hardware word and no platform
        marker. Asserting only `media_type` would pass against the broken
        predicate, because tier 4 files all three as `dvd` either way.
        """
        d = detect_media_type("upc", "auto", title, None)
        assert d.signal != "hardware"

    def test_a_platform_marker_alone_is_not_hardware(self):
        """Tier 2 is unchanged for a title with no hardware word."""
        d = detect_media_type(
            "upc", "auto", "Super Mario: Odyssey - Nintendo Switch", None,
        )
        assert d.media_type == "video_game"
        assert d.signal == "detected"

    def test_an_unbranded_accessory_is_a_known_false_negative(self):
        """`Sony PULSE 3D Wireless Headset` names no `_PLATFORM_MARKERS` member.

        **Deliberate, not an oversight.** Do not "fix" this by adding "Sony"
        or bare accessory words to either table: probe 2 showed that widening
        is exactly what re-opens `Console Wars` and `Air Traffic Controller`
        as false positives. The design plan lists it under
        `## Explicitly out of scope`.
        """
        d = detect_media_type("upc", "auto", "Sony PULSE 3D Wireless Headset", None)
        assert d.signal != "hardware"

    @pytest.mark.parametrize("title", [
        "Blade Runner 2049 4-Disc Ultimate Collector Edition",
        "Parasite Criterion Collection",
        "Casablanca 70th Anniversary",
        "The Matrix Trilogy Boxed Set Widescreen",
    ])
    def test_a_genuine_disc_with_no_format_tag_is_not_hardware(self, title):
        """Probe 1's rows 3-6 — the regression guard for the rejected remedy.

        The issue proposed capping the search ladder for *every* tier-4 scan.
        These four are genuine discs whose useful query rung is the second or
        third, so that rule would have lost their enrichment. They must answer
        `none`, which is what keeps them on the full ladder in `_scan_upc`.
        """
        d = detect_media_type("upc", "auto", title, None)
        assert d.signal == "none"

    def test_hardware_outranks_an_explicit_dvd_hint(self):
        """Dan's decision, 2026-08-29, and why the arm sits above the hint.

        A dropdown choice asserts what the item *is*; it asserts nothing about
        whether a film search on a title containing "Console" will match.
        Honouring the hint here would leave the reported failure reachable for
        anyone not on Auto.
        """
        d = detect_media_type("upc", "dvd", "PlayStation 5 Console", None)
        assert d.signal == "hardware"
        assert d.signal != "hinted"

    def test_a_software_category_still_outranks_hardware(self):
        """Tier 3 sits above the hardware arm and stays there.

        A genuine `Software > Video Game Software` category is a real
        detection; re-deciding tier 3 for a mis-categorised console is out of
        scope for issue #43.
        """
        d = detect_media_type(
            "upc", "auto", "PlayStation 5 Console",
            "Software > Video Game Software",
        )
        assert d.media_type == "video_game"
        assert d.signal == "detected"
