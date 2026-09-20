from pathlib import Path

from app import browse_filters as bf


ITEM_GRID = Path(__file__).resolve().parents[1] / "app" / "templates" / "fragments" / "item_grid.html"


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


def test_native_browse_fragment_renders_source_control():
    src = ITEM_GRID.read_text()
    assert 'name="source_filter"' in src
    assert 'data-testid="source-filter"' in src
    assert "filter_includes('source_filter')" in src

    # The three synced-library integrations that motivated replacing the
    # provider-specific RomM catalogue must all be directly discoverable.
    for value in ("romm", "komga", "audiobookshelf"):
        assert f'value="{value}"' in src


def test_unknown_source_values_remain_representable():
    """A bookmarked/future source must not make the select lie about state."""
    src = ITEM_GRID.read_text()
    assert "current_source not in common_sources" in src
    assert '<option value="{{ current_source }}" selected>' in src
