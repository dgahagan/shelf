"""The creator-field label map (`app/config.py`).

`items.authors` is one column for every media type, so the label the add form
puts on that field is a per-type decision declared once in config. These tests
pin the decision itself — which type reads what — plus the two structural
properties that keep the map from rotting: no key outside `MEDIA_TYPES`, and no
music format that has to be remembered by hand.
"""

from app.config import (
    BOOK_MEDIA_TYPES,
    CREATOR_LABELS,
    DEFAULT_CREATOR_LABEL,
    MEDIA_TYPES,
    MUSIC_MEDIA_TYPES,
    creator_label,
)


class TestCreatorLabelValues:
    """One assertion per decision the map makes."""

    def test_every_music_type_is_artist(self):
        # Derived from MUSIC_MEDIA_TYPES, not retyped — a fifth format added
        # there must arrive as "Artist" without anyone editing the map.
        for media_type in MUSIC_MEDIA_TYPES:
            assert creator_label(media_type) == "Artist", media_type

    def test_video_game_is_developer(self):
        assert creator_label("video_game") == "Developer"

    def test_dvd_is_director(self):
        assert creator_label("dvd") == "Director"

    def test_every_book_family_type_falls_back_to_authors(self):
        for media_type in BOOK_MEDIA_TYPES:
            assert creator_label(media_type) == DEFAULT_CREATOR_LABEL, media_type

    def test_magazine_falls_back_to_authors(self):
        """Deliberate: a magazine's `authors` column holds contributors, and
        the publication has its own `publisher` field. Not an omission."""
        assert "magazine" not in CREATOR_LABELS
        assert creator_label("magazine") == DEFAULT_CREATOR_LABEL

    def test_default_label_is_unchanged(self):
        assert DEFAULT_CREATOR_LABEL == "Author(s)"


class TestCreatorLabelMapIntegrity:
    """Structural properties — what stops the map drifting from MEDIA_TYPES."""

    def test_every_key_is_a_real_media_type(self):
        """A typo'd key would be a dead entry nothing ever reads."""
        unknown = set(CREATOR_LABELS) - set(MEDIA_TYPES)
        assert not unknown, f"CREATOR_LABELS keys not in MEDIA_TYPES: {sorted(unknown)}"

    def test_every_media_type_resolves_to_a_non_empty_label(self):
        for media_type in MEDIA_TYPES:
            assert creator_label(media_type)

    def test_unknown_media_type_falls_back_rather_than_raising(self):
        assert creator_label("not_a_media_type") == DEFAULT_CREATOR_LABEL
        assert creator_label(None) == DEFAULT_CREATOR_LABEL

    def test_registered_as_jinja_globals(self):
        """Both templates read the map as a Jinja global, so neither host route
        needs a context key. If the registration goes, the placeholder silently
        renders empty rather than raising."""
        from app.main import templates  # G14: import inside the test

        assert templates.env.globals["creator_label"] is creator_label
        assert templates.env.globals["creator_labels"] is CREATOR_LABELS

    def test_map_holds_only_overrides(self):
        """Nothing in the map may repeat the default — an entry that does is a
        type someone added to the map without changing its answer."""
        repeats = [k for k, v in CREATOR_LABELS.items() if v == DEFAULT_CREATOR_LABEL]
        assert not repeats, f"redundant CREATOR_LABELS entries: {sorted(repeats)}"
