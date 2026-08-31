"""Regression coverage for checkout/check-in request boundaries."""

from tests.conftest import _insert_borrower, _insert_item


def test_checkout_rejects_due_date_overflow(admin_client):
    resp = admin_client.post(
        "/api/items/999999/checkout",
        data={"borrower_id": "999999", "due_days": str(10**100)},
    )
    assert resp.status_code == 400
    assert resp.json() == {"ok": False, "message": "Due date is out of range"}


def test_checkin_rejects_already_returned_checkout(admin_client, db):
    item_id = _insert_item(db, title="Already Returned", isbn="9780000000200")
    borrower_id = _insert_borrower(db, "Returned Borrower")
    cursor = db.execute(
        "INSERT INTO checkouts (item_id, borrower_id, checked_out, checked_in) "
        "VALUES (?, ?, datetime('now', '-1 day'), datetime('now'))",
        (item_id, borrower_id),
    )
    checkout_id = cursor.lastrowid
    db.commit()

    resp = admin_client.post(f"/api/checkouts/{checkout_id}/checkin")
    assert resp.status_code == 409
    assert resp.json() == {"ok": False, "message": "Checkout already checked in"}
