"""Tests for app.routers.checkouts — borrowers, checkout, checkin, overdue."""

from contextlib import contextmanager

import pytest

from app.database import get_db
from tests.conftest import _insert_item, _insert_borrower


class TestBorrowers:
    def test_create_borrower(self, admin_client):
        resp = admin_client.post("/api/borrowers", data={"name": "Alice"}, follow_redirects=False)
        assert resp.status_code == 303
        with get_db() as db:
            row = db.execute("SELECT name FROM borrowers WHERE name = 'Alice'").fetchone()
        assert row is not None

    def test_create_duplicate_borrower_ignored(self, admin_client, db):
        _insert_borrower(db, "Bob")
        db.commit()
        resp = admin_client.post("/api/borrowers", data={"name": "Bob"}, follow_redirects=False)
        assert resp.status_code == 303
        with get_db() as check_db:
            count = check_db.execute("SELECT COUNT(*) as c FROM borrowers WHERE name = 'Bob'").fetchone()["c"]
        assert count == 1

    def test_delete_borrower(self, admin_client, db):
        bid = _insert_borrower(db, "Carol")
        db.commit()
        resp = admin_client.post(f"/api/borrowers/{bid}/delete", follow_redirects=False)
        assert resp.status_code == 303
        with get_db() as check_db:
            row = check_db.execute("SELECT id FROM borrowers WHERE id = ?", (bid,)).fetchone()
        assert row is None

    def test_delete_borrower_with_active_checkout_blocked(self, admin_client, db):
        bid = _insert_borrower(db, "Dan")
        item_id = _insert_item(db, title="Book", isbn="9780000000100")
        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_out) VALUES (?, ?, datetime('now'))",
            (item_id, bid),
        )
        db.commit()
        resp = admin_client.post(f"/api/borrowers/{bid}/delete", follow_redirects=False)
        # A plain form post gets a page back, not raw JSON (issue #29).
        assert resp.status_code == 303
        assert resp.headers["location"] == "/settings?borrower_error=active"
        with get_db() as check_db:
            assert check_db.execute("SELECT id FROM borrowers WHERE id = ?", (bid,)).fetchone() is not None
            assert check_db.execute(
                "SELECT COUNT(*) as c FROM checkouts WHERE borrower_id = ?", (bid,)
            ).fetchone()["c"] == 1

    def test_delete_borrower_with_returned_history_succeeds(self, admin_client, db):
        """Regression pin for issue #29: a completed loan used to 500 the delete."""
        bid = _insert_borrower(db, "Erin")
        item_id = _insert_item(db, title="Returned Book", isbn="9780000000101")
        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_out, checked_in) "
            "VALUES (?, ?, datetime('now'), datetime('now'))",
            (item_id, bid),
        )
        db.commit()
        resp = admin_client.post(f"/api/borrowers/{bid}/delete", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/settings"
        with get_db() as check_db:
            assert check_db.execute("SELECT id FROM borrowers WHERE id = ?", (bid,)).fetchone() is None
            assert check_db.execute(
                "SELECT COUNT(*) as c FROM checkouts WHERE borrower_id = ?", (bid,)
            ).fetchone()["c"] == 0

    def test_delete_borrower_history_is_scoped(self, admin_client, db):
        """Only the deleted borrower's rows go — a co-borrower on the same item stays."""
        item_id = _insert_item(db, title="Shared Book", isbn="9780000000102")
        a_id = _insert_borrower(db, "Aaron")
        b_id = _insert_borrower(db, "Bianca")
        for borrower_id in (a_id, b_id):
            db.execute(
                "INSERT INTO checkouts (item_id, borrower_id, checked_out, checked_in) "
                "VALUES (?, ?, datetime('now'), datetime('now'))",
                (item_id, borrower_id),
            )
        db.commit()
        resp = admin_client.post(f"/api/borrowers/{a_id}/delete", follow_redirects=False)
        assert resp.status_code == 303
        with get_db() as check_db:
            assert check_db.execute("SELECT id FROM borrowers WHERE id = ?", (a_id,)).fetchone() is None
            assert check_db.execute("SELECT id FROM borrowers WHERE id = ?", (b_id,)).fetchone() is not None
            assert check_db.execute(
                "SELECT COUNT(*) as c FROM checkouts WHERE borrower_id = ?", (a_id,)
            ).fetchone()["c"] == 0
            assert check_db.execute(
                "SELECT COUNT(*) as c FROM checkouts WHERE borrower_id = ?", (b_id,)
            ).fetchone()["c"] == 1

    def test_delete_borrower_guard_reads_under_write_lock(self, admin_client, db, monkeypatch):
        """G18: the active-loan guard must be read while holding the write lock.

        Without `BEGIN IMMEDIATE` first, sqlite3 opens no transaction for the
        guard SELECT, so another connection could commit a checkout between
        the read and the DELETE and have it destroyed as "history". Probe
        from inside the route: at the moment the guard runs, a competing
        writer must already be locked out.
        """
        import sqlite3

        import app.config
        import app.routers.checkouts as checkouts_mod

        bid = _insert_borrower(db, "Frida")
        _insert_item(db, title="Lockable", isbn="9780000000103")
        db.commit()

        probe_results = []
        real_get_db = checkouts_mod.get_db

        class LockProbingConnection:
            """Passes everything through, but probes the lock at guard time."""

            def __init__(self, conn):
                self._conn = conn

            def __getattr__(self, name):
                return getattr(self._conn, name)

            def execute(self, sql, *args, **kwargs):
                result = self._conn.execute(sql, *args, **kwargs)
                if "checked_in IS NULL" in sql:
                    rival = sqlite3.connect(str(app.config.DATABASE_PATH), timeout=0)
                    try:
                        rival.execute("BEGIN IMMEDIATE")
                        probe_results.append("acquired")
                        rival.rollback()
                    except sqlite3.OperationalError as exc:
                        probe_results.append(f"locked: {exc}")
                    finally:
                        rival.close()
                return result

        @contextmanager
        def probing_get_db():
            with real_get_db() as conn:
                yield LockProbingConnection(conn)

        monkeypatch.setattr(checkouts_mod, "get_db", probing_get_db)
        resp = admin_client.post(f"/api/borrowers/{bid}/delete", follow_redirects=False)
        assert resp.status_code == 303

        assert probe_results, "guard query never ran — the probe did not fire"
        assert probe_results[0].startswith("locked"), (
            "a rival writer could take the write lock while the guard was being "
            f"read (got {probe_results[0]!r}) — the route is missing its "
            "BEGIN IMMEDIATE, or takes it after the guard SELECT (G18)"
        )

    def test_delete_borrower_rolls_back_on_failure(self, admin_client, db, monkeypatch):
        """The checkout delete must not commit unless the borrower delete does."""
        import app.routers.checkouts as checkouts_mod

        bid = _insert_borrower(db, "Gus")
        item_id = _insert_item(db, title="Rollback Book", isbn="9780000000104")
        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_out, checked_in) "
            "VALUES (?, ?, datetime('now'), datetime('now'))",
            (item_id, bid),
        )
        db.commit()

        real_get_db = checkouts_mod.get_db

        class FailingBorrowerDelete:
            def __init__(self, conn):
                self._conn = conn

            def __getattr__(self, name):
                return getattr(self._conn, name)

            def execute(self, sql, *args, **kwargs):
                if "DELETE FROM borrowers" in sql:
                    raise RuntimeError("boom")
                return self._conn.execute(sql, *args, **kwargs)

        @contextmanager
        def failing_get_db():
            with real_get_db() as conn:
                yield FailingBorrowerDelete(conn)

        monkeypatch.setattr(checkouts_mod, "get_db", failing_get_db)
        with pytest.raises(RuntimeError):
            admin_client.post(f"/api/borrowers/{bid}/delete", follow_redirects=False)

        with get_db() as check_db:
            assert check_db.execute(
                "SELECT COUNT(*) as c FROM checkouts WHERE borrower_id = ?", (bid,)
            ).fetchone()["c"] == 1, "checkout delete committed without the borrower delete"
            assert check_db.execute("SELECT id FROM borrowers WHERE id = ?", (bid,)).fetchone() is not None

    def test_borrower_requires_admin(self, editor_client):
        resp = editor_client.post("/api/borrowers", data={"name": "Hacker"}, follow_redirects=False)
        assert resp.status_code in (303, 401, 403)


class TestCheckout:
    def test_checkout_item(self, admin_client, db):
        item_id = _insert_item(db, title="Checkout Book", isbn="9780000000110")
        bid = _insert_borrower(db, "Eve")
        db.commit()
        resp = admin_client.post(f"/api/items/{item_id}/checkout", data={
            "borrower_id": str(bid),
            "due_days": "14",
            "notes": "Be careful",
        }, follow_redirects=False)
        assert resp.status_code == 303

        with get_db() as check_db:
            checkout = check_db.execute(
                "SELECT * FROM checkouts WHERE item_id = ? AND checked_in IS NULL", (item_id,)
            ).fetchone()
        assert checkout is not None
        assert checkout["borrower_id"] == bid
        assert checkout["due_date"] is not None
        assert checkout["notes"] == "Be careful"

    def test_checkout_already_checked_out(self, admin_client, db):
        item_id = _insert_item(db, title="Already Out", isbn="9780000000111")
        bid = _insert_borrower(db, "Frank")
        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_out) VALUES (?, ?, datetime('now'))",
            (item_id, bid),
        )
        db.commit()
        resp = admin_client.post(f"/api/items/{item_id}/checkout", data={
            "borrower_id": str(bid), "due_days": "14",
        })
        assert resp.json()["ok"] is False
        assert "Already checked out" in resp.json()["message"]


class TestCheckin:
    def test_checkin_item(self, admin_client, db):
        item_id = _insert_item(db, title="Return Book", isbn="9780000000120")
        bid = _insert_borrower(db, "Grace")
        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_out) VALUES (?, ?, datetime('now'))",
            (item_id, bid),
        )
        db.commit()
        with get_db() as check_db:
            checkout = check_db.execute(
                "SELECT id FROM checkouts WHERE item_id = ? AND checked_in IS NULL", (item_id,)
            ).fetchone()
        resp = admin_client.post(f"/api/checkouts/{checkout['id']}/checkin", follow_redirects=False)
        assert resp.status_code == 303

        with get_db() as check_db:
            row = check_db.execute("SELECT checked_in FROM checkouts WHERE id = ?", (checkout["id"],)).fetchone()
        assert row["checked_in"] is not None

    def test_checkin_nonexistent(self, admin_client):
        resp = admin_client.post("/api/checkouts/99999/checkin")
        assert resp.json()["ok"] is False


class TestOverdue:
    def test_overdue_list_empty(self, admin_client):
        resp = admin_client.get("/api/checkouts/overdue")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_overdue_list_with_overdue_items(self, admin_client, db):
        item_id = _insert_item(db, title="Overdue Book", isbn="9780000000130")
        bid = _insert_borrower(db, "Hank")
        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_out, due_date) VALUES (?, ?, datetime('now', '-30 days'), date('now', '-1 day'))",
            (item_id, bid),
        )
        db.commit()
        resp = admin_client.get("/api/checkouts/overdue")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Overdue Book"
        assert data[0]["borrower_name"] == "Hank"
