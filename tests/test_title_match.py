"""Tests for app.services.title_match — the printed-ISBN title-agreement guard.

The table below is the design plan's behaviour matrix (section 3, step 4),
carried verbatim: it is the *specification*, and `titles_agree` must satisfy
every row. The thresholds inside the helper are implementation constants and
may change; these rows may not.
"""

import pytest

from app.services.title_match import titles_agree


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
