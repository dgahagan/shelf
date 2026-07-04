"""Outbound notifications for loan reminders (ntfy or generic JSON webhook).

Kept deliberately tiny: one function, two formats. The URL is operator-
configured in settings (encrypted at rest — an ntfy topic URL is effectively
a credential).
"""
import logging

import httpx

from app.config import HTTP_TIMEOUT

logger = logging.getLogger(__name__)

FORMATS = ("ntfy", "webhook")


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
        logger.warning("Notification to %s returned %d", url, resp.status_code)
        return False
    except httpx.HTTPError as e:
        logger.warning("Notification to %s failed: %s", url, e)
        return False
