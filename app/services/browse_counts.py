"""Cross-filter dropdown counts for Browse.

One function, shared by `/browse` and `/api/search`, so the numbers beside the
filter options cannot disagree between the first paint and the first swap. The
filters themselves are declared in `app/browse_filters.py`.
"""

from app import browse_filters, i18n
from app.i18n import N_
from app.services import lists

# Display names for `items.source` in the Browse Source filter. The dropdown is
# built from the values actually present, so a source missing here still
# appears — under a title-cased fallback — rather than being unreachable.
SOURCE_LABELS = {
    "manual": N_("Manual"),
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
    "porbase": "PORBASE",
    "bnf": "BnF",
    "neodb": "NeoDB",
    "google": "Google Books",
    "audible_import": N_("Audible import"),
    "photo_intake": N_("Photo Intake"),
    "store_queue": N_("Store Queue"),
    "csv_import": N_("CSV Import"),
}


# Players filter options (#167). One msgid per count, so a language can say
# "zu dritt" rather than "3 Spieler"; the last one also holds larger groups.
PLAYER_LABELS = {
    1: N_("Solo"),
    2: N_("2 players"),
    3: N_("3 players"),
    4: N_("4 players"),
    5: N_("5 players"),
    6: N_("6 players"),
    7: N_("7 players"),
    8: N_("8 players"),
    9: N_("9 players"),
    10: N_("10 or more players"),
}

# Labels name the bucket's own bounds (browse_filters.PLAY_TIME_BUCKETS), so a
# 60-minute game is not offered under two of them.
PLAY_TIME_LABELS = {
    "short": N_("Up to 30 min"),
    "medium": N_("31–60 min"),
    "long": N_("61–120 min"),
    "epic": N_("Over 2 hours"),
}


def source_label(key: str) -> str:
    """An `items.source` value as a person reads it — the Browse label, or
    the same title-cased fallback the Source dropdown uses."""
    label = SOURCE_LABELS.get(key)
    return i18n.gettext(label) if label else key.replace("_", " ").title()


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

    # Whether the board game selects render at all: decided over the whole
    # library, not the filtered rows, so a swap that narrows to books keeps
    # the select in the page (with no options) instead of leaving a stale copy.
    has_players, has_play_time = db.execute(
        "SELECT EXISTS(SELECT 1 FROM items_live WHERE min_players IS NOT NULL), "
        "EXISTS(SELECT 1 FROM items_live WHERE media_type = 'board_game' "
        "AND duration_mins IS NOT NULL)"
    ).fetchone()
    player_counts = _player_counts(db, _count_where("players"))
    play_time_counts = _play_time_counts(db, _count_where("play_time"))

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
        # Translated here, not with _() in the template: its fallback there is a
        # query-string value, and _() would %-format and mark it safe.
        "source_labels": {key: i18n.gettext(label) for key, label in SOURCE_LABELS.items()},
        "locations": locations,
        "filtered_total": total,
        "active_type": values["media_type_filter"],
        "active_owned": values["owned"],
        "active_location": values["location_filter"],
        "active_reading_status": values["reading_status"],
        "active_source": values["source_filter"],
        "player_counts": player_counts,
        "has_player_filter": bool(has_players),
        "has_play_time_filter": bool(has_play_time),
        "play_time_counts": play_time_counts,
        "player_labels": {n: i18n.gettext(label) for n, label in PLAYER_LABELS.items()},
        "play_time_labels": {k: i18n.gettext(label) for k, label in PLAY_TIME_LABELS.items()},
        "active_players": values["players"],
        "active_play_time": values["play_time"],
    }


def _player_counts(db, where_params) -> dict[int, int]:
    """{player count: games playable with that many}, for the Players filter.

    Counted in Python over the (min, max) pairs: a game for 2-4 counts towards
    2, 3 and 4, which one GROUP BY cannot express. Only counts that some game
    reaches are offered, up to `MAX_PLAYERS_OPTION`, which also holds every
    larger group. Empty when no item has a player count, and the template
    then leaves the filter out.
    """
    where, params = where_params
    join = " AND" if where else " WHERE"
    rows = db.execute(
        f"SELECT i.min_players AS lo, COALESCE(i.max_players, i.min_players) AS hi "
        f"FROM items_live i {where}{join} i.min_players IS NOT NULL",
        params,
    ).fetchall()
    top = browse_filters.MAX_PLAYERS_OPTION
    counts: dict[int, int] = {}
    for row in rows:
        lo, hi = max(1, row["lo"]), max(row["lo"], row["hi"])
        # A game for 12-20 belongs under the top option even though its range
        # starts above it, as the filter (`max >= top`) already says.
        for n in range(min(lo, top), min(hi, top) + 1):
            counts[n] = counts.get(n, 0) + 1
    return dict(sorted(counts.items()))


def _play_time_counts(db, where_params) -> dict[str, int]:
    """{bucket: board games in it}, in bucket order; empty with no play times."""
    where, params = where_params
    join = " AND" if where else " WHERE"
    cases = []
    case_params: list = []
    for key, (low, high) in browse_filters.PLAY_TIME_BUCKETS.items():
        cond = []
        if low is not None:
            cond.append("i.duration_mins >= ?")
            case_params.append(low)
        if high is not None:
            cond.append("i.duration_mins <= ?")
            case_params.append(high)
        cases.append(f"COALESCE(SUM({' AND '.join(cond)}), 0) AS {key}")
    row = db.execute(
        f"SELECT COUNT(*) AS total, {', '.join(cases)} FROM items_live i {where}{join} "
        "i.media_type = 'board_game' AND i.duration_mins IS NOT NULL",
        case_params + params,
    ).fetchone()
    if not row["total"]:
        return {}
    return {key: row[key] for key in browse_filters.PLAY_TIME_BUCKETS}
