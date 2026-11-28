"""The outermost header-rewrite stage (#165). Assertions by equality (G102)."""
import asyncio

import pytest

from app.base_path import BasePathHeadersMiddleware, rewrite_location, rewrite_own_absolute

BASE = "/ingress/test"


@pytest.mark.parametrize("value,expected", [
    ("/item/3", "/ingress/test/item/3"),
    ("/item/3/edit?error=x", "/ingress/test/item/3/edit?error=x"),
    ("/", "/ingress/test/"),
    ("//evil.example", "//evil.example"),
    ("/\\evil.example", "/\\evil.example"),
    ("https://x/y", "https://x/y"),
    ("", ""),
    ("/ingress/test", "/ingress/test"),
    ("/ingress/test/browse", "/ingress/test/browse"),
    ("/ingress/test?x=1", "/ingress/test?x=1"),
    ("/ingress/test#top", "/ingress/test#top"),
    ("/ingress/testing", "/ingress/test/ingress/testing"),
    ('{"path": "/x"}', '{"path": "/x"}'),
])
def test_rewrite_location(value, expected):
    assert rewrite_location(value, BASE) == expected


def test_no_base_is_identity():
    assert rewrite_location("/item/3", "") == "/item/3"


@pytest.mark.parametrize("value,expected", [
    # Starlette's trailing-slash redirect, built from the stripped path (F4).
    ("http://shelf.local:8080/browse", "/ingress/test/browse"),
    ("https://shelf.local:8080/browse?q=a#x", "/ingress/test/browse?q=a#x"),
    ("http://shelf.local:8080", "/ingress/test/"),
    ("http://SHELF.local:8080/x", "/ingress/test/x"),
    ("http://shelf.local:8080/ingress/test/x", "/ingress/test/x"),
    ("https://other.example/browse", "https://other.example/browse"),
    ("ftp://shelf.local:8080/x", "ftp://shelf.local:8080/x"),
    ("/browse", "/browse"),
])
def test_rewrite_own_absolute(value, expected):
    assert rewrite_own_absolute(value, BASE, "shelf.local:8080") == expected


def _run(headers, chunks=(b"",), host=b"shelf.local:8080"):
    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 303, "headers": headers})
        for i, chunk in enumerate(chunks):
            await send({"type": "http.response.body", "body": chunk, "more_body": i < len(chunks) - 1})

    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    mw = BasePathHeadersMiddleware(inner, base_path=BASE)
    asyncio.run(mw({"type": "http", "path": "/", "headers": [(b"host", host)]}, receive, send))
    return sent


def _headers(sent):
    return {k.decode(): v.decode() for k, v in sent[0]["headers"]}


def test_location_and_hx_redirect_are_prefixed():
    h = _headers(_run([(b"location", b"/item/3"), (b"HX-Redirect", b"/login"), (b"x-other", b"/keep")]))
    assert h["location"] == "/ingress/test/item/3"
    assert h["HX-Redirect"] == "/ingress/test/login"
    assert h["x-other"] == "/keep"


def test_hx_location_path_is_prefixed():
    assert _headers(_run([(b"hx-location", b"/browse")]))["hx-location"] == "/ingress/test/browse"


def test_own_absolute_location_is_prefixed_and_foreign_left_alone():
    h = _headers(_run([(b"location", b"http://shelf.local:8080/browse")]))
    assert h["location"] == "/ingress/test/browse"
    h = _headers(_run([(b"location", b"https://other.example/x")]))
    assert h["location"] == "https://other.example/x"


def test_refresh_url_is_prefixed():
    h = _headers(_run([(b"refresh", b"0;url=/browse?q=a"), (b"cache-control", b"no-store")]))
    assert h["refresh"] == "0;url=/ingress/test/browse?q=a"
    assert h["cache-control"] == "no-store"


def test_refresh_without_url_is_unchanged():
    assert _headers(_run([(b"refresh", b"5")]))["refresh"] == "5"


def test_hx_location_json_is_unchanged():
    value = b'{"path": "/x", "target": "#main"}'
    assert _headers(_run([(b"hx-location", value)]))["hx-location"] == value.decode()


def test_body_chunks_pass_through_one_by_one():
    sent = _run([(b"content-type", b"text/event-stream")], chunks=(b"data: 1\n\n", b"data: 2\n\n", b""))
    bodies = [m["body"] for m in sent[1:]]
    assert bodies == [b"data: 1\n\n", b"data: 2\n\n", b""]


def test_non_http_scope_passes_through():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    mw = BasePathHeadersMiddleware(inner, base_path=BASE)
    asyncio.run(mw({"type": "lifespan"}, None, None))
    assert seen == ["lifespan"]


def test_stage_is_not_installed_at_the_default():
    import importlib
    main = importlib.import_module("app.main")
    app = main.app
    if app.middleware_stack is None:
        app.middleware_stack = app.build_middleware_stack()
    node = app.middleware_stack
    found = []
    while node is not None:
        found.append(type(node).__name__)
        node = getattr(node, "app", None)
    assert "BasePathHeadersMiddleware" not in found
    assert "SecurityHeadersMiddleware" in found
