"""Tests for app.services.title_match — the printed-ISBN title-agreement guard.

The table below is the design plan's behaviour matrix (section 3, step 4),
carried verbatim: it is the *specification*, and `titles_agree` must satisfy
every row. The thresholds inside the helper are implementation constants and
may change; these rows may not.
"""

import pytest

from app.services.title_match import titles_agree, titles_match_exactly


# Matrix row label -> (row title, catalog title, must accept).
MATRIX = [
    # --- accepts ---
    ("identical", "Dune", "Dune", True),
    (
        "punctuation-and-case-only",
        "Harry Potter and the Philosophers Stone",
        "Harry Potter and the Philosopher's Stone",
        True,
    ),
    (
        "catalog-series-decoration",
        "Dune",
        "Dune (Dune Chronicles, Book 1)",
        True,
    ),
    (
        "one-token-kids-title-catalog-decoration",
        "Corduroy",
        "Corduroy (Picture Puffin Books)",
        True,
    ),
    (
        "catalog-subtitle",
        "The Hobbit",
        "The Hobbit: Or There and Back Again",
        True,
    ),
    (
        "cover-decoration-catalog-bare",
        "Goodnight Moon (Board Book)",
        "Goodnight Moon",
        True,
    ),
    (
        "whitespace-and-typography-only",
        "Where the Wild Things Are",
        "Where  the  Wild  Things  Are’ ",
        True,
    ),
    (
        "whitespace-and-typography-only-heavier",
        "Where the Wild Things Are",
        "Where   the   Wild   Things   Are’ ",
        True,
    ),
    (
        "alt-title-tail-no-comma",
        "The Hobbit or There and Back Again",
        "The Hobbit",
        True,
    ),
    (
        "alt-title-tail-comma",
        "The Hobbit, or There and Back Again",
        "The Hobbit",
        True,
    ),
    (
        "alt-title-tail-semicolon-comma",
        "Moby-Dick; or, The Whale",
        "Moby Dick",
        True,
    ),
    (
        "one-sided-edition-subtitle",
        "Harry Potter and the Philosopher's Stone",
        "Harry Potter and the Philosopher's Stone: Illustrated Edition",
        True,
    ),
    # --- rejects ---
    ("empty-normalized-input", "東京", "Tokyo Story", False),
    ("trivial-short-substring", "It", "Little Fires Everywhere", False),
    ("same-series-no-numerics", "Dune", "Dune Messiah", False),
    (
        "same-series-high-similarity",
        "Frog and Toad Are Friends",
        "Frog and Toad Together",
        False,
    ),
    (
        "numeric-disagreement",
        "The Walking Dead Volume 1",
        "The Walking Dead Volume 2",
        False,
    ),
    ("unrelated", "Dune", "The Martian", False),
    (
        "bare-series-name",
        "Harry Potter",
        "Harry Potter and the Chamber of Secrets",
        False,
    ),
    (
        "both-subtitled-same-series-prefix",
        "Magic Tree House: Dinosaurs Before Dark",
        "Magic Tree House: The Knight at Dawn",
        False,
    ),
    (
        "both-subtitled-distinct-subtitles",
        "Star Wars: A New Hope",
        "Star Wars: The Last Jedi",
        False,
    ),
    (
        "both-parenthetical-distinct-volumes",
        "Dune (Book 2)",
        "Dune (Book 1)",
        False,
    ),
    (
        "both-subtitled-distinct-volume-numbers",
        "A History of Britain: Volume 2",
        "A History of Britain: Volume 1",
        False,
    ),
    (
        "both-decorated-mixed-kinds",
        "Dune (Dune Chronicles, Book 1)",
        "Dune: House Atreides",
        False,
    ),
    (
        "or-inside-an-ordinary-title",
        "Do or Die",
        "Die",
        False,
    ),
    (
        "or-inside-an-ordinary-title-longer",
        "Now or Never",
        "Now",
        False,
    ),
    (
        "both-alt-titled-distinct-books",
        "The Hobbit, or There and Back Again",
        "Frankenstein, or The Modern Prometheus",
        False,
    ),
    ("catalog-title-none", "Dune", None, False),
    ("catalog-title-empty", "Dune", "", False),
]


@pytest.mark.parametrize(
    "row_title,catalog_title,expected",
    [pytest.param(r, c, e, id=label) for label, r, c, e in MATRIX],
)
def test_matrix(row_title, catalog_title, expected):
    assert titles_agree(row_title, catalog_title) is expected


class TestFailsClosed:
    def test_non_string_row_title(self):
        assert titles_agree(None, "Dune") is False
        assert titles_agree(7, "Dune") is False

    def test_empty_row_title(self):
        assert titles_agree("", "Dune") is False


class TestTitlesMatchExactly:
    """`titles_match_exactly` is the strict retail guard (variant E' from
    the design's probe) — see its docstring for why it never calls
    `_strip_decoration`. The two false-accept pins below are the reason it
    exists; per GOTCHAS G31 both were hand-verified to turn red under the
    mutation that removes the property they pin (documented in the task
    report, not re-run here): inserting a `_strip_decoration` call reddens
    the Dune pin, and removing the roman-numeral mapping reddens the
    Modern-Warfare pin.
    """

    # --- the false-accept regression pins ---

    def test_dune_does_not_match_dune_part_two(self):
        assert titles_match_exactly("Dune", "Dune: Part Two") is False

    def test_alien_does_not_match_aliens(self):
        assert titles_match_exactly("ALIEN", "Aliens") is False

    # --- accepts across punctuation and case ---

    def test_mad_max_fury_road(self):
        assert titles_match_exactly("MAD MAX FURY ROAD", "Mad Max: Fury Road") is True

    def test_top_gun_maverick(self):
        assert titles_match_exactly("TOP GUN MAVERICK", "Top Gun: Maverick") is True

    def test_call_of_duty_modern_warfare_roman(self):
        assert (
            titles_match_exactly(
                "CALL OF DUTY MODERN WARFARE II", "Call of Duty: Modern Warfare II"
            )
            is True
        )

    # --- diacritic folding ---

    def test_diacritic_folding(self):
        assert titles_match_exactly("Pokémon", "Pokemon") is True

    # --- numeral equivalence (roman vs arabic) ---

    def test_roman_arabic_numeral_equivalence(self):
        assert titles_match_exactly("Modern Warfare II", "Modern Warfare 2") is True

    # --- documented residual false-rejects: franchise-prefix omission is
    # explicitly out of scope (a franchise prefix and a sequel suffix are
    # the same shape to a matcher; recovering one reopens the other) ---

    def test_franchise_prefix_omitted_no_way_home(self):
        assert titles_match_exactly("NO WAY HOME", "Spider-Man: No Way Home") is False

    def test_franchise_prefix_omitted_empire_strikes_back(self):
        assert (
            titles_match_exactly(
                "THE EMPIRE STRIKES BACK", "Star Wars: The Empire Strikes Back"
            )
            is False
        )

    # --- defensive: fail closed ---

    def test_none_catalog_title(self):
        assert titles_match_exactly("Dune", None) is False

    def test_empty_catalog_title(self):
        assert titles_match_exactly("Dune", "") is False

    def test_non_string_row_title(self):
        assert titles_match_exactly(7, "Dune") is False

    def test_cjk_only_title_normalizes_empty(self):
        assert titles_match_exactly("東京", "Tokyo Story") is False
