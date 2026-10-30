"""The versioned JSON API: `/api/v1`.

- `GET /api/v1/stats` (and `HEAD`) — one document for dashboards: item
  counts, loans, collection value and upcoming releases. A section whose
  feature is off is `null`, never absent, so a template reading
  `value_json.loans.overdue` gets None rather than an error.

Readable with a session cookie or a read-only API token (the bearer branch in
`AuthMiddleware`, `app/main.py`). Every route here declares a `response_model`:
the model, not a service's dict, is the contract the recipes depend on, and
`tests/test_api_v1_stats.py` walks the routes to keep it so. Role first, then
the `api` feature — the order the registry lint checks.
"""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.auth import require_role
from app.currency import get_currency
from app.database import get_db
from app.features import feature_enabled, require_feature
from app.routers.checkouts import count_overdue
from app.services import collection_value, release_radar
from app.services.home_dashboard import dashboard_summary

router = APIRouter(prefix="/api/v1", tags=["v1"])


class MediaTypeCount(BaseModel):
    media_type: str
    total: int
    owned: int
    wishlist: int


class ItemsSection(BaseModel):
    total: int
    owned: int
    wishlist: int
    by_media_type: list[MediaTypeCount]


class LoansSection(BaseModel):
    open: int
    overdue: int


class ValueSection(BaseModel):
    total: float
    currency: str


class UpcomingSection(BaseModel):
    count: int
    next_release_date: str | None


class StatsResponse(BaseModel):
    items: ItemsSection
    loans: LoansSection | None
    value: ValueSection | None
    upcoming: UpcomingSection | None
    generated_at: str


# HEAD is listed so a monitor's HEAD probe gets 200; FastAPI's `get` serves GET only.
@router.api_route("/stats", methods=["GET", "HEAD"], response_model=StatsResponse)
async def stats(
    response: Response,
    _=Depends(require_role("viewer")),
    __=Depends(require_feature("api")),
):
    with get_db() as db:
        summary = dashboard_summary(db, recent_limit=0)
        items = ItemsSection(
            total=summary["total_count"],
            owned=summary["owned_count"],
            wishlist=summary["wishlist_count"],
            by_media_type=[
                MediaTypeCount(
                    media_type=r["media_type"],
                    total=r["item_count"],
                    owned=r["owned_count"] or 0,
                    wishlist=r["wishlist_count"] or 0,
                )
                for r in summary["media_types"]
            ],
        )
        loans = None
        if feature_enabled("lending"):
            loans = LoansSection(open=summary["lent_out_count"], overdue=count_overdue(db))
        value = None
        if feature_enabled("valuation"):
            # Rounded here, not in the shared sum: a float sum of prices
            # reads 65.47999999999999 in any consumer that shows it raw.
            value = ValueSection(total=round(collection_value.owned_total(db), 2),
                                 currency=get_currency().code)
        upcoming = None
        if feature_enabled("release_radar") and feature_enabled("hardcover"):
            # limit=None: VISIBLE_LIMIT is the Upcoming page's render cap, and
            # `count` is a total, not a page size.
            rows = release_radar.visible_rows(db, limit=None)
            upcoming = UpcomingSection(
                count=len(rows),
                next_release_date=release_radar.next_release_date(rows, date.today()),
            )
    # Live counts with no validators: nothing between Shelf and a dashboard
    # may store and replay them.
    response.headers["Cache-Control"] = "no-store"
    return StatsResponse(
        items=items,
        loans=loans,
        value=value,
        upcoming=upcoming,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
