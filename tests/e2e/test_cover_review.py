"""E2E: walk the cover review queue end to end (issue behind T7 of the
cover-attention-queue plan).

Seeds three cover-less items of three different media types — a book, a DVD
and a video game, since that spread is the whole point of this queue over the
media-type-gated automatic sweep (see `app/routers/cover_review.py`'s module
docstring) — then drives the real page: dismiss one, give another a cover, and
confirm the third is what is left. Every action swaps `#cover-review` via
HTMX (`hx-swap="outerHTML"`) and the page never navigates, so this test
prefers `expect(locator)` waits over `wait_for_load_state` throughout (G83).

Uses `server_factory` rather than the shared session-scoped `live_server`: the
queue's own predicate is deliberately unfiltered (no media-type gate — see
that same docstring), so a shared DB accumulates cover-less rows from every
other E2E test in the session (a real one surfaced during authoring:
test_component_load_guard.py's "Guard Control Item" outranked this test's
seeded rows in the queue's newest-first order and broke the "3 of 3" premise).
A fresh per-test server is the only way to make "exactly these three items"
true.

**This test found a real defect while it was being written**, now fixed: the
queue card's `cover-select` buttons and `cover-upload` form
(`app/templates/fragments/cover_search.html`) sent no `pos`/`total` — unlike
Skip (query string) and Dismiss (`hx-vals`) — so `cover_review_actions.py` fell
back to its defaults and the on-screen "N of M" counter read "2 of 0"
immediately after a real pick or upload. Ordering was never affected
(`next_from_key` seeks off the database row, not off the carried value), so it
was a display bug rather than a queue-integrity one — which is exactly the kind
only a driven browser notices. The fragment now emits both fields for a counted
host and neither for item detail, and every counter value below is asserted.
"""
import sqlite3

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import (
    _run_setup_wizard,
    assert_page_clean,
    attach_page_guard,
    insert_item,
)

pytestmark = pytest.mark.e2e

# A minimal but valid PNG — magic bytes are all `covers.save_uploaded_cover`
# checks (see tests/test_cover_review.py's TestUploadingACover._png).
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200


def _seed_three(data_dir):
    """Newest-first order (`updated_at DESC, id DESC`) is the queue's own
    order, so timestamps are pinned explicitly rather than left to insert
    order — the game shows first, then the DVD, then the book.
    """
    book = insert_item(
        data_dir, title="E2E Queue Book", media_type="book",
        isbn=None, cover_path=None, updated_at="2020-01-01 00:00:00",
    )
    dvd = insert_item(
        data_dir, title="E2E Queue DVD", media_type="dvd",
        isbn=None, cover_path=None, updated_at="2020-01-02 00:00:00",
    )
    game = insert_item(
        data_dir, title="E2E Queue Game", media_type="video_game",
        isbn=None, cover_path=None, updated_at="2020-01-03 00:00:00",
    )
    return book, dvd, game


def test_walk_the_queue_dismiss_one_cover_another_leaves_the_third(
    server_factory, browser,
):
    """Seed a book, a DVD and a video game; dismiss the newest, upload a
    cover for the next, and confirm the third — the book — is what remains,
    both in the swapped card and on a fresh page load.
    """
    server = server_factory()
    base = server["url"]
    credentials = _run_setup_wizard(browser, base)
    book, dvd, game = _seed_three(server["data_dir"])

    ctx = browser.new_context()
    try:
        page = attach_page_guard(ctx.new_page())
        page.goto(f"{base}/login")
        page.fill("input[name=username]", credentials["username"])
        page.fill("input[name=password]", credentials["password"])
        page.click("button[type=submit]")
        page.wait_for_url(f"{base}/", timeout=10_000)

        page.goto(f"{base}/cover-review")
        page.wait_for_load_state("networkidle")

        title = page.get_by_test_id("cover-review-title")
        counter = page.get_by_test_id("cover-review-counter")
        expect(title).to_have_text("E2E Queue Game")
        expect(counter).to_have_text("1 of 3")

        # Dismiss the game — "Not available" — and land on the DVD next.
        page.get_by_test_id("cover-review-dismiss").click()
        expect(title).to_have_text("E2E Queue DVD")
        expect(counter).to_have_text("2 of 3")

        # Give the DVD a cover via upload (deterministic — no live provider
        # hit) and land on the book next.
        upload_form = page.get_by_test_id("cover-upload")
        upload_form.locator('input[type="file"]').set_input_files({
            "name": "cover.png", "mimeType": "image/png", "buffer": _PNG_BYTES,
        })
        upload_form.get_by_role("button", name="Upload").click()
        expect(title).to_have_text("E2E Queue Book")
        # The counter is asserted here too. Authoring this test is what found
        # that the picker's upload form carried no pos/total, so the server fell
        # back to its defaults and this read "2 of 0"; the fragment now emits
        # both for a counted host, and this line is the regression pin.
        expect(counter).to_have_text("3 of 3")

        # The queue is now empty of unresolved items except the book. A
        # fresh page load recomputes the total from scratch, so this is
        # independent proof — not just a read of the same swapped fragment.
        page.goto(f"{base}/cover-review")
        page.wait_for_load_state("networkidle")
        expect(title).to_have_text("E2E Queue Book")
        expect(counter).to_have_text("1 of 1")

        # And the database agrees: the game is dismissed, the DVD has a
        # cover, the book has neither.
        conn = sqlite3.connect(str(server["data_dir"] / "shelf.db"))
        try:
            rows = {
                r[0]: (r[1], r[2]) for r in conn.execute(
                    "SELECT id, cover_path, cover_review_dismissed FROM items"
                )
            }
        finally:
            conn.close()
        assert rows[game] == (None, 1)
        assert rows[dvd][0] is not None and rows[dvd][1] == 0
        assert rows[book] == (None, 0)

        assert_page_clean(page)
    finally:
        ctx.close()
