"""Outbound notifications for loan reminders (ntfy or generic JSON webhook).

Kept deliberately tiny: one function, two formats. The URL is operator-
configured in settings (encrypted at rest — an ntfy topic URL is effectively
a credential). Because of that, anything this module logs about the URL
names only the delivery target's scheme and host — never the userinfo, the path,
the query, or an exception's string form, which for `httpx` errors commonly
embeds the request URL.
"""
import logging
from urllib.parse import urlsplit

import httpx

from app.config import HTTP_TIMEOUT

logger = logging.getLogger(__name__)

FORMATS = ("ntfy", "webhook")


def _target(url: str) -> str:
    """Return `scheme://host[:port]` for `url`, safe to log — no userinfo, path
    or query.

    Deliberately not `netloc`: that carries `user:pass@`, and ntfy documents
    `https://user:pass@host/topic` for authenticated topics. `hostname` and
    `port` are the parsed halves without it.

    Both reads stay inside the `try`. `parts.port` raises `ValueError` on a
    non-numeric or out-of-range port, and this is called from inside
    `send_notification`'s `except httpx.HTTPError` arm — a raise escaping here
    would break that function's "returns False" contract.
    """
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        return "<unparseable url>"
    if not host:
        return "<unparseable url>"
    return f"{parts.scheme}://{host}:{port}" if port else f"{parts.scheme}://{host}"


async def send_notification(url: str, title: str, message: str, fmt: str = "ntfy") -> bool:
    """POST a notification. Returns True on 2xx, False otherwise (logged)."""
    if not url or fmt not in FORMATS:
        return False
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            if fmt == "ntfy":
                resp = await client.post(
                    url,
                    content=message.encode(),
                    headers={"X-Title": title, "X-Tags": "books"},
                )
            else:  # webhook
                resp = await client.post(url, json={"title": title, "message": message})
        if 200 <= resp.status_code < 300:
            return True
        logger.warning("Notification to %s returned %d", _target(url), resp.status_code)
        return False
    except httpx.HTTPError as e:
        logger.warning("Notification to %s failed: %s", _target(url), type(e).__name__)
        return False
