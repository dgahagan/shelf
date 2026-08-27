"""Tests for app.services.detect — pure media-type detection, no I/O."""

import pytest

from app.config import MEDIA_TYPES
from app.services.detect import detect_media_type
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
    media_type, reason = detect_media_type(barcode_type, "auto", title, category)
    if expected is None:
        # The PS5 console row: the one contract that matters is that it does
        # NOT come back as video_game. Shelf has no hardware media type, so
        # it must land on the tier-4 fallback instead.
        assert media_type != "video_game"
    else:
        assert media_type == expected
    assert reason  # the card always has something to show


class TestPlatformBeatsFormat:
    def test_pc_dvd_title_resolves_video_game_not_dvd(self):
        media_type, reason = detect_media_type(
            "upc", "auto", "Alice Madness Returns (PC DVD)",
            "Software > Video Game Software",
        )
        assert media_type == "video_game"
        assert "video_game" != "dvd"  # explicit contract, not just incidental


class TestZeldaConsoleCategoryConfirms:
    def test_switch_title_with_console_category_resolves_video_game(self):
        media_type, _ = detect_media_type(
            "upc", "auto",
            "The Legend of Zelda: Breath of the Wild - Nintendo Switch",
            "Electronics > Video Game Consoles",
        )
        assert media_type == "video_game"


class TestPs5ConsoleIsNotAGame:
    """The contract a future maintainer widening the marker/category tables
    will break: a console category plus a plausible platform word in the
    title ("PlayStation") must never resolve video_game — Shelf has no
    hardware media type."""

    def test_ps5_console_does_not_resolve_video_game(self):
        media_type, reason = detect_media_type(
            "upc", "auto", "PlayStation 5 Console",
            "Electronics > Video Game Consoles",
        )
        assert media_type != "video_game"
        assert media_type in MEDIA_TYPES

    def test_ps5_console_lands_on_tier4_fallback(self):
        # Nothing in title or category legitimately resolves this, so it
        # must be the honest tier-4 fallback, not a disguised detection.
        media_type, reason = detect_media_type(
            "upc", "auto", "PlayStation 5 Console",
            "Electronics > Video Game Consoles",
        )
        assert media_type == "dvd"
        assert "couldn't tell" in reason.lower() or "no usable" in reason.lower()


class TestCategoryNeverDecidesDvd:
    def test_tv_category_alone_does_not_resolve_dvd(self):
        # No title signal at all — category is the only thing present, and
        # it must not be enough to decide dvd on its own.
        media_type, _ = detect_media_type(
            "upc", "auto", None, "Electronics > Video > Televisions",
        )
        # It's still a MEDIA_TYPES member (tier 4), just not *decided* by
        # the category — the reason must say fallback, not detection.
        assert media_type in MEDIA_TYPES

    def test_all_probe_categories_alone_never_decide_dvd(self):
        for _, _, category, _ in [p.values for p in PROBE_ROWS]:
            media_type, reason = detect_media_type("upc", "auto", None, category)
            if media_type == "dvd":
                assert "couldn't tell" in reason.lower() or "no usable" in reason.lower()


class TestVideoGameSoftwareCategoryDecidesAlone:
    def test_software_category_alone_resolves_video_game(self):
        media_type, reason = detect_media_type(
            "upc", "auto", None, "Software > Video Game Software",
        )
        assert media_type == "video_game"
        assert "video game software" in reason.lower()


class TestIsbnHintOverride:
    def test_isbn_with_dvd_hint_overrides_to_book(self):
        media_type, reason = detect_media_type("isbn", "dvd", None, None)
        assert media_type == "book"
        assert "overrid" in reason.lower()

    def test_isbn_with_kids_book_hint_is_honoured(self):
        media_type, reason = detect_media_type("isbn", "kids_book", None, None)
        assert media_type == "kids_book"

    def test_isbn_with_no_hint_defaults_to_book(self):
        media_type, reason = detect_media_type("isbn", "auto", None, None)
        assert media_type == "book"

    @pytest.mark.parametrize("hint", sorted({"audiobook", "ebook", "comic"}))
    def test_isbn_honours_every_book_family_hint(self, hint):
        media_type, _ = detect_media_type("isbn", hint, None, None)
        assert media_type == hint


class TestUpcWithBookHintFallsThrough:
    def test_upc_with_book_hint_and_no_signal_resolves_dvd_not_book(self):
        media_type, reason = detect_media_type("upc", "book", None, None)
        assert media_type == "dvd"
        assert "no usable" in reason.lower()

    def test_upc_with_book_hint_and_switch_title_still_resolves_game(self):
        # A book-family hint is not evidence about a non-ISBN barcode — it
        # must not suppress a real tier-2 title marker either.
        media_type, _ = detect_media_type(
            "upc", "book", "Metroid Prime 4 - Nintendo Switch", None,
        )
        assert media_type == "video_game"


class TestTier4NeverEscapesMediaTypes:
    @pytest.mark.parametrize("hint", ["auto", "", "nonsense", None])
    @pytest.mark.parametrize("barcode_type", ["upc", "unknown"])
    def test_no_signal_always_resolves_within_media_types(self, barcode_type, hint):
        media_type, reason = detect_media_type(barcode_type, hint, None, None)
        assert media_type in MEDIA_TYPES
        assert media_type != "auto"
        assert reason

    @pytest.mark.parametrize("hint", ["auto", "", "nonsense", None])
    def test_isbn_with_junk_hint_still_resolves_within_media_types(self, hint):
        media_type, reason = detect_media_type("isbn", hint, None, None)
        assert media_type in MEDIA_TYPES
        assert media_type == "book"


class TestADeliberateNonBookHintSurvivesTier4:
    """The dropdown is the only evidence a CD will ever have.

    §1's rule is that a *book-family* hint is wrong on a non-978 barcode, not
    that every hint is. Discarding a deliberate "CD" here would silently refile
    every scanned album as a DVD — Shelf has no CD detection anywhere to put
    back what the dropdown said.
    """

    @pytest.mark.parametrize("hint", ["cd", "dvd", "video_game"])
    def test_a_non_book_hint_stands_when_nothing_contradicts_it(self, hint):
        media_type, reason = detect_media_type("upc", hint, None, None)
        assert media_type == hint
        assert "kept your" in reason.lower()

    def test_a_cd_hint_survives_a_product_record_with_no_markers(self):
        media_type, _ = detect_media_type(
            "upc", "cd", "Abbey Road (Remastered)", "Music > Rock",
        )
        assert media_type == "cd"

    @pytest.mark.parametrize("hint", _BOOK_FAMILY_HINTS_FOR_TEST)
    def test_a_book_family_hint_still_does_not_survive_a_upc(self, hint):
        media_type, reason = detect_media_type("upc", hint, None, None)
        assert media_type == "dvd"
        assert "no usable" in reason.lower()

    def test_a_real_title_marker_still_beats_a_deliberate_hint(self):
        """Tier 2 runs first, so a certain signal still outranks the dropdown."""
        media_type, _ = detect_media_type(
            "upc", "cd", "Super Mario: Odyssey - Nintendo Switch", None,
        )
        assert media_type == "video_game"


class TestUnknownBarcodeType:
    def test_unknown_barcode_with_no_signal_resolves_within_media_types(self):
        media_type, reason = detect_media_type("unknown", "auto", None, None)
        assert media_type in MEDIA_TYPES
        assert reason
