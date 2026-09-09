"""Tests for the presentation-neutral Home dashboard summary."""

from app.services import home_dashboard
from app.services.item_write import insert_item


def test_empty_library_summary_is_zeroed(db):
    summary = home_dashboard.dashboard_summary(db)
    assert summary["total_count"] == 0
    assert summary["owned_count"] == 0
    assert summary["wishlist_count"] == 0
    assert summary["lent_out_count"] == 0
    assert summary["missing_cover_count"] == 0
    assert summary["media_types"] == []
    assert summary["recent_items"] == []


def test_summary_counts_owned_wishlist_and_missing_covers(db):
    insert_item(db, title="Owned Book", media_type="book", owned=1, cover_path="covers/1.jpg")
    insert_item(db, title="Wishlist Book", media_type="book", owned=0)
    insert_item(db, title="Disc", media_type="dvd", owned=1)

    summary = home_dashboard.dashboard_summary(db)
    assert summary["total_count"] == 3
    assert summary["owned_count"] == 2
    assert summary["wishlist_count"] == 1
    assert summary["missing_cover_count"] == 2


def test_media_type_breakdown_is_data_driven(db):
    insert_item(db, title="Book A", media_type="book", owned=1)
    insert_item(db, title="Book B", media_type="book", owned=0)
    insert_item(db, title="DVD", media_type="dvd", owned=1)

    breakdown = home_dashboard.dashboard_summary(db)["media_types"]
    assert breakdown == [
        {"media_type": "book", "item_count": 2, "owned_count": 1, "wishlist_count": 1},
        {"media_type": "dvd", "item_count": 1, "owned_count": 1, "wishlist_count": 0},
    ]


def test_lent_out_counts_items_not_checkout_rows(db):
    item_id = insert_item(db, title="Borrowed", media_type="book")
    borrower_id = db.execute(
        "INSERT INTO borrowers (name) VALUES ('Alice') RETURNING id"
    ).fetchone()["id"]
    db.execute(
        "INSERT INTO checkouts (item_id, borrower_id) VALUES (?, ?)",
        (item_id, borrower_id),
    )
    db.execute(
        "INSERT INTO checkouts (item_id, borrower_id, checked_in) "
        "VALUES (?, ?, datetime('now'))",
        (item_id, borrower_id),
    )

    assert home_dashboard.dashboard_summary(db)["lent_out_count"] == 1


def test_recent_items_have_deterministic_id_tiebreak(db):
    first = insert_item(db, title="First", media_type="book")
    second = insert_item(db, title="Second", media_type="dvd")
    db.execute(
        "UPDATE items SET created_at = '2026-01-01 12:00:00' WHERE id IN (?, ?)",
        (first, second),
    )

    recent = home_dashboard.dashboard_summary(db, recent_limit=2)["recent_items"]
    assert [row["id"] for row in recent] == [second, first]


def test_recent_limit_is_bounded(db):
    for number in range(60):
        insert_item(db, title=f"Item {number}", media_type="book")

    assert len(home_dashboard.dashboard_summary(db, recent_limit=500)["recent_items"]) == 50
    assert home_dashboard.dashboard_summary(db, recent_limit=-1)["recent_items"] == []


def test_a_dismissed_item_is_not_counted_as_missing_a_cover(db):
    """Home's tile and Settings' figure must be the same number.

    Before the cover review queue they always agreed. Once a reviewer can mark
    an item "not available", a tile that still counted it would show a
    different number from Settings for what reads as the same thing — and the
    tile is not a link, so nobody could drill in to find out why.
    """
    insert_item(db, title="Still Needs One", media_type="book")
    dismissed = insert_item(db, title="No Cover Exists", media_type="book")
    db.execute("UPDATE items SET cover_review_dismissed = 1 WHERE id = ?",
               (dismissed,))

    assert home_dashboard.dashboard_summary(db)["missing_cover_count"] == 1
