"""The cover-shape rule (`app/config.py`, issue #119).

Music artwork is square, everything else is portrait. The rule is declared once
and every template reads it through the `cover_shape` Jinja global, which one
CSS rule turns into a square box. These tests pin the decision, the derivation
that keeps a new music format from falling back to portrait, and the two
places the rule has to reach: the Jinja globals and the built stylesheet.
"""

import re
from pathlib import Path

from app.config import (
    MEDIA_TYPES,
    MUSIC_MEDIA_TYPES,
    SQUARE_COVER_MEDIA_TYPES,
    cover_shape,
)

APP_CSS = Path(__file__).resolve().parent.parent / "static" / "css" / "app.css"


def test_every_music_type_is_square():
    for media_type in MUSIC_MEDIA_TYPES:
        assert cover_shape(media_type) == "square", media_type


def test_every_other_type_is_portrait():
    for media_type in set(MEDIA_TYPES) - MUSIC_MEDIA_TYPES:
        assert cover_shape(media_type) == "portrait", media_type


def test_square_set_is_derived_from_the_music_set():
    # Derived, not retyped: a fifth music format must arrive square.
    assert SQUARE_COVER_MEDIA_TYPES == MUSIC_MEDIA_TYPES


def test_missing_or_unknown_type_is_portrait_not_an_error():
    assert cover_shape(None) == "portrait"
    assert cover_shape("not_a_type") == "portrait"


def test_registered_as_a_jinja_global():
    from app.main import templates  # G14: import inside the test

    assert templates.env.globals["cover_shape"] is cover_shape


def test_built_stylesheet_carries_the_square_rule():
    # Tailwind's minifier may drop the attribute value's quotes; accept both.
    css = APP_CSS.read_text()
    assert re.search(r'\[data-cover-shape=("square"|square)\]', css)
