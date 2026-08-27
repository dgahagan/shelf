"""The UPC Item DB client and its retail-title ladder (issue #36 §3, §4).

The four raw titles below are verbatim from live lookups recorded in the design
plan's Observation 2 — they are why the ladder exists rather than a single
normalised query.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services import upcitemdb


ALICE = "Alice Madness Returns (PC DVD)"
TOM = "Tom & Jerry: Lost Dragon / Giant Adventure [DVD]"
MARIO = "Super Mario: Odyssey - Nintendo Switch"
GOODFELLAS = (
    "Goodfellas [DVD]  Feature Thriller Drama  Action  Suspense  Drama  "
    "Crime  Drama Drama"
)
# A bracket nested inside a parenthesised tag — `_BRACKETED` stops at the inner
# closer, so the outer one has to be trimmed off the rung.
NESTED = "Movie (Director's Cut [Special])"


class StubResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data

    def json(self):
        return self._json


@pytest.fixture
def fake_fetch():
    # G37: patch on the module that defines fetch.
    with patch("app.services.outbound.fetch", new=AsyncMock()) as m:
        yield m


def _no_retry_timeouts(call):
    return call.kwargs.get("retry_timeouts", False) is False


class TestCleanTitle:
    def test_a_parenthesised_format_tag_is_dropped(self):
        assert upcitemdb.clean_title(ALICE) == "Alice Madness Returns"

    def test_a_bracketed_format_tag_is_dropped_without_touching_the_title(self):
        assert upcitemdb.clean_title(TOM) == "Tom & Jerry: Lost Dragon / Giant Adventure"

    def test_a_trailing_platform_suffix_is_dropped(self):
        assert upcitemdb.clean_title(MARIO) == "Super Mario: Odyssey"

    def test_the_goodfellas_row_loses_its_tag_but_keeps_the_category_keywords(self):
        # Stripping alone cannot rescue this row — that is the ladder's job.
        assert upcitemdb.clean_title(GOODFELLAS) == (
            "Goodfellas Feature Thriller Drama Action Suspense Drama Crime Drama Drama"
        )

    def test_edition_noise_is_dropped_anywhere_in_the_title(self):
        assert upcitemdb.clean_title("Blade Runner Special Edition Blu-ray") == "Blade Runner"

    def test_matching_is_case_insensitive_but_survivors_keep_their_case(self):
        assert upcitemdb.clean_title("The Thing [dvd] WIDESCREEN") == "The Thing"

    def test_a_noise_word_inside_a_real_word_is_not_touched(self):
        assert upcitemdb.clean_title("DVDA Records Presents") == "DVDA Records Presents"

    def test_stacked_platform_suffixes_are_all_dropped(self):
        assert upcitemdb.clean_title("Zelda - Switch - DVD") == "Zelda"

    def test_a_clean_title_is_returned_unchanged(self):
        assert upcitemdb.clean_title("The Matrix") == "The Matrix"

    def test_a_format_only_title_becomes_empty(self):
        assert upcitemdb.clean_title("[DVD]") == ""

    def test_empty_input_is_empty(self):
        assert upcitemdb.clean_title("") == ""
        assert upcitemdb.clean_title("   ") == ""


class TestSearchQueries:
    def test_the_goodfellas_ladder_shortens_all_the_way_to_the_film(self):
        queries = upcitemdb.search_queries(GOODFELLAS)
        assert queries[-1] == "Goodfellas"
        assert queries[0].startswith("Goodfellas Feature")

    def test_the_mario_ladder_starts_whole_and_drops_the_subtitle(self):
        queries = upcitemdb.search_queries(MARIO)
        assert queries[0] == "Super Mario: Odyssey"
        assert "Super Mario" in queries

    def test_a_clean_title_yields_a_single_query(self):
        assert upcitemdb.search_queries("The Matrix") == ["The Matrix"]

    def test_a_leading_article_is_kept_on_the_shortest_rung(self):
        # "The" alone is a useless query; "The Matrix" is not.
        assert upcitemdb.search_queries("The Matrix Reloaded Special Edition")[-1] == "The Matrix"

    def test_every_query_differs_from_a_raw_title_that_carried_a_tag(self):
        for raw in (ALICE, TOM, MARIO, GOODFELLAS):
            assert raw not in upcitemdb.search_queries(raw)

    def test_the_ladder_never_exceeds_four_rungs(self):
        for raw in (ALICE, TOM, MARIO, GOODFELLAS, "A B C D E F G H"):
            assert len(upcitemdb.search_queries(raw)) <= 4

    def test_rungs_are_deduplicated_in_order(self):
        queries = upcitemdb.search_queries(TOM)
        assert queries == list(dict.fromkeys(queries))

    def test_no_rung_ends_on_dangling_punctuation(self):
        # Reads the module's own set rather than a copy of it: the stray-bracket
        # bug survived because this assertion pinned an outdated duplicate.
        for raw in (ALICE, TOM, MARIO, GOODFELLAS, NESTED):
            for query in upcitemdb.search_queries(raw):
                assert query == query.strip(upcitemdb._EDGE_PUNCT)

    def test_a_nested_format_tag_leaves_no_orphan_bracket(self):
        # `_BRACKETED` stops at the first closer, so the outer ")" survives the
        # substitution and has to be trimmed — it reached both the provider
        # query and, on a total miss, the filed title.
        assert upcitemdb.search_queries(NESTED) == ["Movie"]

    def test_a_short_first_word_is_not_offered_as_a_query(self):
        # A one-word search does not fail, it matches a different work: "Tom"
        # returns real films, none of them this disc. Below MIN_SOLO_WORD the
        # ladder stops and the item is filed title-only.
        assert "Tom" not in upcitemdb.search_queries(TOM)
        assert "Super" not in upcitemdb.search_queries(MARIO)
        assert "Alice" not in upcitemdb.search_queries(ALICE)

    def test_the_floor_does_not_eat_the_rung_the_ladder_exists_for(self):
        # The other half of the floor: "Goodfellas" is what recovers the
        # category-keyword shape, and it must survive.
        assert upcitemdb.search_queries(GOODFELLAS)[-1] == "Goodfellas"

    def test_a_whole_title_shorter_than_the_floor_is_still_queried(self):
        # The floor applies to a *shortening*, never to the title itself —
        # otherwise "It" and "M" would become unscannable.
        assert upcitemdb.search_queries("It [DVD]") == ["It"]
        assert upcitemdb.search_queries("M (Blu-ray)") == ["M"]

    def test_an_empty_or_format_only_title_yields_no_queries(self):
        # T6's not_found guard depends on this: an empty ladder must not be
        # indexed, and no provider call should be made for a title-less product.
        assert upcitemdb.search_queries("") == []
        assert upcitemdb.search_queries("   ") == []
        assert upcitemdb.search_queries("[DVD]") == []


class TestLookup:
    async def test_it_returns_the_whole_useful_product(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"items": [{
            "title": GOODFELLAS,
            "category": "Electronics > Video > Televisions",
            "brand": "Warner",
            "images": ["https://i5.walmartimages.com/x.jpg"],
        }]})

        product = await upcitemdb.lookup("085391163121", object())

        assert product == {
            "title": GOODFELLAS,
            "category": "Electronics > Video > Televisions",
            "brand": "Warner",
            "images": ["https://i5.walmartimages.com/x.jpg"],
        }

    async def test_missing_fields_come_back_as_none_and_an_empty_list(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"items": [{"title": "X"}]})
        product = await upcitemdb.lookup("1", object())
        assert product == {"title": "X", "category": None, "brand": None, "images": []}

    async def test_it_routes_through_outbound_without_retry_timeouts(self, fake_fetch):
        """Request path: a retried read timeout must not triple HTTP_TIMEOUT."""
        fake_fetch.return_value = StubResponse(200, json_data={"items": [{"title": "X"}]})
        client = object()

        await upcitemdb.lookup("085391163121", client)

        call = fake_fetch.await_args
        assert call.args == (client, "GET", upcitemdb.UPC_LOOKUP_URL)
        assert call.kwargs.get("params") == {"upc": "085391163121"}
        assert call.kwargs.get("timeout") == 10
        assert _no_retry_timeouts(call)

    async def test_a_404_is_none(self, fake_fetch):
        fake_fetch.return_value = StubResponse(404)
        assert await upcitemdb.lookup("1", object()) is None

    async def test_an_empty_item_list_is_none(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"items": []})
        assert await upcitemdb.lookup("1", object()) is None

    @pytest.mark.parametrize(
        "exc",
        [httpx.ConnectError("offline"), httpx.ReadTimeout("slow")],
        ids=["connect-error", "read-timeout"],
    )
    async def test_a_transport_failure_propagates(self, exc, fake_fetch):
        """Offline is not "no such record" (GOTCHAS G47).

        This pinned the opposite until this branch: the bare `except
        Exception` swallowed a broken resolver into `None`, so a self-hoster
        with no DNS was told the disc was not found and the scan was logged
        `not_found`. `_scan_upc`'s connectivity handler was dead code that
        read as live. `httpx.NetworkError` covers ConnectError/ReadError/
        WriteError/CloseError; TimeoutException is a separate branch of the
        hierarchy and needs naming.
        """
        fake_fetch.side_effect = exc
        with pytest.raises((httpx.TimeoutException, httpx.NetworkError)):
            await upcitemdb.lookup("1", object())

    async def test_a_429_calls_on_rate_limit_and_returns_none(self, fake_fetch):
        """T6: a rate-limited product lookup is not 'unknown barcode' either."""
        fake_fetch.return_value = StubResponse(429)
        calls = []
        result = await upcitemdb.lookup(
            "1", object(), on_rate_limit=lambda: calls.append(1)
        )
        assert result is None
        assert calls == [1]

    async def test_a_malformed_body_is_still_none(self, fake_fetch):
        """The contract the bare catch existed for — assert it explicitly,
        not by omission: these three are what keep an unresolvable barcode
        reaching the manual-add form."""
        class _Bad:
            status_code = 200

            def json(self):
                raise ValueError("not json")

        fake_fetch.return_value = _Bad()
        assert await upcitemdb.lookup("1", object()) is None


def test_the_module_does_not_import_the_provider_clients():
    """It sits below tmdb/igdb, not beside them — no import cycle either way."""
    import inspect

    source = inspect.getsource(upcitemdb)
    assert "import tmdb" not in source
    assert "import igdb" not in source
