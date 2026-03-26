"""Tests for app.routers.checkouts — borrowers, checkout, checkin, overdue."""

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
        # Should return error JSON, not redirect
        assert resp.json()["ok"] is False
        assert "active checkouts" in resp.json()["message"]

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
