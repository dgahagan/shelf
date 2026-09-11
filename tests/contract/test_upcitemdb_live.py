"""One live check against the real UPC Item DB trial endpoint.

Off every gate (#123): this spends one trial-tier lookup from the 100/day
budget the whole stub-and-fixture plan exists to protect, so it runs only via
`make test-contract`, never in `make test` or `make test-e2e`.

A **skip** here is not "the checks are clean" for `/release` — it means the
day's quota was already spent and the contract went *unchecked* this run.
Record the reset time from the skip reason and move on; do not wait for it
inside the release.

To look by hand instead of running this test:

    curl -s -o /dev/null -w '%{http_code}\n' \
        "https://api.upcitemdb.com/prod/trial/lookup?upc=000000000000"

    curl -sD - -o /dev/null \
        "https://api.upcitemdb.com/prod/trial/lookup?upc=000000000000" \
        | grep -iE 'ratelimit|retry-after'
"""

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "upcitemdb_lookup_000000000000.json"


@pytest.mark.live
async def test_the_trial_endpoint_still_serves_the_recorded_placeholder():
    # G14: app.services.upcitemdb / app.config import app.main transitively —
    # keep the import inside the test so it runs after the autouse DATA_DIR
    # fixture, not at collection.
    import app.config  # noqa: F401
    from app.services import upcitemdb

    seen = {}

    async def _capture(response):
        seen["headers"] = response.headers

    async with httpx.AsyncClient(timeout=15, event_hooks={"response": [_capture]}) as client:
        result = await upcitemdb.lookup("000000000000", client)

    if result.outcome == "found":
        recorded = json.loads(FIXTURE.read_text())
        expected_title = recorded["items"][0]["title"]
        assert result.payload["title"] == expected_title, (
            f"UPC Item DB's placeholder title changed upstream "
            f"({result.payload['title']!r} != {expected_title!r}). "
            f"Re-record the fixture: {FIXTURE.relative_to(FIXTURE.parent.parent.parent)} "
            "via `curl -s \"https://api.upcitemdb.com/prod/trial/lookup?"
            "upc=000000000000\"`."
        )
        return

    if result.outcome == "rate_limited":
        pytest.skip(_quota_skip_reason(seen.get("headers")))

    if result.outcome == "transport_failed":
        pytest.skip("UPC Item DB unreachable — contract not checked")

    pytest.fail(
        f"unexpected outcome {result.outcome!r} (HTTP {result.status}) — "
        "the recorded placeholder may have been dropped, or the response "
        "shape changed"
    )


def _quota_skip_reason(headers) -> str:
    """Build a skip reason from the last response's headers, no extra request.

    `lookup` discards headers, so the reset epoch is only visible via the
    `event_hooks` capture on the client that made the one lookup this test is
    allowed to spend. Falls back to an admission that the reset time is
    unknown rather than raising inside the skip path.
    """
    if headers is not None:
        reset = headers.get("X-RateLimit-Reset")
        if reset is not None:
            try:
                when = datetime.fromtimestamp(int(reset)).isoformat(sep=" ", timespec="seconds")
            except (ValueError, OSError, OverflowError):
                pass
            else:
                return (
                    f"UPC Item DB trial quota spent — resets at {when}; "
                    "the contract was not checked"
                )
    return (
        "UPC Item DB trial quota spent — reset time unknown; "
        "the contract was not checked"
    )
