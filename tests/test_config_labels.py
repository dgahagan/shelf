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
        from app.config import browse_creator_label

        assert templates.env.globals["browse_creator_label"] is browse_creator_label

    def test_map_holds_only_overrides(self):
        """Nothing in the map may repeat the default — an entry that does is a
        type someone added to the map without changing its answer."""
        repeats = [k for k, v in CREATOR_LABELS.items() if v == DEFAULT_CREATOR_LABEL]
        assert not repeats, f"redundant CREATOR_LABELS entries: {sorted(repeats)}"


class TestMediaTypeAliases:
    """`MEDIA_TYPE_ALIASES` — retired physical types, mapped to what replaced
    them. Input-only: nothing at rest keeps the old spelling."""

    def test_no_alias_key_is_a_live_media_type(self):
        """A key that is still a MEDIA_TYPES member would give the vocabulary
        two spellings of one type, and every consumer would need both."""
        from app.config import MEDIA_TYPE_ALIASES

        live = sorted(set(MEDIA_TYPE_ALIASES) & set(MEDIA_TYPES))
        assert not live, f"alias keys that are still live types: {live}"

    def test_every_alias_target_is_a_live_media_type(self):
        from app.config import MEDIA_TYPE_ALIASES

        unknown = sorted(
            v for v in MEDIA_TYPE_ALIASES.values() if v not in MEDIA_TYPES
        )
        assert not unknown, f"alias targets that are not live types: {unknown}"

    def test_kids_book_canonicalises_to_book(self):
        from app.config import canonical_media_type

        assert canonical_media_type("kids_book") == "book"

    def test_a_live_type_is_returned_unchanged(self):
        from app.config import canonical_media_type

        for name in MEDIA_TYPES:
            assert canonical_media_type(name) == name

    def test_it_does_not_decide_validity(self):
        """An unknown value passes straight through — the caller's own
        membership test is what refuses it, and it runs after."""
        from app.config import canonical_media_type

        assert canonical_media_type("nonsense") == "nonsense"
        assert canonical_media_type("") == ""
        assert canonical_media_type(None) is None

    def test_kids_book_is_gone_from_the_live_vocabulary(self):
        assert "kids_book" not in MEDIA_TYPES
        assert "kids_book" not in BOOK_MEDIA_TYPES
