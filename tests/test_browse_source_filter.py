import re

from app import browse_filters as bf
from tests.conftest import _insert_item


def test_source_filter_is_registered_as_a_narrowing_browse_filter():
    source_filter = bf.BY_NAME["source_filter"]
    assert source_filter.prefix == "Source"
    assert source_filter.condition is not None

    where, params = bf.build_where({"source_filter": "romm"})
    assert where == "WHERE i.source = ?"
    assert params == ["romm"]


def test_source_filter_round_trips_in_browse_querystrings():
    qs = bf.querystring({"source_filter": "komga", "sort": "newest"})
    assert qs == "source_filter=komga"


def test_source_filter_participates_in_clear_and_chip_state():
    assert bf.has_active_filters({"source_filter": "audiobookshelf"})
    config = {entry["name"]: entry for entry in bf.client_config()}
    assert config["source_filter"]["prefix"] == "Source"
    assert config["source_filter"]["chip"] is True
    assert config["source_filter"]["inUrl"] is True


def _source_options(html: str) -> dict[str, str]:
    """value -> visible label for the first Source <select> in the markup."""
    select = re.search(r'<select id="source-filter".*?</select>', html, re.S).group(0)
    return {
        value: " ".join(label.split())
        for value, label in re.findall(r'<option value="([^"]*)"[^>]*>(.*?)</option>', select, re.S)
    }


def test_source_dropdown_lives_in_the_filter_bar_and_counts_what_exists(viewer_client, db):
    _insert_item(db, title="Chrono Trigger", isbn=None, source="romm")
    _insert_item(db, title="Secret of Mana", isbn=None, source="romm")
    _insert_item(db, title="Alien", isbn=None, source="tmdb")
    db.commit()

    html = viewer_client.get("/browse").text
    assert html.count('<select id="source-filter"') == 1
    options = _source_options(html)
    assert options["romm"] == "RomM (2)"
    # A source the static list never named is offered because an item has it.
    assert options["tmdb"] == "TMDb (1)"
    # ...and one no item carries is not offered at all.
    assert "komga" not in options


def test_source_counts_are_cross_filtered_and_refreshed_out_of_band(viewer_client, db):
    _insert_item(db, title="Chrono Trigger", isbn=None, source="romm", media_type="video_game")
    _insert_item(db, title="Dune", isbn=None, source="openlibrary", media_type="book")
    db.commit()

    fragment = viewer_client.get("/api/search?media_type_filter=book").text
    assert '<select id="source-filter"' in fragment and "hx-swap-oob" in fragment
    options = _source_options(fragment)
    assert options == {"": "All Sources", "openlibrary": "Open Library (1)"}

    # The Source group excludes its own filter, so choosing one source does not
    # erase the others from the dropdown.
    options = _source_options(viewer_client.get("/api/search?source_filter=romm").text)
    assert set(options) == {"", "romm", "openlibrary"}


def test_unknown_source_values_remain_representable(viewer_client, db):
    """A bookmarked/future source must not make the select lie about state."""
    _insert_item(db, title="Dune", isbn=None, source="openlibrary")
    db.commit()
    options = _source_options(viewer_client.get("/browse?source_filter=some_future_sync").text)
    assert options["some_future_sync"] == "Some Future Sync (0)"


def test_source_dropdown_labels_an_audible_import(viewer_client, db):
    _insert_item(db, title="Hail Mary", isbn=None, media_type="audiobook",
                 source="audible_import", asin="B0D1234567")
    db.commit()

    options = _source_options(viewer_client.get("/browse").text)

    assert options["audible_import"] == "Audible import (1)"
