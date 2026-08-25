"""The Browse filter registry — the structural fix for G24.

A Browse filter used to be declared in four independent places (two templates'
`hx-include` lists, three condition groups in `search_items`, two name lists in
`browse.js`) with nothing keeping them in sync. These tests assert the
derivation, so a filter added to `app/browse_filters.py` is either wired
everywhere or fails here.
"""

import json
import re
from pathlib import Path

import pytest

from app import browse_filters as bf

REPO_ROOT = Path(__file__).resolve().parents[1]
BROWSE_HTML = REPO_ROOT / "app" / "templates" / "browse.html"
OOB_HTML = REPO_ROOT / "app" / "templates" / "fragments" / "filter_counts_oob.html"
BROWSE_JS = REPO_ROOT / "static" / "js" / "browse.js"


class TestRegistry:
    def test_names_are_unique(self):
        names = [f.name for f in bf.FILTERS]
        assert len(names) == len(set(names))

    def test_names_are_identifiers(self):
        # filter_includes() marks its output HTML-safe on the strength of this.
        for f in bf.FILTERS:
            assert bf._NAME_RE.match(f.name), f.name

    def test_rejects_a_name_that_could_inject_markup(self):
        with pytest.raises(ValueError):
            bf.BrowseFilter("q'],[name='x")

    def test_default_is_inactive(self):
        for f in bf.FILTERS:
            assert not f.is_active(f.default)
            assert not f.is_active("")


class TestFilterIncludes:
    def test_excludes_only_the_named_filter(self):
        for f in bf.FILTERS:
            rendered = bf.filter_includes(f.name)
            assert f"[name='{f.name}']" not in rendered
            for other in bf.FILTERS:
                if other.name != f.name:
                    assert f"[name='{other.name}']" in rendered

    def test_unknown_name_raises(self):
        # A typo must fail loudly at render rather than silently emitting the
        # full list — a silently-included control double-sends its value.
        with pytest.raises(KeyError):
            bf.filter_includes("locaton_filter")

    def test_no_literal_include_lists_remain_in_templates(self):
        """Every hx-include must call filter_includes(), not spell out names."""
        for path in (BROWSE_HTML, OOB_HTML):
            src = path.read_text()
            literals = re.findall(r'hx-include="\[name=', src)
            assert not literals, (
                f"{path.name} still has {len(literals)} hand-written "
                "hx-include list(s). Use {{ filter_includes('own_name') }} so "
                "the set stays derived from app/browse_filters.py."
            )

    def test_every_hx_include_names_a_real_filter(self):
        for path in (BROWSE_HTML, OOB_HTML):
            for name in re.findall(r"filter_includes\('([^']+)'\)", path.read_text()):
                assert name in bf.BY_NAME, f"{path.name} excludes unknown filter {name!r}"

    def test_each_oob_select_excludes_its_own_name(self):
        """G24's core trap: an OOB-swapped select that includes itself, or
        omits a sibling, breaks the *second* sequential filter change."""
        src = OOB_HTML.read_text()
        for tag in re.findall(r"<select[^>]*hx-swap-oob[^>]*>", src, re.S):
            own = re.search(r'name="([a-z_]+)"', tag).group(1)
            excluded = re.search(r"filter_includes\('([^']+)'\)", tag).group(1)
            assert own == excluded, (
                f"OOB select {own!r} excludes {excluded!r} from its "
                "hx-include — it must exclude itself and nothing else."
            )


class TestBuildWhere:
    def test_empty_when_nothing_active(self):
        assert bf.build_where({}) == ("", [])
        # sort and view never narrow, however they are set.
        assert bf.build_where({"sort": "title", "view": "list"}) == ("", [])

    def test_placeholders_match_param_count(self):
        values = {
            "q": "dune", "media_type_filter": "book", "location_filter": "3",
            "reading_status": "read", "owned": "1", "lent_out": "1",
            "tag": "sci-fi", "language": "eng", "sort": "title", "view": "list",
        }
        where, params = bf.build_where(values)
        assert where.count("?") == len(params)
        # owned/lent_out bind no parameter — the count only balances because
        # each condition's params are built together with its SQL.
        assert "i.owned = 1" in where

    def test_exclude_drops_exactly_one_condition(self):
        values = {"q": "dune", "location_filter": "3", "owned": "1"}
        full, full_params = bf.build_where(values)
        without, without_params = bf.build_where(values, exclude="location_filter")
        assert "i.location_id = ?" in full
        assert "i.location_id = ?" not in without
        assert 3 in full_params and 3 not in without_params
        assert without.count("?") == len(without_params)

    def test_every_filter_can_be_excluded(self):
        values = {f.name: "1" for f in bf.FILTERS}
        for f in bf.FILTERS:
            where, params = bf.build_where(values, exclude=f.name)
            assert where.count("?") == len(params)

    def test_location_is_cast_to_int(self):
        _where, params = bf.build_where({"location_filter": "7"})
        assert params == [7]

    @pytest.mark.parametrize("value", ["abc", "1.5", "7,8", "-", "9" * 22])
    def test_an_uncastable_location_matches_nothing(self, value):
        # Same answer a valid-but-unused id gives, rather than a 500: a filtered
        # Browse URL is shareable, so a hand-edited one must not crash. The
        # over-large id casts fine and would only fail at sqlite bind time.
        #
        # It must narrow to nothing, not fall back to no filter at all — an
        # empty WHERE here would render the whole collection under an active
        # Location chip.
        where, params = bf.build_where({"location_filter": value})
        assert where == "WHERE 1 = 0"
        assert params == []

    def test_owned_is_tri_state(self):
        assert "i.owned = 1" in bf.build_where({"owned": "1"})[0]
        assert "i.owned = 0" in bf.build_where({"owned": "0"})[0]
        assert bf.build_where({"owned": "either"}) == ("", [])

    def test_lent_out_only_applies_when_set(self):
        assert bf.build_where({"lent_out": "1"})[0] != ""
        assert bf.build_where({"lent_out": "0"}) == ("", [])


class TestValuesFrom:
    def test_missing_keys_become_defaults(self):
        values = bf.values_from({})
        assert set(values) == set(bf.FILTER_NAMES)
        assert values["sort"] == "newest"
        assert values["q"] == ""

    def test_blank_value_falls_back_to_default(self):
        # ?sort= must mean "newest", not an empty sort key.
        assert bf.values_from({"sort": ""})["sort"] == "newest"

    def test_unknown_params_are_ignored(self):
        values = bf.values_from({"q": "dune", "drop_table": "items"})
        assert values["q"] == "dune"
        assert "drop_table" not in values

    @pytest.mark.parametrize(
        "route_path",
        ["app.routers.items.search_items", "app.routers.pages.browse"],
    )
    def test_route_signature_declares_no_filter_params(self, route_path):
        """The filter set must not be re-declared as route parameters — that
        was the fifth copy. Only paging stays explicit."""
        import importlib
        import inspect

        module_path, func_name = route_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        route = getattr(module, func_name)

        params = set(inspect.signature(route).parameters)
        assert not params & set(bf.FILTER_NAMES), (
            f"{route_path} re-declares filter names in its signature; read them "
            "with browse_filters.values_from(request.query_params) instead."
        )


class TestHasActiveFilters:
    def test_language_alone_counts(self):
        """The hand-written version omitted language, so a language-only
        filter offered no way to clear it."""
        assert bf.has_active_filters({"language": "deu"})

    def test_presentation_only_filters_do_not_count(self):
        assert not bf.has_active_filters({"sort": "title", "view": "list"})

    def test_every_narrowing_filter_counts(self):
        for f in bf.FILTERS:
            if f.condition is None:
                continue
            value = "1" if f.name in ("owned", "lent_out") else "x"
            assert bf.has_active_filters({f.name: value}), f.name


class TestQuerystring:
    def test_skips_defaults(self):
        qs = bf.querystring({"sort": "newest", "q": "dune"})
        assert "sort=" not in qs
        assert "q=dune" in qs

    def test_omits_filters_the_url_does_not_own(self):
        """`view` is client-owned, and this URL is built once on page 1.

        The load-more sentinel carries `hx-include="[name='view']"`, which
        reads the live hidden input on every request; a copy baked into the
        URL here would be stale after a grid/list toggle and would put `view`
        on the wire twice (G8 — last duplicate wins, which happened to be the
        right one). Exactly the filters with `in_url=False` are omitted.
        """
        qs = bf.querystring({"view": "list", "q": "dune"})
        assert "view=" not in qs
        assert "q=dune" in qs
        omitted = [f.name for f in bf.FILTERS if not f.in_url]
        assert omitted == ["view"]

    def test_tag_is_percent_encoded(self):
        qs = bf.querystring({"tag": "science & fiction"})
        assert "tag=science%20%26%20fiction" in qs

    def test_extra_parts_are_appended(self):
        assert bf.querystring({}, extra=["page=2"]) == "page=2"


class TestClientConfig:
    def test_browse_js_reads_the_json_block(self):
        assert 'id="browse-filter-config"' in BROWSE_HTML.read_text()
        assert "browse-filter-config" in BROWSE_JS.read_text()

    def test_no_hand_written_name_list_remains_in_js(self):
        """browse.js's literal list had already drifted — it was missing
        'view'. Nothing may reintroduce one."""
        src = BROWSE_JS.read_text()
        assert "'media_type_filter', 'location_filter'" not in src

    def test_config_is_json_serialisable_and_complete(self):
        config = json.loads(json.dumps(bf.client_config()))
        assert [c["name"] for c in config] == list(bf.FILTER_NAMES)
        for entry in config:
            assert set(entry) == {"name", "prefix", "default", "chip", "clearTo", "inUrl"}

    def test_view_is_client_owned(self):
        """view rides along to the server but localStorage owns it: writing it
        to the URL would leave a bare /browse showing ?view=grid forever."""
        view = bf.BY_NAME["view"]
        assert not view.in_url and not view.chip and view.clear_to is None

    def test_sort_clears_to_its_default_not_empty(self):
        assert bf.BY_NAME["sort"].clear_to == "newest"
