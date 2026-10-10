"""Cross-filter dropdown counts for Browse.

One function, shared by `/browse` and `/api/search`, so the numbers beside the
filter options cannot disagree between the first paint and the first swap. The
filters themselves are declared in `app/browse_filters.py`.
"""

from app import browse_filters
from app.services import lists

# Display names for `items.source` in the Browse Source filter. The dropdown is
# built from the values actually present, so a source missing here still
# appears — under a title-cased fallback — rather than being unreachable.
SOURCE_LABELS = {
    "manual": "Manual",
    "romm": "RomM",
    "komga": "Komga",
    "audiobookshelf": "Audiobookshelf",
    "musicbrainz": "MusicBrainz",
    "hardcover": "Hardcover",
    "igdb": "IGDB",
    "tmdb": "TMDb",
    "issn": "ISSN",
    "openlibrary": "Open Library",
    "oszk": "OSZK NEKTÁR",
    "google": "Google Books",
    "photo_intake": "Photo Intake",
    "store_queue": "Store Queue",
    "csv_import": "CSV Import",
}


def filter_counts(db, values: dict, total: int) -> dict:
    """Cross-filter dropdown counts: each group is build_where minus its own filter.

    `values` is the dict `browse_filters.values_from` produced; `total` is the
    row count for the *unexcluded* where-clause, which every caller has already
    run. Both `/browse` and `/api/search` render their dropdowns from this dict,
    so the numbers cannot disagree between the first paint and the first swap.
    """
    def _count_where(exclude):
        return browse_filters.build_where(values, exclude=exclude)

    type_where, type_params = _count_where("media_type_filter")
    type_counts = {
        row["media_type"]: row["c"]
        for row in db.execute(
            f"SELECT media_type, COUNT(*) as c FROM items_live i {type_where} GROUP BY media_type",
            type_params,
        ).fetchall()
    }
    type_total = sum(type_counts.values())

    own_where, own_params = _count_where("owned")
    own_row = db.execute(
        f"SELECT COALESCE(SUM(i.owned = 1), 0) AS o, COALESCE(SUM({lists.WISHLISTED_SQL}), 0) AS w, "
        f"COALESCE(SUM({lists.NEITHER_SQL}), 0) AS n FROM items_live i {own_where}",
        own_params,
    ).fetchone()
    owned_count, wishlist_count, neither_count = own_row["o"], own_row["w"], own_row["n"]

    loc_where, loc_params = _count_where("location_filter")
    _loc_join = " AND" if loc_where else " WHERE"
    location_counts = {
        row["location_id"]: row["c"]
        for row in db.execute(
            f"SELECT location_id, COUNT(*) as c FROM items_live i {loc_where}"
            f"{_loc_join} location_id IS NOT NULL GROUP BY location_id",
            loc_params,
        ).fetchall()
    }
    no_location_count = db.execute(
        f"SELECT COUNT(*) as c FROM items_live i {loc_where}{_loc_join} location_id IS NULL",
        loc_params,
    ).fetchone()["c"]

    rs_where, rs_params = _count_where("reading_status")
    _rs_join = " AND" if rs_where else " WHERE"
    reading_status_counts = {
        row["reading_status"]: row["c"]
        for row in db.execute(
            f"SELECT reading_status, COUNT(*) as c FROM items_live i {rs_where}"
            f"{_rs_join} reading_status IS NOT NULL AND reading_status != '' "
            "GROUP BY reading_status",
            rs_params,
        ).fetchall()
    }

    src_where, src_params = _count_where("source_filter")
    _src_join = " AND" if src_where else " WHERE"
    source_counts = {
        row["source"]: row["c"]
        for row in db.execute(
            f"SELECT source, COUNT(*) as c FROM items_live i {src_where}"
            f"{_src_join} source IS NOT NULL AND source != '' "
            "GROUP BY source ORDER BY source",
            src_params,
        ).fetchall()
    }

    locations = db.execute(
        "SELECT * FROM locations ORDER BY sort_order, name"
    ).fetchall()

    return {
        "type_counts": type_counts,
        "type_total": type_total,
        "owned_count": owned_count,
        "wishlist_count": wishlist_count,
        "neither_count": neither_count,
        "location_counts": location_counts,
        "no_location_count": no_location_count,
        "reading_status_counts": reading_status_counts,
        "source_counts": source_counts,
        "source_labels": SOURCE_LABELS,
        "locations": locations,
        "filtered_total": total,
        "active_type": values["media_type_filter"],
        "active_owned": values["owned"],
        "active_location": values["location_filter"],
        "active_reading_status": values["reading_status"],
        "active_source": values["source_filter"],
    }
