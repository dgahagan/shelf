"""The per-type status words: one verb map in app/config.py, read as a Jinja global."""
import pytest

from app.config import (
    BOOK_MEDIA_TYPES,
    MEDIA_TYPES,
    NEUTRAL_LABELS,
    PLAY_LABELS,
    READ_LABELS,
    STATUS_MEDIA_TYPES,
    STATUS_VERBS,
    WATCH_LABELS,
    StatusLabels,
    status_labels,
)


@pytest.mark.parametrize("media_type", sorted(MEDIA_TYPES))
def test_every_media_type_resolves_to_a_verb_set(media_type):
    assert isinstance(status_labels(media_type), StatusLabels)


def test_discs_are_watched_and_games_are_played():
    assert status_labels("dvd").key == "watch"
    assert status_labels("video_game").key == "play"


@pytest.mark.parametrize("media_type", sorted(BOOK_MEDIA_TYPES | {"cd", "magazine"}))
def test_the_book_family_and_other_types_read_by_default(media_type):
    assert status_labels(media_type).key == "read"


@pytest.mark.parametrize("media_type", [None, ""])
def test_no_type_gives_the_neutral_set(media_type):
    assert status_labels(media_type).key == "neutral"


def test_an_unknown_type_reads():
    assert status_labels("not_a_media_type").key == "read"


def test_label_maps_each_stored_value():
    assert WATCH_LABELS.label("want_to_read") == "Want to Watch"
    assert WATCH_LABELS.label("reading") == "Watching"
    assert WATCH_LABELS.label("read") == "Watched"
    assert WATCH_LABELS.label(None) == ""
    assert WATCH_LABELS.label("bogus") == ""


def test_the_words_are_pinned():
    assert WATCH_LABELS.want == "Want to Watch"
    assert PLAY_LABELS.done_lower == "played"
    assert NEUTRAL_LABELS.doing == "In progress"
    assert READ_LABELS.done == "Read"
    assert NEUTRAL_LABELS.done_lower == "finished"


def test_status_media_types_is_the_book_family_plus_disc_and_games():
    assert STATUS_MEDIA_TYPES == BOOK_MEDIA_TYPES | {"dvd", "video_game", "board_game"}


def test_status_verbs_holds_overrides_only():
    assert all(labels != READ_LABELS for labels in STATUS_VERBS.values())


def test_registered_as_a_jinja_global():
    from app.main import templates  # G14: import inside the test

    assert templates.env.globals["status_labels"] is status_labels
