"""The ISBN scan path can now reach every card arm it renders.

Two of these are reachability pins, not behaviour pins. `scan_result.html` has
carried a `rejected` arm since issue #42, but only the UPC branches could
produce that state: the book path rebuilt its answer by hand as
`"quota" if lookup_rate_limited else None`, so a rejected Google Books or
Hardcover key was filed as "no such book" and the copy sat unreachable. The
third asserts the literal that made it so cannot come back.
"""

import re
from pathlib import Path

import httpx
import pytest

from app.services import outbound

ISBN = "9780306406157"
ROUTERS = Path(__file__).resolve().parents[1] / "app/routers"


def test_no_router_rebuilds_the_enrichment_state_by_hand():
    """`"quota" if …` is the shape this whole plan exists to delete.

    A guard that greps raw source (GOTCHAS G53), and deliberately narrow: it
    forbids one exact string rather than trying to recognise the pattern, so
    its false-positive surface is that string and nothing else. Every branch
    that needs an enrichment state must project it from a `ProviderResult`
    through `scan_outcome`, which is what makes the state reachable from
    branches that hold no booleans.
    """
    offenders = [
        f"{path.name}:{n}"
        for path in sorted(ROUTERS.glob("*.py"))
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if '"quota" if' in line
    ]
    assert not offenders, f"hand-built enrich_status: {offenders}"


class TestARejectedKeyIsVisibleOnTheBookPath:
    """Stubbed at `outbound.fetch`, not at the clients.

    The clients are what this plan re-typed, so stubbing them would pin the
    stubs. Every leg answers from one fake transport instead: Google Books
    rejects the key with a 400 (G64), Open Library and DNB miss, and no
    Hardcover token is configured.
    """

    @pytest.fixture
    def _google_key(self, monkeypatch):
        # Through the env override, as every other credential test here does:
        # `google_books_api_key` is a SENSITIVE_KEY, so a plaintext row would
        # fail decryption on read.
        monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "a-revoked-key")

    @pytest.fixture
    def stub_transport(self, monkeypatch):
        def _install(google_status, other_status=404):
            async def _fetch(client, method, url, **kw):
                if "googleapis.com" in url:
                    return httpx.Response(google_status, json={})
                return httpx.Response(other_status, json={})

            async def _get(url, **kw):
                return httpx.Response(other_status, json={})

            monkeypatch.setattr(outbound, "fetch", _fetch)
            monkeypatch.setattr(
                "app.services.openlibrary.httpx.AsyncClient.get", lambda self, *a, **k: _get(*a, **k)
            )
        return _install

    def test_a_rejected_google_books_key_renders_the_rejected_arm(
        self, editor_client, _google_key, stub_transport, monkeypatch
    ):
        """The card the ISBN path could not reach before this plan.

        Google Books answers an invalid key with **400**, not 401 (G64), which
        is why it was filed as "no such book" — the status a cascade reads as
        a plain miss.
        """
        stub_transport(google_status=400)
        monkeypatch.setattr(
            "app.routers.items_common._fetch_preview_cover",
            _none_coro(),
        )

        resp = editor_client.post(
            "/api/scan", data={"isbn": ISBN, "media_type": "book", "mode": "add"}
        )

        assert resp.status_code == 200
        assert "rejected the configured key" in resp.text
        assert "Google Books" in resp.text
        assert "rate-limiting us" not in resp.text

    def test_a_rejection_outranks_a_quota_across_two_legs(
        self, editor_client, _google_key, stub_transport, monkeypatch
    ):
        """One leg 429s, another rejects the key — the actionable one wins.

        `provider_result.combine` owns that order now; before this plan the
        `not_found` branch could only ever say "quota", whatever else it saw.
        """
        stub_transport(google_status=400, other_status=429)
        monkeypatch.setattr(
            "app.routers.items_common._fetch_preview_cover",
            _none_coro(),
        )

        resp = editor_client.post(
            "/api/scan", data={"isbn": ISBN, "media_type": "book", "mode": "add"}
        )

        assert resp.status_code == 200
        assert "rejected the configured key" in resp.text
        assert "rate-limiting us" not in resp.text

    def test_a_plain_miss_still_says_nothing_extra(
        self, editor_client, stub_transport, monkeypatch
    ):
        """The control. Without it the two pins above would prove only that a
        notice renders, not that it renders *for this reason*."""
        stub_transport(google_status=404)
        monkeypatch.setattr(
            "app.routers.items_common._fetch_preview_cover",
            _none_coro(),
        )

        resp = editor_client.post(
            "/api/scan", data={"isbn": ISBN, "media_type": "book", "mode": "add"}
        )

        assert resp.status_code == 200
        assert "Not found" in resp.text
        assert "rejected the configured key" not in resp.text
        assert "match for this barcode" not in resp.text


def _none_coro():
    async def _f(*a, **kw):
        return None
    return _f
