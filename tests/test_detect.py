"""Tests for app.services.detect — pure media-type detection, no I/O."""

import typing

import pytest

from app.config import MEDIA_TYPES, BOOK_MEDIA_TYPES
from app.services import detect
from app.services.detect import SIGNALS, Signal, detect_media_type
from app.services.upc import detect_barcode_type

_BOOK_FAMILY_HINTS_FOR_TEST = sorted(BOOK_MEDIA_TYPES)

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
    """When the record names neither, the dropdown is the evidence.

    §1's rule is that a *book-family* hint is wrong on a non-978 barcode, not
    that every hint is. Tier 2 now reads an audio tag out of the retail title
    and tier 3 reads a "Music CDs" category, so most albums are detected — but
    where the record carries neither, discarding a deliberate "CD" here would
    still silently refile the album as a DVD, and nothing downstream could put
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
    platform marker or a hardware brand — and both halves are pinned. The
    second half widened to brands on 2026-09-01 (roadmap residual (ii)); the
    film pin below is what keeps the conjunction honest, because dropping the
    hardware-word half is the tempting "fix" that re-opens those titles.
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

    @pytest.mark.parametrize("title", [
        "Logitech G Pro X Gaming Headset",
        "SteelSeries Arctis 7 Wireless Headset",
        "Corsair HS80 RGB Wireless Headset",
        "Thrustmaster T300 RS Racing Controller",
        "Turtle Beach Stealth 600 Headset",
        "Sony PULSE 3D Wireless Headset",
        "Sony PULSE 3D Wireless Headset CD-ROM",
        "Sony PULSE 3D Wireless Headset [DVD]",
        "PS5 PULSE 3D Wireless Headset",
        "8BitDo Pro 2 Controller",
    ])
    def test_a_brand_named_accessory_is_hardware(self, title):
        """The design's ten measured hardware rows — roadmap residual (ii).

        This used to pin `Sony PULSE 3D Wireless Headset` as an *accepted*
        false negative, on the argument that its shortened search stops at
        three words. That argument was measured to depend on brand length:
        `Logitech G Pro X Gaming Headset` descends to the bare one-word rung
        `Logitech` (G46). The second half of the conjunction is now a
        platform marker *or* a `_HARDWARE_BRANDS` member.
        """
        d = detect_media_type("upc", "auto", title, None)
        assert d.signal == "hardware"
        assert d.media_type == "dvd"
        assert "console hardware" in d.reason.lower()

    @pytest.mark.parametrize("title,check", [
        ("Astro Boy", ("signal", "none")),
        ("Turtle Beach [DVD]", ("detected", "dvd")),
        ("Ghost in the Shell [Blu-ray]", ("detected", "dvd")),
        ("Mission Control [DVD]", ("detected", "dvd")),
        ("Sony Pictures Classics Presents Whiplash", ("not-hardware", None)),
        ("The Sony Betamax Story", ("not-hardware", None)),
        ("Corsair", ("not-hardware", None)),
        ("The Corsair 1931", ("not-hardware", None)),
        ("Turtle Beach", ("not-hardware", None)),
    ])
    def test_a_brand_alone_is_not_hardware(self, title, check):
        """The design's adversaries: a brand word without a hardware word.

        `Sony` is the one film-industry name in the table, so both Sony rows
        are the specific judgment a reviewer should check. The three films
        that carry a hardware word and no brand are pinned separately in
        `test_a_hardware_word_alone_is_not_hardware`, not duplicated here.
        """
        d = detect_media_type("upc", "auto", title, None)
        kind, expected = check
        if kind == "signal":
            assert d.signal == expected
        elif kind == "detected":
            assert d.signal == "detected"
            assert d.media_type == expected
        else:
            assert d.signal != "hardware"

    def test_the_brand_table_is_discoverable(self):
        """Guard for the structural pin below (G31): a rename must not make
        it iterate an empty list and pass vacuously."""
        assert detect._HARDWARE_BRANDS
        assert detect._HARDWARE_TERMS
        assert "Sony" in detect._HARDWARE_BRANDS

    @pytest.mark.parametrize("brand,term", [
        pytest.param(b, t, id=f"{b}-{t}")
        for b in detect._HARDWARE_BRANDS
        for t in detect._HARDWARE_TERMS
    ])
    def test_every_brand_conjoined_with_every_hardware_word_is_hardware(
        self, brand, term,
    ):
        """The structural pin, built by introspection like `_EVERY_MARKER`.

        A brand added to `_HARDWARE_BRANDS` later is covered here without
        editing the test — every brand × every hardware word answers
        `hardware`, and every brand on its own does not.
        """
        d = detect_media_type("upc", "auto", f"{brand} Wireless {term.title()}", None)
        assert d.signal == "hardware", f"{brand!r} + {term!r} not recognised"
        alone = detect_media_type("upc", "auto", f"{brand} Anthology", None)
        assert alone.signal != "hardware", f"{brand!r} alone decided hardware"

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



# --- G68: the hardware guard covers every tier-2 arm, not just the platform one


_EVERY_MARKER = [
    pytest.param(name, marker, id=f"{name.strip('_').lower()}-{marker}")
    for name, table in sorted(vars(detect).items())
    if name.endswith("_MARKERS")
    for marker in table
]


class TestAHardwareTitleSkipsEveryTitleMarkerArm:
    """`_is_hardware_title` gates all four tier-2 arms, not the platform loop.

    Before this, the guard wrapped only the platform loop, so a hardware title
    carrying a format, medium or audio word was decided by that word. The
    `CD-ROM` row is the one that mattered: it filed as `video_game`, which
    `UPC_METADATA_PROVIDERS` maps to IGDB, so a scanned PS5 headset sent a real
    provider request. The guard is now the function's **first statement** — an
    arm added below it cannot sit above it.
    """

    @pytest.mark.parametrize("tag", [
        "",              # the control: already `hardware` before this change
        " DVD", " [DVD]", " Blu-ray",   # the format arm
        " CD-ROM",                      # the medium arm — filed `video_game`
        " CD", " Audio CD",             # the audio arm
    ])
    def test_a_tag_on_a_hardware_title_does_not_decide(self, tag):
        """The design plan's measured rows. Only `""` was green before."""
        d = detect_media_type(
            "upc", "auto", "PlayStation 5 Wireless Headset" + tag, None,
        )
        assert d.signal == "hardware"
        assert d.media_type == "dvd"
        assert "console hardware" in d.reason.lower()

    @pytest.mark.parametrize("title", [
        "Nintendo Switch Pro Controller DVD",
        "Xbox Series X Console Blu-ray",
    ])
    def test_a_controller_or_console_carrying_a_tag_is_still_hardware(self, title):
        d = detect_media_type("upc", "auto", title, None)
        assert d.signal == "hardware"
        assert d.media_type == "dvd"

    def test_the_marker_tables_are_discoverable(self):
        """Guard for the structural pin below — introspection must match something.

        Without this, renaming the tables would make
        `test_no_marker_in_any_table_decides_a_hardware_title` iterate an empty
        list and pass vacuously.
        """
        names = {n for n in vars(detect) if n.endswith("_MARKERS")}
        assert {
            "_PLATFORM_MARKERS", "_FORMAT_MARKERS",
            "_MEDIUM_MARKERS", "_AUDIO_MARKERS",
        } <= names
        assert _EVERY_MARKER

    @pytest.mark.parametrize("table_name,marker", _EVERY_MARKER)
    def test_no_marker_in_any_table_decides_a_hardware_title(self, table_name, marker):
        """The structural pin: every marker in every `*_MARKERS` table.

        A new table added to `detect`, or a new loop over an existing one, is
        covered here without anyone editing this test — the parametrise list is
        built by introspection. The only way to re-open G68 is to insert an arm
        *above* the guard, and the guard is the function's first statement.

        14 of these rows (the bare platform tokens) were green before the fix;
        the other 13 were red, so judge a mutation on those — `PC DVD` filed
        `dvd` off the format arm, `PC CD` filed `cd` off the audio arm.
        """
        d = detect_media_type(
            "upc", "auto", f"PlayStation 5 Wireless Headset {marker}", None,
        )
        assert d.signal == "hardware", f"{table_name} member {marker!r} decided"

    @pytest.mark.parametrize("title,expected", [
        ("Console Wars [DVD]", "dvd"),
        ("Air Traffic Controller Blu-ray", "dvd"),
        ("The Controller 2019 DVD", "dvd"),
        ("Console Wars CD", "cd"),
    ])
    def test_a_film_carrying_a_tag_is_still_detected(self, title, expected):
        """The guard's scope grew; the predicate's recall did not.

        These name a hardware word and no platform marker, so
        `_is_hardware_title` is false and the tier-2 arm decides them exactly
        as before. If this reddens, someone widened `_HARDWARE_TERMS`,
        `_PLATFORM_MARKERS` or `_HARDWARE_BRANDS` — see `_is_hardware_title`'s
        docstring.
        """
        d = detect_media_type("upc", "auto", title, None)
        assert d.media_type == expected
        assert d.signal == "detected"

    @pytest.mark.parametrize("tag", ["", " CD-ROM", " [DVD]", " Audio CD"])
    def test_a_brand_named_accessory_survives_a_tag(self, tag):
        """Roadmap residual (ii), shapes (ii-a) and (ii-c), closed 2026-09-01.

        This used to pin the tagged Sony headset as `detected` off the format
        arm — an accepted false negative. It was not harmless: the `CD-ROM`
        shape filed `video_game` and reached IGDB, and the `[DVD]` shape made
        a confident format claim about a headset. With `Sony` in
        `_HARDWARE_BRANDS`, the guard declines every arm for it exactly as it
        does for `PlayStation 5 Wireless Headset`.
        """
        d = detect_media_type(
            "upc", "auto", "Sony PULSE 3D Wireless Headset" + tag, None,
        )
        assert d.signal == "hardware"
        assert d.media_type == "dvd"

    def test_the_platform_arm_is_unchanged_for_a_non_hardware_title(self):
        """The guard did not cost the platform loop its own job."""
        d = detect_media_type(
            "upc", "auto", "Alice Madness Returns (PC DVD)", None,
        )
        assert d.media_type == "video_game"
        assert d.signal == "detected"
        assert "PC DVD" in d.reason

    @pytest.mark.parametrize("category,expected", [
        ("Software > Video Game Software", "video_game"),
        ("Media > Music & Sound Recordings > Music CDs", "cd"),
    ])
    def test_tier_3_still_outranks_the_guard(self, category, expected):
        """The early return is inside tier 2; tier 3 is untouched.

        The `Music CDs` row was red before the fix — the medium arm decided
        `video_game` before tier 3 ever ran. Re-deciding tier 3 for a
        mis-categorised accessory is out of scope: the category is a real
        detection either way, and the design's boundary is tier 2 only.
        """
        d = detect_media_type(
            "upc", "auto", "PlayStation 5 Wireless Headset CD-ROM", category,
        )
        assert d.media_type == expected
        assert d.signal == "detected"

    def test_hardware_still_outranks_a_hint_when_the_title_carries_a_tag(self):
        """The tier-4 arm sits above the hint branch, tag or no tag."""
        d = detect_media_type(
            "upc", "video_game", "PlayStation 5 Wireless Headset DVD", None,
        )
        assert d.signal == "hardware"
        assert d.media_type == "dvd"

# --- Issue: a scanned music CD is detected, not guessed as a film ----------
#
# The six rows are the real upcitemdb records for six real CD barcodes
# (design plan, probe 1). They are pasted as literals rather than read from
# `.devdocs/plan-scan-audio-signal-probes/cd_titles.json` — `.devdocs/` is
# gitignored, and a committed test may not depend on it.

_OBSERVED_CD_RECORDS = [
    pytest.param(
        "Fleetwood Mac - Rumours - CD",
        "Media > Music & Sound Recordings > Music CDs",
        id="rumours",
    ),
    pytest.param(
        "The Beatles - Abbey Road - CD",
        "Media > Music & Sound Recordings > Music CDs",
        id="abbey_road",
    ),
    pytest.param(
        "Clockcleaner - Nevermind - Rock - CD",
        "Media > Music & Sound Recordings > Music CDs",
        id="nevermind",
    ),
    pytest.param(
        "The Eagles - Hotel California - Music & Performance - CD",
        "Media > Music & Sound Recordings > Music CDs",
        id="hotel_california",
    ),
    pytest.param(
        # Title only: the category is the bare "Media", which decides nothing.
        "Miles Davis Kind of Blue Audio CD", "Media",
        id="kind_of_blue_title_only",
    ),
    pytest.param(
        # Category only: no audio tag anywhere in the title.
        "Born in the USA", "Media > Music & Sound Recordings > Music CDs",
        id="born_in_the_usa_category_only",
    ),
]


class TestAMusicDiscIsDetectedAsACD:
    """Tier 2 gains an audio arm and tier 3 a music-CD arm.

    Before this, `cd` was a value only the dropdown could supply: every
    scanned album fell to the tier-4 fallback, filed as `dvd` with
    `signal="none"`, and climbed the TMDb ladder. The two arms are
    complementary and their union covers 6 of 6 observed records.
    """

    @pytest.mark.parametrize("title, category", _OBSERVED_CD_RECORDS)
    def test_every_observed_cd_record_is_detected_as_a_cd(self, title, category):
        d = detect_media_type("upc", "auto", title, category)
        assert d.media_type == "cd"
        assert d.signal == "detected"
        assert d.reason

    def test_a_title_audio_tag_decides_without_any_category(self):
        """The `Kind of Blue` arm: category is the bare "Media".

        The reason names the *fuller* tag, because `_AUDIO_MARKERS` is
        ordered most-specific-first.
        """
        d = detect_media_type("upc", "auto", "Miles Davis Kind of Blue Audio CD", "Media")
        assert d.media_type == "cd"
        assert "'Audio CD'" in d.reason

    def test_a_music_cd_category_decides_without_any_title_tag(self):
        """The `Born in the USA` arm: nothing in the title says CD."""
        d = detect_media_type(
            "upc", "auto", "Born in the USA",
            "Media > Music & Sound Recordings > Music CDs",
        )
        assert d.media_type == "cd"
        assert d.signal == "detected"
        assert "music cd" in d.reason.lower()

    @pytest.mark.parametrize("title, marker", [
        ("Purple Rain [DVD/CD Combo]", "DVD"),
        ("The Bodyguard Blu-ray + Soundtrack CD", "Blu-ray"),
        ("Woodstock 40th Anniversary [Blu-ray] Bonus CD", "Blu-ray"),
        ("Terminator 2 [DVD] (includes bonus CD-ROM)", "[DVD]"),
    ])
    def test_a_disc_bundle_carrying_cd_is_still_a_disc(self, title, marker):
        """Format is checked before every arm that can be wrong about it.

        The first three film rows in the probe corpus that fire on "CD" are
        disc bundles that also carry a format tag. Audio-first would file a
        concert Blu-ray as a music album.

        The fourth is the same argument one rung over, and the reason
        `CD-ROM` is not in `_PLATFORM_MARKERS`: a platform token beats a
        format tag, so while `CD-ROM` sat in that table this bundle filed as
        a *video game* with a confident reason. Restore it there and this
        row goes red.
        """
        d = detect_media_type("upc", "auto", title, None)
        assert d.media_type == "dvd"
        assert f"'{marker}'" in d.reason

    @pytest.mark.parametrize("title", [
        "Vinyl",
        "Almost Famous",
        "The Soundtrack of Our Lives",
        "Sound of Metal",
        "Off the Record",
        "Family Album",
        "Stereo",
        "Disco Pigs",
        "High Fidelity",
        "The Last Record Store",
    ])
    def test_an_audio_word_adversary_is_never_a_cd(self, title):
        """The roadmap's anticipated risk, measured and avoided.

        `Soundtrack`, `Vinyl` and `Record` as tokens would catch every one of
        these; none of them is needed, and none of them is in the table. Each
        row must still reach the honest tier-4 fallback.
        """
        d = detect_media_type("upc", "auto", title, None)
        assert d.media_type == "dvd"
        assert d.signal == "none"

    @pytest.mark.parametrize("title", [
        "Myst PC CD-ROM",
        "The Sims 2 PC CD-ROM Deluxe",
        "Command & Conquer Red Alert (PC CD-ROM)",
        "Baldur's Gate II PC CD ROM",
    ])
    def test_a_pc_cd_game_is_a_game(self, title):
        """`PC CD` joins `PC DVD` in the platform table.

        All four carry that token, and the platform arm runs before the
        `CD-ROM` medium arm, so the reason names the more specific one.
        Without it the audio arm would file four PC games as music albums.
        """
        d = detect_media_type("upc", "auto", title, None)
        assert d.media_type == "video_game"
        assert d.signal == "detected"
        assert "PC CD" in d.reason

    @pytest.mark.parametrize("title", [
        "Myst CD-ROM",
        "Command & Conquer (CD-ROM)",
    ])
    def test_a_bare_cd_rom_game_is_a_game(self, title):
        """`CD-ROM` gets its own arm, between format and audio.

        `_contains_marker` treats the hyphen as a word boundary, so the "CD"
        audio marker matches *inside* "CD-ROM". A bare CD-ROM title carries no
        `PC CD` and no format tag, so with `PC CD` alone these two would fall
        to the audio arm and file as music albums with a confident reason —
        an admitted guess upgraded to a confident misfile. Below format, not
        above it, so a film bundle carrying the token still files as a disc
        (`test_a_disc_bundle_carrying_cd_is_still_a_disc`).
        """
        d = detect_media_type("upc", "auto", title, None)
        assert d.media_type == "video_game"
        assert d.signal == "detected"
        assert "CD-ROM" in d.reason

    def test_tier_2_still_runs_before_the_music_cd_category(self):
        """A `Music CDs` category never overrules a marker already in the title.

        upcitemdb files disc bundles under music categories often enough that
        this is a real shape, not a hypothetical one.
        """
        d = detect_media_type(
            "upc", "auto", "Purple Rain [DVD/CD Combo]",
            "Media > Music & Sound Recordings > Music CDs",
        )
        assert d.media_type == "dvd"

    def test_an_audio_tag_beats_a_deliberate_hint(self):
        """The existing tier-2-outranks-the-dropdown rule, now reachable for CDs.

        Same rule as `test_a_real_title_marker_still_beats_a_deliberate_hint`
        one class up; this is the CD instance of it.
        """
        d = detect_media_type("upc", "dvd", "Fleetwood Mac - Rumours - CD", None)
        assert d.media_type == "cd"
        assert d.signal == "detected"

    @pytest.mark.parametrize("hint", sorted(MEDIA_TYPES))
    def test_every_undetectable_hint_still_survives_tier_4(self, hint):
        """G57, re-asked after the CD arms landed.

        The entry's question is "list the values this detector can never
        produce" — each one is a value only the user can supply, so each must
        survive a no-signal outcome. `cd` has now come off that list, which
        makes the list exactly the book family. This is the repaired form of
        the entry's own Verify script, which unpacks a two-tuple `Detection`
        has not been since #43.
        """
        d = detect_media_type("upc", hint, None, None)
        assert d.media_type == hint or hint in set(_BOOK_FAMILY_HINTS_FOR_TEST)


def test_every_five_type_book_family_declaration_agrees_with_config():
    """config.BOOK_MEDIA_TYPES is the family's home; the three older five-type
    literals are deferred repoints (design §1) and must not drift from it.
    synopsis.BOOK_MEDIA_TYPES is deliberately four and is not compared."""
    from app.routers import items_catalog, series

    assert BOOK_MEDIA_TYPES <= set(MEDIA_TYPES)
    assert detect._BOOK_FAMILY_HINTS == BOOK_MEDIA_TYPES
    assert set(series.UNASSIGNED_MEDIA_TYPES) == BOOK_MEDIA_TYPES
    assert items_catalog.BOOK_MEDIA_TYPES == BOOK_MEDIA_TYPES
