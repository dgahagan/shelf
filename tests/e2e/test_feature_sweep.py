"""E2E: T4 — Scan offers only the modes of features that are on.

Lending is the only feature with `scan_modes` today (`app.features.FEATURES`),
so turning it off is the only case that removes a mode from `/scan`. The
`lending_off` fixture drives the toggle through Settings -> Features exactly
as `tests/e2e/test_features.py` does, and records + asserts the probe's
confirm-dialog message (G28) rather than accepting blind — an accept-only
handler would pass over a dead confirm. It restores Lending afterwards: the
live_server DB is session-scoped, and a leftover flag reddens unrelated
tests (G126's second route).
"""
import re
import sqlite3

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import assert_page_clean, attach_page_guard, insert_item
from tests.e2e.test_features import _feature_state, _open_features_tab
from tests.e2e.test_scan import _login_seeding_scan_mode

pytestmark = pytest.mark.e2e

# The div carrying the mode-switcher buttons -- scan.html gains no testid in
# this plan (scope is `data-off-modes` on the x-data root only), so the
# locator is the one class combination that string is unique to.
_MODE_SWITCHER = 'div.flex.flex-wrap.gap-2.mb-4'


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _open_loan_counts(data_dir) -> tuple[int, int]:
    """Mirrors `app.features._probe_open_loans`'s predicate, but against the
    physical `items` table: `items_live` is a per-connection temp view that
    only `get_db()` creates, so a raw sqlite3 connection has to spell out
    `deleted_at IS NULL` itself."""
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        loans, borrowers = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT c.borrower_id) FROM checkouts c "
            "JOIN items i ON c.item_id = i.id "
            "WHERE c.checked_in IS NULL AND i.deleted_at IS NULL"
        ).fetchone()
        return loans, borrowers
    finally:
        conn.close()


def _login(ctx, base_url, setup_admin):
    pg = attach_page_guard(ctx.new_page())
    pg.goto(f"{base_url}/login")
    pg.fill("input[name=username]", setup_admin["username"])
    pg.fill("input[name=password]", setup_admin["password"])
    pg.click("button[type=submit]")
    pg.wait_for_url(f"{base_url}/", timeout=10_000)
    return pg


def _seed_open_loan(data_dir) -> tuple[int, int]:
    """One open loan, so turning Lending off always raises the probe's
    confirm (G28 needs the dialog to fire, not merely be allowed to).
    Returns (item_id, borrower_id) for teardown."""
    item_id = insert_item(data_dir, title="Sweep Loaned Book")
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        borrower_id = conn.execute(
            "INSERT INTO borrowers (name) VALUES ('Sweep Borrower')"
        ).lastrowid
        conn.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_out) "
            "VALUES (?, ?, datetime('now'))",
            (item_id, borrower_id),
        )
        conn.commit()
    finally:
        conn.close()
    return item_id, borrower_id


def _remove_open_loan(data_dir, item_id: int, borrower_id: int) -> None:
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        conn.execute("DELETE FROM checkouts WHERE item_id = ?", (item_id,))
        conn.execute("DELETE FROM item_copies WHERE item_id = ?", (item_id,))
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.execute("DELETE FROM borrowers WHERE id = ?", (borrower_id,))
        conn.commit()
    finally:
        conn.close()


def _toggle_lending(browser, base_url, setup_admin, expect_state, dialogs=None):
    ctx = browser.new_context()
    try:
        pg = _login(ctx, base_url, setup_admin)
        _open_features_tab(pg, base_url)
        if dialogs is not None:
            def _accept(dialog):
                dialogs.append(dialog.message)
                dialog.accept()
            pg.once("dialog", _accept)
        pg.click('[data-testid="feature-toggle-lending"]')
        expect(pg.locator(f'[data-testid="feature-row-lending"] [data-feature-state="{expect_state}"]')).to_be_visible()
        assert _feature_state(pg, "lending") == expect_state
        assert_page_clean(pg)
    finally:
        ctx.close()


@pytest.fixture
def lending_off(browser, live_server, setup_admin):
    """Turn Lending off through Settings -> Features; restore it afterwards.

    Seeds one open loan so the probe's confirm always fires, then builds the
    expected text from a fresh count: another test may have left loans in
    the session DB too. The seeded rows are removed on the way out, even
    when setup fails (G126's second route).
    """
    base_url = live_server["url"]
    data_dir = live_server["data_dir"]
    item_id, borrower_id = _seed_open_loan(data_dir)
    turned_off = False
    try:
        loans, borrowers = _open_loan_counts(data_dir)
        assert loans >= 1
        dialogs: list[str] = []
        _toggle_lending(browser, base_url, setup_admin, "off", dialogs)
        turned_off = True
        verb = "stays" if loans == 1 else "stay"
        expected = (
            f"{_plural(loans, 'open loan')} to {_plural(borrowers, 'borrower')} "
            f"{verb} recorded, but overdue reminders pause. Turn it off anyway?"
        )
        assert dialogs == [expected]

        yield
    finally:
        if turned_off:
            _toggle_lending(browser, base_url, setup_admin, "on")
        _remove_open_loan(data_dir, item_id, borrower_id)


def test_lending_off_shows_add_and_no_lend_or_return(live_server, authed_page, lending_off):
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    switcher = authed_page.locator(_MODE_SWITCHER)
    expect(switcher.get_by_role("button", name="Add", exact=True)).to_be_visible()
    expect(switcher.get_by_role("button", name="Lend", exact=True)).to_have_count(0)
    expect(switcher.get_by_role("button", name="Return", exact=True)).to_have_count(0)


def test_lending_off_with_seeded_lend_mode_falls_back_to_add(
    browser, live_server, setup_admin, lending_off
):
    ctx, pg = _login_seeding_scan_mode(
        browser, live_server, setup_admin, "lend", "/scan"
    )
    try:
        switcher = pg.locator(_MODE_SWITCHER)
        add_button = switcher.get_by_role("button", name="Add", exact=True)
        expect(add_button).to_be_visible()
        expect(add_button).to_have_class(re.compile(r"bg-shelf-accent"))
        assert pg.evaluate("localStorage.getItem('shelf_scan_mode')") == "add"
        assert_page_clean(pg)
    finally:
        ctx.close()


def test_lending_on_offers_all_eight_modes(live_server, authed_page):
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    switcher = authed_page.locator(_MODE_SWITCHER)
    expect(switcher.locator("button")).to_have_count(8)
