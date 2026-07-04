"""Tests for loan reminders: overdue computation, notify service, digest task."""
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from tests.conftest import _insert_borrower, _insert_item


def _lend(db, item_id, borrower_id, days_ago=0, due_date=None, checked_in=False):
    db.execute(
        "INSERT INTO checkouts (item_id, borrower_id, checked_out, due_date, checked_in) "
        "VALUES (?, ?, datetime('now', ?), ?, ?)",
        (item_id, borrower_id, f"-{days_ago} days", due_date,
         "2026-01-01 00:00:00" if checked_in else None),
    )


def _set(db, key, value):
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
        (key, value, value),
    )


# ---------------------------------------------------------------------------
# Notify service
# ---------------------------------------------------------------------------


class TestNotifyService:
    @pytest.mark.asyncio
    @respx.mock
    async def test_ntfy_posts_plain_text_with_title(self):
        from app.services.notify import send_notification
        route = respx.post("https://ntfy.example/topic").mock(return_value=httpx.Response(200))
        ok = await send_notification("https://ntfy.example/topic", "Title!", "line1\nline2", "ntfy")
        assert ok is True
        req = route.calls[0].request
        assert req.headers["x-title"] == "Title!"
        assert req.content == b"line1\nline2"

    @pytest.mark.asyncio
    @respx.mock
    async def test_webhook_posts_json(self):
        from app.services.notify import send_notification
        route = respx.post("https://hook.example/x").mock(return_value=httpx.Response(204))
        ok = await send_notification("https://hook.example/x", "T", "M", "webhook")
        assert ok is True
        import json
        assert json.loads(route.calls[0].request.content) == {"title": "T", "message": "M"}

    @pytest.mark.asyncio
    @respx.mock
    async def test_non_2xx_returns_false(self):
        from app.services.notify import send_notification
        respx.post("https://ntfy.example/topic").mock(return_value=httpx.Response(500))
        assert await send_notification("https://ntfy.example/topic", "T", "M", "ntfy") is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_connection_error_returns_false(self):
        from app.services.notify import send_notification
        respx.post("https://down.example/x").mock(side_effect=httpx.ConnectError("boom"))
        assert await send_notification("https://down.example/x", "T", "M", "ntfy") is False

    @pytest.mark.asyncio
    async def test_bad_format_or_empty_url_rejected(self):
        from app.services.notify import send_notification
        assert await send_notification("", "T", "M", "ntfy") is False
        assert await send_notification("https://x.example", "T", "M", "carrier-pigeon") is False


# ---------------------------------------------------------------------------
# Overdue computation (via /api/checkouts/overdue)
# ---------------------------------------------------------------------------


class TestOverdueComputation:
    def _seed(self, db, title, **lend_kwargs):
        item_id = _insert_item(db, title=title, isbn=f"978{abs(hash(title)) % 10**10:010d}")
        borrower_id = _insert_borrower(db, name=f"B-{title}")
        _lend(db, item_id, borrower_id, **lend_kwargs)
        return item_id

    def test_explicit_due_date_past_is_overdue(self, admin_client, db):
        self._seed(db, "Due Yesterday", days_ago=3, due_date="2020-01-01")
        db.execute("COMMIT")
        titles = [o["title"] for o in admin_client.get("/api/checkouts/overdue").json()]
        assert "Due Yesterday" in titles

    def test_no_due_date_past_default_window_is_overdue(self, admin_client, db):
        self._seed(db, "Out 40 Days", days_ago=40)
        db.execute("COMMIT")
        overdue = admin_client.get("/api/checkouts/overdue").json()
        entry = next(o for o in overdue if o["title"] == "Out 40 Days")
        assert entry["days_out"] >= 39

    def test_no_due_date_within_window_not_overdue(self, admin_client, db):
        self._seed(db, "Out 5 Days", days_ago=5)
        db.execute("COMMIT")
        titles = [o["title"] for o in admin_client.get("/api/checkouts/overdue").json()]
        assert "Out 5 Days" not in titles

    def test_custom_window_setting(self, admin_client, db):
        self._seed(db, "Out 10 Days", days_ago=10)
        _set(db, "lending_overdue_days", "5")
        db.execute("COMMIT")
        titles = [o["title"] for o in admin_client.get("/api/checkouts/overdue").json()]
        assert "Out 10 Days" in titles

    def test_zero_disables_fallback_but_not_due_dates(self, admin_client, db):
        self._seed(db, "Fallback Loan", days_ago=400)
        self._seed(db, "Due Date Loan", days_ago=3, due_date="2020-01-01")
        _set(db, "lending_overdue_days", "0")
        db.execute("COMMIT")
        titles = [o["title"] for o in admin_client.get("/api/checkouts/overdue").json()]
        assert "Fallback Loan" not in titles
        assert "Due Date Loan" in titles

    def test_returned_loan_never_overdue(self, admin_client, db):
        self._seed(db, "Returned Book", days_ago=100, checked_in=True)
        db.execute("COMMIT")
        titles = [o["title"] for o in admin_client.get("/api/checkouts/overdue").json()]
        assert "Returned Book" not in titles


# ---------------------------------------------------------------------------
# Digest task
# ---------------------------------------------------------------------------


class TestReminderDigest:
    def _seed_overdue(self, db):
        item_id = _insert_item(db, title="Very Late Book", isbn="9780900000101")
        borrower_id = _insert_borrower(db, name="Slow Reader")
        _lend(db, item_id, borrower_id, days_ago=60)
        _set(db, "notify_url", "https://ntfy.example/shelf")
        db.execute("COMMIT")

    @pytest.mark.asyncio
    async def test_digest_sent_with_details(self, db):
        from app.main import check_loan_reminders
        self._seed_overdue(db)

        with patch("app.services.notify.send_notification", new=AsyncMock(return_value=True)) as send:
            assert await check_loan_reminders() is True
            title, message = send.call_args.args[1], send.call_args.args[2]
        assert "1 overdue loan" in title
        assert "Very Late Book" in message
        assert "Slow Reader" in message

    @pytest.mark.asyncio
    async def test_throttled_to_once_per_day(self, db):
        from app.main import check_loan_reminders
        self._seed_overdue(db)

        with patch("app.services.notify.send_notification", new=AsyncMock(return_value=True)) as send:
            assert await check_loan_reminders() is True
            assert await check_loan_reminders() is False  # within 24h window
            assert send.await_count == 1

    @pytest.mark.asyncio
    async def test_failed_send_not_recorded_as_sent(self, db):
        from app.main import check_loan_reminders
        self._seed_overdue(db)

        with patch("app.services.notify.send_notification", new=AsyncMock(return_value=False)) as send:
            assert await check_loan_reminders() is False
            assert await check_loan_reminders() is False
            assert send.await_count == 2  # retried — last_sent was never written

    @pytest.mark.asyncio
    async def test_no_url_configured_is_noop(self, db):
        from app.main import check_loan_reminders
        with patch("app.services.notify.send_notification", new=AsyncMock()) as send:
            assert await check_loan_reminders() is False
            send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_overdue_loans_is_noop(self, db):
        from app.main import check_loan_reminders
        _set(db, "notify_url", "https://ntfy.example/shelf")
        db.execute("COMMIT")
        with patch("app.services.notify.send_notification", new=AsyncMock()) as send:
            assert await check_loan_reminders() is False
            send.assert_not_awaited()


# ---------------------------------------------------------------------------
# Settings endpoints + UI surface
# ---------------------------------------------------------------------------


class TestLendingSettings:
    def test_save_and_encrypt(self, admin_client, db):
        resp = admin_client.post(
            "/api/settings/lending",
            data={"lending_overdue_days": "14", "notify_url": "https://ntfy.example/topic",
                  "notify_format": "webhook"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        from app.database import get_setting, get_db
        with get_db() as conn:
            raw = conn.execute("SELECT value FROM settings WHERE key = 'notify_url'").fetchone()["value"]
            assert raw != "https://ntfy.example/topic"  # encrypted at rest
            assert get_setting(conn, "notify_url") == "https://ntfy.example/topic"
            assert get_setting(conn, "lending_overdue_days") == "14"
            assert get_setting(conn, "notify_format") == "webhook"

    def test_rejects_non_numeric_days(self, admin_client):
        resp = admin_client.post(
            "/api/settings/lending",
            data={"lending_overdue_days": "soon", "notify_url": "", "notify_format": "ntfy"},
        )
        assert resp.json()["ok"] is False

    def test_notify_test_endpoint(self, admin_client):
        with patch("app.services.notify.send_notification", new=AsyncMock(return_value=True)) as send:
            resp = admin_client.post("/api/settings/notify-test",
                                     json={"url": "https://ntfy.example/t", "format": "ntfy"})
        assert resp.json()["ok"] is True
        send.assert_awaited_once()

    def test_settings_page_shows_lending_card(self, admin_client):
        html = admin_client.get("/settings").text
        assert "Lending" in html
        assert "lending_overdue_days" in html


class TestOverdueBadge:
    def test_browse_shows_overdue_badge(self, admin_client, db):
        item_id = _insert_item(db, title="Badge Overdue", isbn="9780900000201")
        borrower_id = _insert_borrower(db, name="Late Larry")
        _lend(db, item_id, borrower_id, days_ago=60)
        db.execute("COMMIT")
        html = admin_client.get("/browse").text
        assert "Overdue" in html

    def test_browse_shows_loaned_for_recent(self, admin_client, db):
        item_id = _insert_item(db, title="Badge Recent", isbn="9780900000218")
        borrower_id = _insert_borrower(db, name="Prompt Pam")
        _lend(db, item_id, borrower_id, days_ago=2)
        db.execute("COMMIT")
        html = admin_client.get("/browse").text
        assert "Loaned" in html
        assert "Overdue" not in html
