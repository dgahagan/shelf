"""The collection's owned value: one SQL statement, one home.

`owned_total` is the sum the Stats page shows as the current collection
value. It counts what you own (`owned = 1`, #125), reads through
`items_live` so trashed rows do not count, and prefers `manual_value` over
`estimated_value` per item. Items with neither value contribute nothing.

It equals the valuation report's `total_value` (`app/routers/valuation.py`),
which sums the same per-item `COALESCE(manual_value, estimated_value)` over
owned items. The report is left alone; it also adds a per-unpriced-item
average estimate on top, so only its `total_value` figure matches this one.
"""


def owned_total(db) -> float:
    """Sum of the effective value of every live, owned item; 0 when empty."""
    return db.execute(
        "SELECT COALESCE(SUM(COALESCE(manual_value, estimated_value)), 0) as v FROM items_live "
        "WHERE COALESCE(manual_value, estimated_value) IS NOT NULL AND owned = 1"
    ).fetchone()["v"]
