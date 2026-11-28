"""Prefix redirect headers with the URL sub-path Shelf is served under (#165).

A proxy that strips a prefix (Home Assistant ingress, a shared nginx host)
hands Shelf prefix-free request paths, so routing needs no change. Every URL
Shelf *emits* must carry the prefix, though, and response headers are the one
channel templates and scripts cannot reach: `Location` on every redirect,
htmx's `HX-Redirect` / `HX-Location`, and the `url=` of the #149 `Refresh`
bounce. This stage rewrites them in one place, outermost, so a route written
tomorrow with a bare `RedirectResponse("/x")` is still right.

Pure ASGI rather than `BaseHTTPMiddleware`: body messages pass through
untouched, so an SSE stream is never buffered, and the request body is never
read (G9). It imports nothing from `app` and is installed only when a base
path is set.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_REWRITTEN = (b"location", b"hx-redirect", b"hx-location")
_REFRESH_URL = re.compile(r"(url=)(.*)$", re.IGNORECASE | re.DOTALL)


def rewrite_location(value: str, base: str) -> str:
    """Prepend `base` to a root-relative URL; leave everything else alone.

    Left alone: empty, protocol-relative (`//x`, `/\\x`), anything with a
    scheme or not starting with `/` (JSON `HX-Location` included), and a value
    that already carries the prefix. Never decodes or normalises the value.
    """
    if not base or not value.startswith("/"):
        return value
    if value[1:2] in ("/", "\\"):
        return value
    if value == base or value.startswith((base + "/", base + "?", base + "#")):
        return value
    return base + value


def rewrite_own_absolute(value: str, base: str, host: str) -> str:
    """Prefix an absolute URL that points back at this host.

    Starlette's trailing-slash redirect is absolute and built from the
    stripped path (`http://host/browse`), so it leaves the prefix (diff review
    F4). When the URL's host is the request's own `Host`, it is Shelf's URL:
    it becomes the prefixed root-relative path, which the browser resolves
    against its own origin. Any other host is left alone.
    """
    if not base or not host or "://" not in value[:10]:
        return value
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or parts.netloc.lower() != host.lower():
        return value
    return rewrite_location(urlunsplit(("", "", parts.path or "/", parts.query, parts.fragment)), base)


def _rewrite_refresh(value: str, base: str) -> str:
    m = _REFRESH_URL.search(value)
    if not m:
        return value
    return value[: m.start(2)] + rewrite_location(m.group(2), base)


class BasePathHeadersMiddleware:
    def __init__(self, app, base_path: str):
        self.app = app
        self.base = base_path

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.base:
            await self.app(scope, receive, send)
            return
        base = self.base
        host = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"host"), "")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = []
                for name, raw in message.get("headers", []):
                    lname = name.lower()
                    if lname in _REWRITTEN:
                        value = rewrite_own_absolute(raw.decode("latin-1"), base, host)
                        raw = rewrite_location(value, base).encode("latin-1")
                    elif lname == b"refresh":
                        raw = _rewrite_refresh(raw.decode("latin-1"), base).encode("latin-1")
                    headers.append((name, raw))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)
