"""Live contract check for the hosted Audnexus API.

Runs only via `make test-contract`, never in `make test`. A skip means the
contract went unchecked this run (rate limited or unreachable), not that it
is clean. Do not wait for it inside a release.

By hand:

    curl -s 'https://api.audnex.us/books/B08G9PRS1K?region=us'
"""

import httpx
import pytest


@pytest.mark.live
async def test_the_hosted_api_still_serves_a_known_asin():
    # G14: keep app imports inside the test body, after the autouse
    # DATA_DIR fixture, not at collection.
    import app.config  # noqa: F401
    from app.services import audnexus, covers

    async with httpx.AsyncClient(timeout=15) as client:
        result = await audnexus.lookup("B08G9PRS1K", client, region="us")

    if result.outcome in ("rate_limited", "transport_failed"):
        pytest.skip(f"Audnexus answered {result.outcome} — contract not checked")

    assert result.found, f"unexpected outcome {result.outcome!r} (HTTP {result.status})"
    for key in ("title", "authors", "narrator", "duration_mins", "cover_url"):
        assert result.payload.get(key), f"{key} is empty — the response shape may have changed"
    assert covers.is_allowed_cover_url(result.payload["cover_url"])
