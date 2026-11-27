"""Offline BN Poland API contract tests; capture provenance is in fixtures/bn_README.md."""

import asyncio
import copy
import gzip
import json
import unicodedata
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

import app.config
from app.services import bn

FIXTURES = Path(__file__).parent / "fixtures"
ISBN = "9788375780635"


def fixture(isbn=ISBN):
    return json.loads((FIXTURES / f"bn_{isbn}.json").read_text(encoding="utf-8"))


def field(tag, **subfields):
    return {tag: {"ind1": " ", "ind2": " ", "subfields": [{k: v} for k, v in subfields.items()]}}


def record(*fields):
    return {"deleted": False, "marc": {"fields": list(fields)}}


def minimal(isbn=ISBN):
    return record(field("020", a=isbn), field("245", a="Tytuł /"))


async def lookup(isbn=ISBN):
    async with httpx.AsyncClient() as client:
        return await bn.lookup(isbn, client)


@pytest.mark.parametrize(
    "isbn,expected",
    [
        (ISBN, {
            "title": "Ostatnie życzenie", "authors": "Andrzej Sapkowski",
            "publisher": "SuperNOWA", "publish_year": 2014, "page_count": 330,
            "language": "pl", "isbn10": "8375780634",
        }),
        ("9788365646156", {
            "title": "Venus in furs",
            "subtitle": "Wenus w futrze : z podręcznym słownikiem angielsko-polskim",
            "authors": "Leopold von Sacher-Masoch", "publisher": "Ze Słownikiem",
            "publish_year": 2016, "page_count": 276, "language": "en", "isbn10": "8365646153",
        }),
        ("9788386075652", {
            "title": "Wielka encyklopedia krzyżówkowicza", "subtitle": "40000 haseł",
            "authors": "Alicja Łochowska", "publisher": '"Kram"', "publish_year": 1995,
            "page_count": 835, "language": "pl", "isbn10": "8386075651",
        }),
    ],
)
@respx.mock
async def test_captured_metadata_and_documented_request(isbn, expected, monkeypatch):
    pace = AsyncMock()
    monkeypatch.setattr(bn.outbound, "acquire", pace)
    route = respx.get(bn.SEARCH_URL).respond(200, json=fixture(isbn))

    result = await lookup(isbn)

    assert result.found and result.provider == "bn" and result.status == 200
    assert result.payload == expected
    assert route.call_count == 1
    request = route.calls[0].request
    assert dict(request.url.params) == {
        "isbnIssn": f"{isbn} | {expected['isbn10']}", "boolean": "true", "limit": "5",
    }
    assert request.headers["Accept"] == "application/json"
    assert request.headers["Accept-Encoding"] == "identity"
    assert request.headers["User-Agent"].startswith("Shelf/")
    assert "Authorization" not in request.headers
    assert all(value == bn.TIMEOUT for value in request.extensions["timeout"].values())
    pace.assert_awaited_once_with("data.bn.org.pl")


@respx.mock
async def test_related_translations_before_exact_record_are_rejected():
    payload = fixture()
    payload["bibs"] = payload["bibs"][1:] + payload["bibs"][:1]
    respx.get(bn.SEARCH_URL).respond(200, json=payload)
    result = await lookup()
    assert result.payload["title"] == "Ostatnie życzenie"
    assert result.payload["language"] == "pl"


@respx.mock
async def test_actual_cancelled_original_isbn_hits_alone_are_no_match():
    payload = fixture()
    payload["bibs"] = payload["bibs"][1:]
    respx.get(bn.SEARCH_URL).respond(200, json=payload)
    result = await lookup()
    assert result.outcome == "no_match" and result.status == 200


@pytest.mark.parametrize("raw", [
    "", None, 9788375780635, b"9788375780635", "9788375780634", "8375780635",
    "1234567890128", "9788375780635 | 8386075651", '9788375780635"', "ISBN 9788375780635",
])
@respx.mock
async def test_invalid_query_isbn_costs_no_request_or_pacing(raw, monkeypatch):
    pace = AsyncMock()
    monkeypatch.setattr(bn.outbound, "acquire", pace)
    result = await lookup(raw)
    assert result.outcome == "no_match"
    assert not respx.calls
    pace.assert_not_awaited()


@pytest.mark.parametrize("raw", ["978-83-7578-063-5", "83-7578-063-4", " 978 83 7578 063 5 "])
@respx.mock
async def test_valid_input_forms_are_canonicalized(raw):
    route = respx.get(bn.SEARCH_URL).respond(200, json=fixture())
    assert (await lookup(raw)).found
    assert route.calls[0].request.url.params["isbnIssn"] == "9788375780635 | 8375780634"


@pytest.mark.parametrize("value,exact", [
    (ISBN, True), ("978-83-7578-063-5 (oprawa miękka)", True),
    ("83-7578-063-4 : zł 29", True), ("8375780634", True),
    ("9788375780634", False), ("8375780635", False),
    ("9788375780710", False), ("97883757806350", False),
    ("prefix 9788375780635", False), ("9788375780635 9", False),
    ("9788375780635suffix", False), (None, False), (ISBN + "\n9788386075652", False),
])
def test_only_valid_exact_020_a_identifies_edition(value, exact):
    assert bn._matches_isbn(minimal(value), ISBN) is exact


def test_response_checksum_is_validated_even_if_raw_digits_equal_query():
    assert not bn._matches_isbn(minimal("9788375780634"), "9788375780634")


@respx.mock
async def test_missing_020_a_cannot_be_rescued_by_flattened_identifiers():
    bib = minimal()
    bib["marc"]["fields"][0] = field("020", z=ISBN)
    bib["isbnIssn"] = ISBN
    respx.get(bn.SEARCH_URL).respond(200, json={"bibs": [bib]})
    assert (await lookup()).outcome == "no_match"


@pytest.mark.parametrize("deleted", [True, "false", None])
@respx.mock
async def test_deleted_or_unconfirmed_records_are_skipped(deleted):
    bib = minimal()
    bib["deleted"] = deleted
    respx.get(bn.SEARCH_URL).respond(200, json={"bibs": [bib]})
    assert (await lookup()).outcome == "no_match"


@respx.mock
async def test_no_title_and_malformed_candidates_do_not_hide_later_exact_hit():
    bibs = [None, [], {"deleted": False, "marc": []}, record(field("020", a=ISBN)), minimal()]
    respx.get(bn.SEARCH_URL).respond(200, json={"bibs": bibs})
    assert (await lookup()).payload["title"] == "Tytuł"


@respx.mock
async def test_parse_exception_in_one_candidate_still_allows_next(monkeypatch):
    original = bn._parse_record

    def parse(bib):
        if bib.get("broken"):
            raise ValueError("unexpected MARC shape")
        return original(bib)

    monkeypatch.setattr(bn, "_parse_record", parse)
    bad = {**minimal(), "broken": True}
    respx.get(bn.SEARCH_URL).respond(200, json={"bibs": [bad, minimal()]})
    assert (await lookup()).found


def test_unicode_normalization_relators_publication_and_parts():
    bib = record(
        field("245", a="\x98Żółta\x9c książka :", b="pamięć /", n="T. 2,", p="Ścieżki /"),
        field("100", a="Kowalski, Józef", d="(1948- )", e="Autor"),
        field("700", a="Kowalski, Józef", **{"4": "aut"}),
        field("700", a="Nowak, Łucja", e="Autorka"),
        field("700", a="Tłumacz, Jan", e="Tłumaczenie"),
        field("700", a="Redaktor, Jan", **{"4": "edt"}),
        field("700", a="Dawny, Jan", t="Inny utwór", **{"4": "aut"}),
        field("264", b="Wrong copyright publisher", c="2025"),
        field("260", b="Legacy", c="2001"),
        field("300", a="XIV, 399 stron ;"), field("041", a="pol", h="eng"),
        field("490", a="Not a work series"),
    )
    publication = field("264", b="Żółć Sp. z o.o.,", c="[2020].")
    publication["264"]["ind2"] = "1"
    bib["marc"]["fields"].append(publication)
    # Construct a synthetic decomposed response to pin NFC for every string.
    bib = json.loads(unicodedata.normalize("NFD", json.dumps(bib, ensure_ascii=False)))
    assert bn._parse_record(bib) == {
        "title": "Żółta książka. T. 2. Ścieżki", "subtitle": "pamięć",
        "authors": "Józef Kowalski, Łucja Nowak", "publisher": "Żółć Sp. z o.o.",
        "publish_year": 2020, "page_count": 399, "language": "pl",
    }


def test_distinct_coauthors_survive_exact_normalized_deduplication_in_provider_order():
    bib = record(
        field("245", a="Wspólna książka /"),
        field("100", a="Kowalski, Jan Adam", e="Autor"),
        field("700", a="Kowalski, Jan Marek", **{"4": "aut"}),
        field("700", a="Kowalski, Jan Adam", e="Autor"),
        field("700", a=" Kowalski,  Jan   Marek ", e="Autor"),
        field("700", a="Kowalski, Jan", e="Autor"),
        field("700", a="Wiśniewski, Łukasz", e="Autor"),
        field("700", a=unicodedata.normalize("NFD", "Wiśniewski, Łukasz"), e="Autor"),
        field("700", a="Kowalski, Jan Piotr", e="Tłumaczenie"),
        field("700", a="Kowalski, Jan Paweł", t="Related work", **{"4": "aut"}),
    )
    assert bn._parse_record(bib)["authors"] == (
        "Jan Adam Kowalski, Jan Marek Kowalski, Jan Kowalski, Łukasz Wiśniewski"
    )


@pytest.mark.parametrize("extent,expected", [
    ("330, [6] stron ;", 330), ("276, [1] strona ;", 276), ("285, [3] strony ;", 285),
    ("835, [1] s. ;", 835), ("XIV, 399 stron ;", 399), ("123 pages ;", 123),
    ("2 woluminy ;", None), ("1 płyta CD (12 godz.)", None), ("XII s.", None),
    ("0 stron", None), ("1 mapa", None),
])
def test_extent_never_turns_volumes_or_audio_duration_into_pages(extent, expected):
    bib = minimal()
    bib["marc"]["fields"].append(field("300", a=extent))
    assert bn._parse_record(bib).get("page_count") == expected


@pytest.mark.parametrize("code,expected", [("poleng", "pl"), ("eng", "en"), ("ger", "de"), ("deu", "de")])
def test_record_language_not_original_or_flattened_language(code, expected):
    bib = minimal()
    bib.update(language="polski", languageOfOriginal="niemiecki")
    bib["marc"]["fields"].append(field("041", a=code, h="fre"))
    assert bn._parse_record(bib)["language"] == expected


def test_no_language_is_inferred_from_polish_isbn_or_display_language():
    bib = minimal()
    bib["language"] = "polski"
    bib["marc"]["fields"].extend([field("041", a="und", h="pol"), {"008": "short"}])
    assert "language" not in bn._parse_record(bib)


@pytest.mark.parametrize("payload", [None, [], {}, {"bibs": {}}, {"bibs": [None, 7, "invalid"]}])
@respx.mock
async def test_malformed_payload_shapes_are_no_match(payload):
    respx.get(bn.SEARCH_URL).respond(200, content=json.dumps(payload))
    result = await lookup()
    assert result.outcome == "no_match" and result.status == 200


@pytest.mark.parametrize("body", [b"<html>proxy login</html>", b"", b'{"bibs": [', b"\xff\xfeinvalid"])
@respx.mock
async def test_malformed_json_or_encoding_is_no_match(body):
    respx.get(bn.SEARCH_URL).respond(200, content=body)
    assert (await lookup()).outcome == "no_match"


@pytest.mark.parametrize("status,outcome", [(429, "rate_limited"), (403, "no_match"), (500, "transport_failed"), (503, "transport_failed")])
@respx.mock
async def test_http_outcomes_do_not_retry_or_wait_for_retry_after(status, outcome):
    route = respx.get(bn.SEARCH_URL).respond(status, headers={"Retry-After": "3600"})
    result = await lookup()
    assert (result.provider, result.outcome, result.status) == ("bn", outcome, status)
    assert route.call_count == 1


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout, httpx.ReadError])
@respx.mock
async def test_transport_failure_never_retries(error):
    route = respx.get(bn.SEARCH_URL).mock(side_effect=error("offline"))
    result = await lookup()
    assert (result.outcome, result.status) == ("transport_failed", None)
    assert route.call_count == 1


@respx.mock
async def test_redirect_is_not_followed_even_if_caller_enables_redirects():
    route = respx.get(bn.SEARCH_URL).respond(302, headers={"Location": "https://example.com/other"})
    redirected = respx.get("https://example.com/other").respond(200, json={"bibs": [minimal()]})
    async with httpx.AsyncClient(follow_redirects=True) as client:
        result = await bn.lookup(ISBN, client)
    assert result.outcome == "no_match" and result.status == 302
    assert route.call_count == 1 and not redirected.called


@respx.mock
async def test_result_count_and_pagination_are_bounded_even_if_server_ignores_limit():
    wrong = minimal("9788365646156")
    payload = {"bibs": [copy.deepcopy(wrong) for _ in range(5)] + [minimal()],
               "nextPage": "https://example.com/untrusted-page"}
    route = respx.get(bn.SEARCH_URL).respond(200, json=payload)
    assert (await lookup()).outcome == "no_match"
    assert route.call_count == 1 and len(respx.calls) == 1


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, delay=0):
        self.chunks = chunks
        self.delay = delay
        self.read = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.read += 1
            yield chunk

    async def aclose(self):
        self.closed = True


async def test_compressed_small_wire_huge_decoded_response_is_rejected_without_reading():
    # A small compressed chunk would otherwise be expanded by HTTPX before
    # aiter_bytes yields its first bounded-size chunk to the adapter.
    compressed = gzip.compress(b" " * (bn.MAX_RESPONSE_BYTES * 16))
    assert len(compressed) < 16384
    stream = ChunkStream([compressed])
    response = httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=stream)
    requests = []

    def handle(request):
        requests.append(request)
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await bn.lookup(ISBN, client)
        assert stream.closed and response.is_closed

    assert (result.provider, result.outcome, result.status) == ("bn", "no_match", 200)
    assert stream.read == 0
    assert len(requests) == 1
    assert requests[0].headers["Accept-Encoding"] == "identity"


@pytest.mark.parametrize("headers", [
    {"Content-Encoding": "gzip"}, {"Content-Encoding": "deflate"},
    {"Content-Encoding": "br"}, {"Content-Encoding": "zstd"},
    {"Content-Encoding": "unknown"}, {"Content-Encoding": "identity, gzip"},
    {"Content-Encoding": "gzip, identity"}, {"Content-Encoding": ""},
    [("Content-Encoding", "identity"), ("Content-Encoding", "gzip")],
])
async def test_any_nonidentity_encoding_is_rejected_before_body_consumption(headers):
    stream = ChunkStream([b"must not be read or decoded"])
    response = httpx.Response(200, headers=headers, stream=stream)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
        result = await bn.lookup(ISBN, client)
        assert stream.closed and response.is_closed
    assert (result.outcome, result.status) == ("no_match", 200)
    assert stream.read == 0


@pytest.mark.parametrize("headers", [{}, {"Content-Encoding": "identity"}, {"Content-Encoding": " Identity "}])
@pytest.mark.parametrize("preloaded", [False, True])
async def test_identity_or_missing_encoding_succeeds_with_real_mock_transport(headers, preloaded):
    data = json.dumps({"bibs": [minimal()]}).encode()
    stream = ChunkStream([data[:32], data[32:]])
    response = (httpx.Response(200, headers=headers, content=data) if preloaded
                else httpx.Response(200, headers=headers, stream=stream))
    requests = []

    def handle(request):
        requests.append(request)
        return response

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), headers={"Accept-Encoding": "gzip"},
    ) as client:
        result = await bn.lookup(ISBN, client)
        assert response.is_closed
        if not preloaded:
            assert stream.read == 2 and stream.closed
    assert result.found and result.payload["title"] == "Tytuł"
    assert len(requests) == 1
    assert requests[0].headers["Accept-Encoding"] == "identity"


async def test_preloaded_mock_transport_response_is_still_size_bounded(monkeypatch):
    data = json.dumps({"bibs": [minimal()]}).encode()
    monkeypatch.setattr(bn, "MAX_RESPONSE_BYTES", len(data) - 1)
    response = httpx.Response(200, content=data)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
        result = await bn.lookup(ISBN, client)
    assert result.outcome == "no_match" and response.is_closed


@respx.mock
async def test_decoded_response_ceiling_aborts_stream_and_closes_it(monkeypatch):
    monkeypatch.setattr(bn, "MAX_RESPONSE_BYTES", 65536)
    stream = ChunkStream([b" " * 65536, b" " * 65536, b"must not be read"])
    respx.get(bn.SEARCH_URL).mock(return_value=httpx.Response(200, stream=stream))
    assert (await lookup()).outcome == "no_match"
    assert stream.read == 2 and stream.closed


@respx.mock
async def test_exact_byte_limit_is_accepted(monkeypatch):
    data = json.dumps({"bibs": [minimal()]}).encode()
    monkeypatch.setattr(bn, "MAX_RESPONSE_BYTES", len(data))
    respx.get(bn.SEARCH_URL).respond(200, content=data)
    assert (await lookup()).found


@respx.mock
async def test_whole_request_deadline_covers_slow_stream_and_closes_it(monkeypatch):
    monkeypatch.setattr(bn, "TIMEOUT", 0.01)
    stream = ChunkStream([b"not delivered"], delay=1)
    respx.get(bn.SEARCH_URL).mock(return_value=httpx.Response(200, stream=stream))
    result = await lookup()
    assert result.outcome == "transport_failed"
    assert stream.read == 0 and stream.closed


@respx.mock
async def test_cancellation_is_not_swallowed():
    async def cancel(request):
        raise asyncio.CancelledError()

    respx.get(bn.SEARCH_URL).mock(side_effect=cancel)
    with pytest.raises(asyncio.CancelledError):
        await lookup()


@respx.mock
async def test_bn_delegates_to_configured_shared_host_interval(monkeypatch):
    # The shared config entry is owned by the orchestrator, not this adapter.
    # Prove the adapter uses it rather than a private, unresettable limiter.
    monkeypatch.setitem(app.config.HOST_RATE_LIMITS, bn.HOST, 1.0)
    ticks = iter((10.0, 10.0, 10.25, 10.25))
    monkeypatch.setattr(bn.outbound, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    sleep = AsyncMock()
    monkeypatch.setattr(bn.outbound, "_sleep", sleep)
    route = respx.get(bn.SEARCH_URL).respond(200, json={"bibs": []})
    await lookup()
    await lookup()
    assert route.call_count == 2
    sleep.assert_awaited_once_with(0.75)
