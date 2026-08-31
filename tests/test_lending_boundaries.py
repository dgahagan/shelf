"""Regression coverage for borrower feedback and serialized checkout creation."""

import sqlite3
from contextlib import contextmanager

from tests.conftest import _insert_borrower, _insert_item


def test_blank_borrower_preserves_bad_request_contract(admin_client, db):
    response = admin_client.post(
        "/api/borrowers",
        data={"name": "   "},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert db.execute("SELECT COUNT(*) FROM borrowers").fetchone()[0] == 0


def test_duplicate_borrower_is_reported_instead_of_silently_ignored(admin_client, db):
    _insert_borrower(db, "Alice")
    db.commit()

    response = admin_client.post(
        "/api/borrowers",
        data={"name": "Alice"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?borrower_error=duplicate"
    assert db.execute(
        "SELECT COUNT(*) FROM borrowers WHERE name = 'Alice'"
    ).fetchone()[0] == 1


def test_delete_missing_borrower_is_reported(admin_client):
    response = admin_client.post(
        "/api/borrowers/999999/delete",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?borrower_error=missing"


def test_borrower_mutation_banner_never_reflects_unknown_code(admin_client):
    known = admin_client.get("/settings?borrower_error=duplicate")
    assert 'data-testid="borrower-mutation-error-banner"' in known.text
    assert "A borrower with that name already exists." in known.text

    unknown = admin_client.get(
        "/settings?borrower_error=%3Cscript%3Ealert(1)%3C%2Fscript%3E"
    )
    assert 'data-testid="borrower-mutation-error-banner"' not in unknown.text
    assert "alert(1)" not in unknown.text


def test_checkout_active_guard_runs_under_write_lock(admin_client, db, monkeypatch):
    """A competing checkout writer must be locked out while the guard is read."""
    import app.config
    import app.routers.checkouts as checkouts_mod

    item_id = _insert_item(db, title="Serialized Loan", isbn="9780306406157")
    borrower_id = _insert_borrower(db, "Lock Tester")
    db.commit()

    probe_results = []
    real_get_db = checkouts_mod.get_db

    class LockProbingConnection:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def execute(self, sql, *args, **kwargs):
            result = self._conn.execute(sql, *args, **kwargs)
            if "WHERE item_id = ? AND checked_in IS NULL" in sql:
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
    response = admin_client.post(
        f"/api/items/{item_id}/checkout",
        data={"borrower_id": str(borrower_id), "due_days": "14"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert probe_results, "checkout guard query never ran"
    assert probe_results[0].startswith("locked"), (
        "a rival writer acquired the database while checkout's active-loan "
        f"guard was being read: {probe_results[0]!r}"
    )
