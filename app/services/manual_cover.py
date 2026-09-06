"""Safe downloader for user-supplied cover image URLs.

Provider artwork already uses Shelf's static cover-domain allowlist. A manually
pasted URL intentionally needs to work with arbitrary public image hosts, so it
has a different boundary: public HTTPS only, with DNS resolution pinned to the
address that was validated before the connection is opened. Every redirect is
resolved and validated again.
"""

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

import httpx

from app.config import COVERS_DIR, HTTP_TIMEOUT
from app.services import covers, outbound

logger = logging.getLogger(__name__)

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5


class _PinnedIPTransport(httpx.AsyncBaseTransport):
    """Connect to one validated IP while preserving the URL hostname for TLS.

    The request is built against the original hostname, so its Host header is
    already correct. Only the transport destination is replaced. httpcore's
    ``sni_hostname`` request extension keeps TLS certificate verification tied
    to the original hostname instead of the pinned IP address.
    """

    def __init__(self, address: str, transport: httpx.AsyncBaseTransport | None = None):
        self._address = address
        self._transport = transport or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        hostname = request.url.host
        request.extensions["sni_hostname"] = hostname
        request.url = request.url.copy_with(host=self._address)
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self._transport.aclose()


def _is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return address.is_global


async def _resolve_addresses(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve ``host`` off the event loop and return all usable addresses."""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        def _resolve():
            return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)

        infos = await asyncio.to_thread(_resolve)
        addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        for info in infos:
            try:
                addresses.add(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
        return sorted(addresses, key=lambda addr: (addr.version != 4, str(addr)))
    return [literal]


async def _public_target(url: str) -> tuple[str, str] | None:
    """Return ``(hostname, pinned_ip)`` for a safe manual URL, else ``None``."""
    try:
        parsed = urlparse((url or "").strip())
        if parsed.scheme != "https" or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        port = parsed.port or 443
        addresses = await _resolve_addresses(parsed.hostname, port)
    except (OSError, socket.gaierror, UnicodeError, ValueError):
        return None

    # Reject mixed public/private answers rather than selecting the convenient
    # one. That keeps split-horizon and rebinding-style answers out entirely.
    if not addresses or not all(_is_public(address) for address in addresses):
        return None
    return parsed.hostname, str(addresses[0])


async def _fetch_once(url: str) -> tuple[int, str | None, bytes | None] | None:
    """Fetch one URL hop after pinning its validated public DNS result."""
    target = await _public_target(url)
    if target is None:
        return None
    hostname, address = target

    await outbound.acquire(hostname)
    transport = _PinnedIPTransport(address)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            timeout=HTTP_TIMEOUT,
            follow_redirects=False,
        ) as client:
            async with client.stream("GET", url) as resp:
                location = resp.headers.get("location")
                if resp.status_code in _REDIRECT_STATUSES:
                    return resp.status_code, location, None
                if resp.status_code != 200:
                    return resp.status_code, None, None

                raw_length = resp.headers.get("content-length")
                if raw_length:
                    try:
                        if int(raw_length) > covers.MAX_COVER_SIZE:
                            return resp.status_code, None, None
                    except ValueError:
                        pass

                content = bytearray()
                async for chunk in resp.aiter_bytes():
                    if len(content) + len(chunk) > covers.MAX_COVER_SIZE:
                        return resp.status_code, None, None
                    content.extend(chunk)
                return resp.status_code, None, bytes(content)
    except (httpx.HTTPError, OSError):
        logger.debug("Manual cover download failed for %s", url, exc_info=True)
        return None


async def download(item_id: int, url: str) -> str | None:
    """Download a public HTTPS image into Shelf and return its relative path."""
    current = (url or "").strip()
    for _ in range(_MAX_REDIRECTS + 1):
        result = await _fetch_once(current)
        if result is None:
            return None
        status, location, content = result
        if status in _REDIRECT_STATUSES:
            if not location:
                return None
            current = urljoin(current, location)
            continue
        if status != 200 or content is None:
            return None
        if len(content) < covers.MIN_COVER_SIZE or not covers._looks_like_image(content):
            return None

        COVERS_DIR.mkdir(parents=True, exist_ok=True)
        (COVERS_DIR / f"{item_id}.jpg").write_bytes(content)
        return f"covers/{item_id}.jpg"
    return None
