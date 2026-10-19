"""The book picker must distinguish an ISBN edition from a title match."""

import json
import re
from pathlib import Path

import httpx
import pytest

from app.services import book_cover_search, provider_result


def _response(status, url, data):
    return httpx.Response(status, json=data, request=httpx.Request("GET", url))


@pytest.mark.asyncio
async def test_exact_isbn_wins_without_title_fallback(monkeypatch):
    calls = []

    async def fetch(client, method, url, **kwargs):
        calls.append((url, kwargs.get("params")))
        if "googleapis" in url:
            return _response(200, url, {"items": [{"volumeInfo": {
                "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9780441172719"}],
                "imageLinks": {"thumbnail": "http://books.google.com/cover.jpg"},
            }}]})
        return _response(404, url, {})

    monkeypatch.setattr(book_cover_search.outbound, "fetch", fetch)
    result = await book_cover_search.search("Dune", "Frank Herbert", "9780441172719", object())

    assert result.found
    assert result.payload[0]["match"] == "isbn"
    assert result.payload[0]["url"] == "https://books.google.com/cover.jpg"
    assert len(calls) == 2
    assert calls[0][1]["q"] == "isbn:9780441172719"
    assert calls[1][0].endswith("/isbn/9780441172719.json")


@pytest.mark.asyncio
async def test_wrong_google_edition_is_not_labelled_exact(monkeypatch):
    async def fetch(client, method, url, **kwargs):
        if "googleapis" in url:
            return _response(200, url, {"items": [{"volumeInfo": {
                "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9780141439518"}],
                "imageLinks": {"thumbnail": "https://books.google.com/wrong.jpg"},
            }}]})
        return _response(404, url, {})

    monkeypatch.setattr(book_cover_search.outbound, "fetch", fetch)
    result = await book_cover_search.search("Dune", None, "9780441172719", object())

    assert result.found
    assert all(candidate["match"] == "title" for candidate in result.payload)
    assert all("Check edition" in candidate["source"] for candidate in result.payload)


@pytest.mark.asyncio
async def test_open_library_isbn_edition_uses_its_cover_id(monkeypatch):
    followed_redirects = []

    async def fetch(client, method, url, **kwargs):
        if "googleapis" in url:
            return _response(404, url, {})
        followed_redirects.append(kwargs.get("follow_redirects"))
        return _response(200, url, {"isbn_13": ["9780441172719"], "covers": [1234]})

    monkeypatch.setattr(book_cover_search.outbound, "fetch", fetch)
    result = await book_cover_search.search("Dune", None, "9780441172719", object())

    assert result.found
    assert followed_redirects == [True]
    assert result.payload == [{
        "url": "https://covers.openlibrary.org/b/id/1234-L.jpg",
        "thumbnail": "https://covers.openlibrary.org/b/id/1234-M.jpg",
        "source": "Open Library · ISBN match", "match": "isbn",
    }]


@pytest.mark.asyncio
async def test_quota_is_not_reported_as_no_cover(monkeypatch):
    async def fetch(client, method, url, **kwargs):
        if "googleapis" in url:
            return _response(429, url, {})
        return _response(404, url, {})

    monkeypatch.setattr(book_cover_search.outbound, "fetch", fetch)
    result = await book_cover_search.search("Dune", None, None, object())

    assert result.outcome == "rate_limited"
    assert result.provider == "google"


@pytest.mark.asyncio
async def test_isbn_quota_skips_second_google_query(monkeypatch):
    google_queries = []

    async def fetch(client, method, url, **kwargs):
        if "googleapis" in url:
            google_queries.append(kwargs["params"]["q"])
            return _response(429, url, {})
        return _response(404, url, {})

    monkeypatch.setattr(book_cover_search.outbound, "fetch", fetch)
    result = await book_cover_search.search("Dune", None, "9780441172719", object())

    assert result.outcome == "rate_limited"
    assert google_queries == ["isbn:9780441172719"]


@pytest.mark.asyncio
async def test_one_provider_outage_does_not_hide_other_candidates(monkeypatch):
    async def google(*args, **kwargs):
        return provider_result.transport_failed("google")

    async def openlibrary(*args, **kwargs):
        return provider_result.found("openlibrary", [{
            "url": "https://covers.openlibrary.org/b/id/12-L.jpg",
            "thumbnail": "https://covers.openlibrary.org/b/id/12-M.jpg",
            "source": "Open Library · Check edition", "match": "title",
        }])

    monkeypatch.setattr(book_cover_search, "google_candidates", google)
    monkeypatch.setattr(book_cover_search, "openlibrary_candidates", openlibrary)
    result = await book_cover_search.search("Dune", None, None, object())

    assert result.found
    assert len(result.payload) == 1


# --- Sibling editions on the Open Library ISBN path (plan cover-editions) ---

_FIXTURES = Path(__file__).parent / "fixtures"
_ISBN = "9780441172719"
_EDITIONS_PATH = "/works/OL893414W/editions.json"
_OL_TILE = re.compile(r"^https://covers\.openlibrary\.org/b/id/\d+-[LM]\.jpg$")


def _fixture(name):
    return json.loads((_FIXTURES / name).read_text())


def _ol_stub(monkeypatch, *, isbn_doc=None, editions=None, calls=None):
    """Google misses; OL answers the captured ISBN doc and editions list.

    `editions` is a status code, an exception instance, or a JSON body.
    """
    isbn_doc = _fixture("ol_isbn_9780441172719.json") if isbn_doc is None else isbn_doc
    editions = _fixture("ol_editions_OL893414W.json") if editions is None else editions
    calls = [] if calls is None else calls

    async def fetch(client, method, url, **kwargs):
        calls.append(url)
        if "googleapis" in url:
            return _response(404, url, {})
        if url.endswith(f"/isbn/{_ISBN}.json"):
            return _response(200, url, isbn_doc)
        if url.endswith(_EDITIONS_PATH):
            if isinstance(editions, Exception):
                raise editions
            if isinstance(editions, int):
                return _response(editions, url, {})
            return _response(200, url, editions)
        return _response(404, url, {})

    monkeypatch.setattr(book_cover_search.outbound, "fetch", fetch)
    return calls


def _expected_sibling_ids(exclude):
    ids = []
    for entry in _fixture("ol_editions_OL893414W.json")["entries"]:
        covers = entry.get("covers") or []
        cid = covers[0] if covers else None
        if isinstance(cid, int) and cid > 0 and cid != exclude and cid not in ids:
            ids.append(cid)
    return ids[:book_cover_search.SIBLINGS_MAX]


def _tile_id(candidate):
    return int(re.search(r"/b/id/(\d+)-L\.jpg$", candidate["url"]).group(1))


@pytest.mark.asyncio
async def test_exact_tile_leads_then_siblings_in_fixture_order(monkeypatch):
    exact_id = _fixture("ol_isbn_9780441172719.json")["covers"][0]
    _ol_stub(monkeypatch)

    result = await book_cover_search.search("Dune", None, _ISBN, object())

    assert result.found
    assert result.payload[0]["match"] == "isbn"
    assert _tile_id(result.payload[0]) == exact_id
    siblings = result.payload[1:]
    assert [_tile_id(c) for c in siblings] == _expected_sibling_ids(exact_id)
    assert [_tile_id(c) for c in result.payload].count(exact_id) == 1
    assert all(c["match"] == "edition" for c in siblings)
    assert all(c["source"] == "Open Library · Check edition" for c in siblings)
    for candidate in result.payload:
        assert _OL_TILE.match(candidate["url"])
        assert _OL_TILE.match(candidate["thumbnail"])


@pytest.mark.asyncio
async def test_sibling_labels_degrade_without_languages(monkeypatch):
    _ol_stub(monkeypatch)
    by_id = {}
    for entry in _fixture("ol_editions_OL893414W.json")["entries"]:
        covers = entry.get("covers") or []
        if covers and isinstance(covers[0], int) and covers[0] > 0:
            by_id.setdefault(covers[0], entry)

    result = await book_cover_search.search("Dune", None, _ISBN, object())

    siblings = result.payload[1:]
    without = [c for c in siblings if not by_id[_tile_id(c)].get("languages")]
    with_lang = [c for c in siblings if by_id[_tile_id(c)].get("languages")]
    assert without and with_lang
    for candidate in without:
        assert not candidate["label"].endswith("·")
        assert not candidate["label"].endswith(" ")
        assert candidate["label"].count(" · ") <= 1
    for candidate in with_lang:
        code = by_id[_tile_id(candidate)]["languages"][0]["key"].rsplit("/", 1)[-1]
        assert candidate["label"].endswith(f" · {code}") or candidate["label"] == code


def test_an_entry_with_nothing_to_say_gets_no_label():
    assert book_cover_search._edition_label({"covers": [1]}) == ""
    assert book_cover_search._edition_label(
        {"publish_date": "Jun 14, 1965", "publishers": ["Ace"],
         "languages": [{"key": "/languages/eng"}]}
    ) == "1965 · Ace · eng"


@pytest.mark.asyncio
async def test_a_labelless_sibling_carries_no_label_key(monkeypatch):
    _ol_stub(monkeypatch, editions={"entries": [{"covers": [77]}, {"covers": [-1]}]})

    result = await book_cover_search.search("Dune", None, _ISBN, object())

    assert result.payload[1] == {
        "url": "https://covers.openlibrary.org/b/id/77-L.jpg",
        "thumbnail": "https://covers.openlibrary.org/b/id/77-M.jpg",
        "source": "Open Library · Check edition", "match": "edition",
    }
    assert len(result.payload) == 2  # -1 is OL's "no scan", never a tile


@pytest.mark.asyncio
@pytest.mark.parametrize("editions", [
    404, 503, httpx.ReadTimeout("slow"), {"entries": "x"},
], ids=["404", "503", "timeout", "malformed"])
async def test_a_failed_editions_call_leaves_the_exact_tile_alone(monkeypatch, editions):
    exact_id = _fixture("ol_isbn_9780441172719.json")["covers"][0]
    calls = _ol_stub(monkeypatch, editions=editions)

    result = await book_cover_search.search("Dune", None, _ISBN, object())

    assert any(url.endswith(_EDITIONS_PATH) for url in calls)
    assert result.outcome == "found"
    assert result.payload == [{
        "url": f"https://covers.openlibrary.org/b/id/{exact_id}-L.jpg",
        "thumbnail": f"https://covers.openlibrary.org/b/id/{exact_id}-M.jpg",
        "source": "Open Library · ISBN match", "match": "isbn",
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize("works", [
    [{"key": "/works/../x"}], [{"key": "/works/OL1W/../../admin"}], ["/works/OL893414W"], None,
], ids=["traversal", "suffix", "not-a-dict", "missing"])
async def test_a_bad_work_key_makes_no_editions_request(monkeypatch, works):
    doc = _fixture("ol_isbn_9780441172719.json")
    if works is None:
        doc.pop("works")
    else:
        doc["works"] = works
    calls = _ol_stub(monkeypatch, isbn_doc=doc)

    result = await book_cover_search.search("Dune", None, _ISBN, object())

    assert result.found
    assert not any("/works/" in url for url in calls)
    assert len(result.payload) == 1


@pytest.mark.asyncio
async def test_a_coverless_exact_edition_offers_its_siblings_not_title_guesses(monkeypatch):
    doc = _fixture("ol_isbn_9780441172719.json")
    doc["covers"] = []
    calls = _ol_stub(monkeypatch, isbn_doc=doc)

    result = await book_cover_search.search("Dune", None, _ISBN, object())

    assert result.outcome == "found"
    assert [_tile_id(c) for c in result.payload] == _expected_sibling_ids(None)
    assert all(c["match"] == "edition" for c in result.payload)
    assert not any(url.endswith("/search.json") for url in calls)
    assert sum("googleapis" in url for url in calls) == 1  # the ISBN query only


@pytest.mark.asyncio
@pytest.mark.parametrize("covers", [[-1], ["1234"], [0]])
async def test_a_sentinel_or_string_cover_id_makes_no_exact_tile(monkeypatch, covers):
    """codex-M2: Open Library writes `[-1]` for "no scan"; a string id would
    build a broken URL. Both read as no exact cover, and the siblings still come."""
    doc = _fixture("ol_isbn_9780441172719.json")
    doc["covers"] = covers
    _ol_stub(monkeypatch, isbn_doc=doc)

    result = await book_cover_search.search("Dune", None, _ISBN, object())

    assert result.found
    assert all(c["match"] == "edition" for c in result.payload)
    assert [_tile_id(c) for c in result.payload] == _expected_sibling_ids(None)
    assert not any("/-1-" in c["url"] or "/1234-" in c["url"] for c in result.payload)
