"""Tests for app.services.sbn — SBN OPAC flat-JSON metadata client.

Fixtures were captured from the live endpoint on 2026-09-02; every network
-touching test mocks it with respx, so the suite stays offline. Pure client
logic, no app/db needed (see G14 — no module-level `from app.main import app`).
"""

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from app.services import sbn

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# G31 — the mutations this class was checked against, each reddening the row
# named for it. A pin that survives its own mutation reads as coverage and
# defends nothing, so these were run for real before the suite was trusted:
#   1. `_select_record` -> `records[0] if records else None` (the naive
#      implementation the issue proposes) reddens
#      test_multi_record_picks_the_exact_isbn_author_bearing_record and
#      test_records_all_carrying_another_isbn_are_no_match.
#   2. `resp.json()` hoisted above the catch-all in `lookup` reddens
#      test_a_200_with_a_non_json_body_is_no_match_not_a_raise (G66).
#   3. `_language`'s single-value guard relaxed to "any value" reddens
#      test_language_is_read_only_from_an_unambiguous_lingua_facet.
#   4. reading `autorePrincipale` raw instead of through
#      `bib_normalize.invert_name` (G26) reddens the two rows that assert
#      `authors` in display order.


class TestSbnLookup:
    @respx.mock
    @pytest.mark.asyncio
    async def test_single_record_yields_every_mapped_field(self):
        respx.get("https://opac.sbn.it/opacmobilegw/search.json").mock(
            return_value=httpx.Response(
                200, text=_fixture("sbn_search_9788842092995.json")
            )
        )
        async with httpx.AsyncClient() as client:
            result = await sbn.lookup("9788842092995", client)

        assert result.outcome == "found"
        meta = result.payload
        # `titolo` is one ISBD string — "La quarta rivoluzione : sei lezioni
        # sul futuro del libro / Gino Roncaglia" — split by bib_normalize.
        assert meta["title"] == "La quarta rivoluzione"
        assert meta["subtitle"] == "sei lezioni sul futuro del libro"
        # `autorePrincipale` "Roncaglia, Gino <1960-    >" in display order.
        assert meta["authors"] == "Gino Roncaglia"
        assert meta["publisher"] == "GLF editori Laterza"
        assert meta["publish_year"] == 2010
        assert meta["language"] == "it"  # MARC "ita" from the `lingua` facet

    @respx.mock
    @pytest.mark.asyncio
    async def test_multi_record_picks_the_exact_isbn_author_bearing_record(self):
        """`briefRecords` lists records SBN considers *related*, so [0] is not
        the answer. Here [0] carries no `autorePrincipale` and the year 2025;
        [1] is the exact-ISBN, author-bearing record; [2] and [3] carry a
        different ISBN entirely (978-88-418-6255-1, years 2010 and 2015).

        G31/G45: this asserts the **stored fields**, not the request. A test
        that asserted only the query URL would pass against `briefRecords[0]`,
        which is precisely the bug.
        """
        respx.get("https://opac.sbn.it/opacmobilegw/search.json").mock(
            return_value=httpx.Response(
                200, text=_fixture("sbn_search_9791221200454.json")
            )
        )
        async with httpx.AsyncClient() as client:
            result = await sbn.lookup("9791221200454", client)

        assert result.outcome == "found"
        meta = result.payload
        assert meta["authors"] == "Steve Stevenson"
        assert meta["publish_year"] == 2022
        assert meta["publisher"] == "De Agostini"
        assert meta["title"] == "L' enigma del faraone"
        # And the payload is a dict, not a list — items_common assigns it
        # straight to `metadata` and indexes it (G45).
        assert isinstance(meta, dict)

    @respx.mock
    @pytest.mark.asyncio
    async def test_records_all_carrying_another_isbn_are_no_match(self):
        """Every record answers a different ISBN than the one queried. The
        exact-ISBN filter must refuse it rather than store a related edition:
        SBN short-circuits the cascade, so a confidently wrong imprint is
        worse for the user than Open Library's thinner record.
        """
        respx.get("https://opac.sbn.it/opacmobilegw/search.json").mock(
            return_value=httpx.Response(200, text=_fixture("sbn_search_wrong_isbn.json"))
        )
        async with httpx.AsyncClient() as client:
            result = await sbn.lookup("9791221200454", client)

        assert result.outcome == "no_match"
        assert result.payload is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_no_hit_is_no_match(self):
        respx.get("https://opac.sbn.it/opacmobilegw/search.json").mock(
            return_value=httpx.Response(200, text=_fixture("sbn_search_nohit.json"))
        )
        async with httpx.AsyncClient() as client:
            result = await sbn.lookup("9788854519794", client)

        assert result.outcome == "no_match"

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_200_with_a_non_json_body_is_no_match_not_a_raise(self):
        """G66: "never raises" is not earned by handling the request while the
        parse is still bare. A captive portal or proxy login page answers 200
        with HTML; `resp.json()` must be inside the catch-all, not above it.
        """
        respx.get("https://opac.sbn.it/opacmobilegw/search.json").mock(
            return_value=httpx.Response(200, content=b"<html>not json</html>")
        )
        async with httpx.AsyncClient() as client:
            result = await sbn.lookup("9788842092995", client)

        assert result.outcome == "no_match"

    @respx.mock
    @pytest.mark.asyncio
    async def test_rate_limited_status_is_rate_limited(self):
        respx.get("https://opac.sbn.it/opacmobilegw/search.json").mock(
            return_value=httpx.Response(429)
        )
        async with httpx.AsyncClient() as client:
            result = await sbn.lookup("9788842092995", client)

        assert result.outcome == "rate_limited"

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_server_error_is_no_match(self):
        respx.get("https://opac.sbn.it/opacmobilegw/search.json").mock(
            return_value=httpx.Response(500)
        )
        async with httpx.AsyncClient() as client:
            result = await sbn.lookup("9788842092995", client)

        assert result.outcome == "no_match"
        assert result.status == 500

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_connection_error_is_transport_failed(self):
        """Offline and "no such record" are not the same outcome (G47): the
        scan card tells the user to check connectivity for one and falls
        through to Open Library for the other.
        """
        respx.get("https://opac.sbn.it/opacmobilegw/search.json").mock(
            side_effect=httpx.ConnectError("boom")
        )
        async with httpx.AsyncClient() as client:
            result = await sbn.lookup("9788842092995", client)

        assert result.outcome == "transport_failed"

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_field_mapping_exception_degrades_to_no_match(self):
        """The dnb F1a regression, one provider over: `items_common` stopped
        wrapping the national leg in `except Exception`, so an escaping
        exception from the mapping would 500 the scan for every Italian ISBN
        instead of falling through.
        """
        respx.get("https://opac.sbn.it/opacmobilegw/search.json").mock(
            return_value=httpx.Response(
                200, text=_fixture("sbn_search_9788842092995.json")
            )
        )
        with patch("app.services.sbn._parse_record", side_effect=AttributeError("boom")):
            async with httpx.AsyncClient() as client:
                result = await sbn.lookup("9788842092995", client)

        assert result.outcome == "no_match"

    @respx.mock
    @pytest.mark.asyncio
    async def test_rate_limiter_invoked(self, monkeypatch):
        calls = []

        async def fake_rate_limit():
            calls.append(1)

        monkeypatch.setattr(sbn, "_rate_limit", fake_rate_limit)
        respx.get("https://opac.sbn.it/opacmobilegw/search.json").mock(
            return_value=httpx.Response(200, text=_fixture("sbn_search_nohit.json"))
        )
        async with httpx.AsyncClient() as client:
            await sbn.lookup("9788854519794", client)

        assert calls == [1]

    @respx.mock
    @pytest.mark.asyncio
    async def test_language_is_read_only_from_an_unambiguous_lingua_facet(self):
        """The `lingua` facet aggregates over every matched record, not over
        the one selected, so two values mean the language of *this* book is
        unknown. Built inline rather than committed: the real endpoint has not
        been observed returning a mixed facet.
        """
        import json

        payload = json.loads(_fixture("sbn_search_9788842092995.json"))
        for facet in payload["facetRecords"]:
            if facet["facetName"] == "lingua":
                facet["facetValues"].append(["francese", "fre", "1"])
        respx.get("https://opac.sbn.it/opacmobilegw/search.json").mock(
            return_value=httpx.Response(200, json=payload)
        )
        async with httpx.AsyncClient() as client:
            result = await sbn.lookup("9788842092995", client)

        assert result.outcome == "found"
        assert "language" not in result.payload
        # The rest of the mapping is unaffected.
        assert result.payload["title"] == "La quarta rivoluzione"
