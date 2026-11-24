"""Period parsing and summary data for the wrap-up preview.

`stats()` (app/routers/pages.py:446-535) is the source of truth for what
"finished" and "top author" mean in this app — this module reproduces those
same predicates, bounded to one month or one year instead of "all time" /
"this calendar year", so a wrap-up can never disagree with /stats:

- finished: ``reading_status = 'read' AND date_finished IS NOT NULL``
  (pages.py:481), same for every media type — the verb (read/watch/play)
  comes from `status_labels(media_type)`, not a second predicate.
- top author: the first entry of the comma-joined `authors` string
  (pages.py:530), counted here over the period's finished items, or over
  the period's additions when nothing was finished.

Every query here reads `items_live`, never `items` (G105) — a trashed item
must never appear in a count or a tile. Every function takes the caller's
already-open connection; nothing here opens its own `get_db()` (G112).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app import i18n
from app.i18n import N_
from app.config import PLAY_LABELS, READ_LABELS, WATCH_LABELS, status_labels

_PERIOD_RE = re.compile(r"^\d{4}(-(0[1-9]|1[0-2]))?$")

# read/watch/play order, matching stats()'s finished_this_year (pages.py:515-519).
_VERB_ORDER = (("read", READ_LABELS), ("watch", WATCH_LABELS), ("play", PLAY_LABELS))

_MONTH_NAMES = (
    N_("January"), N_("February"), N_("March"), N_("April"), N_("May"), N_("June"),
    N_("July"), N_("August"), N_("September"), N_("October"), N_("November"), N_("December"),
)


@dataclass(frozen=True)
class Period:
    kind: str   # "month" | "year"
    key: str    # "2026-09" | "2026" — the value bound into the SQL prefix match
    label: str  # "September 2026" | "2026"


@dataclass(frozen=True)
class Summary:
    finished: list[tuple[int, str]]        # [(count, done_lower), ...], read/watch/play order, zeros dropped
    finished_total: int                     # full period total, never capped
    added_total: int                        # full period total, never capped
    top_author: tuple[str, int] | None
    showing: str | None                     # "finished" | "added" | None
    tiles: list[dict]                       # ≤ cap dicts with id, title, cover_path, media_type


def _current_month_period(today: date) -> Period:
    return Period(
        kind="month",
        key=today.strftime("%Y-%m"),
        label=f"{i18n.gettext(_MONTH_NAMES[today.month - 1])} {today.year}",
    )


def parse_period(raw: str | None, today: date) -> Period:
    """Parse a `?period=` value into a `Period`, falling back to the current month.

    Grammar: ``^\\d{4}(-(0[1-9]|1[0-2]))?$``. Missing, malformed, or a month/year
    after `today` all fall back to the current month — there is no error case.
    """
    if not raw or not _PERIOD_RE.match(raw):
        return _current_month_period(today)

    if len(raw) == 4:
        year = int(raw)
        if year > today.year:
            return _current_month_period(today)
        return Period(kind="year", key=raw, label=raw)

    year_str, month_str = raw.split("-")
    year, month = int(year_str), int(month_str)
    if (year, month) > (today.year, today.month):
        return _current_month_period(today)
    return Period(kind="month", key=raw, label=f"{i18n.gettext(_MONTH_NAMES[month - 1])} {year}")


def summarize(db, period: Period, cap: int = 24) -> Summary:
    """Build the wrap-up `Summary` for `period` from the caller's open connection."""
    prefix_len = 7 if period.kind == "month" else 4
    date_finished_prefix = f"substr(date_finished, 1, {prefix_len}) = ?"
    created_at_prefix = f"substr(created_at, 1, {prefix_len}) = ?"
    key = period.key

    finished_rows = db.execute(
        "SELECT media_type, COUNT(*) as c FROM items_live "
        f"WHERE reading_status = 'read' AND date_finished IS NOT NULL AND {date_finished_prefix} "
        "GROUP BY media_type",
        (key,),
    ).fetchall()

    by_verb_key: dict[str, int] = {}
    for row in finished_rows:
        verb_key = status_labels(row["media_type"]).key
        by_verb_key[verb_key] = by_verb_key.get(verb_key, 0) + row["c"]

    finished = [
        (by_verb_key.get(verb_key, 0), i18n.gettext(labels.done_lower))
        for verb_key, labels in _VERB_ORDER
    ]
    finished = [(count, word) for count, word in finished if count > 0]
    finished_total = sum(by_verb_key.values())

    added_total = db.execute(
        f"SELECT COUNT(*) as c FROM items_live WHERE {created_at_prefix}",
        (key,),
    ).fetchone()["c"]

    if finished_total > 0:
        author_rows = db.execute(
            "SELECT authors, COUNT(*) as c FROM items_live "
            f"WHERE reading_status = 'read' AND date_finished IS NOT NULL AND {date_finished_prefix} "
            "AND authors IS NOT NULL AND TRIM(authors) != '' GROUP BY authors",
            (key,),
        ).fetchall()
    else:
        author_rows = db.execute(
            "SELECT authors, COUNT(*) as c FROM items_live "
            f"WHERE {created_at_prefix} AND authors IS NOT NULL AND TRIM(authors) != '' "
            "GROUP BY authors",
            (key,),
        ).fetchall()

    author_counts: dict[str, int] = {}
    for row in author_rows:
        first = row["authors"].split(",")[0].strip()
        if first:
            author_counts[first] = author_counts.get(first, 0) + row["c"]
    top_author = (
        sorted(author_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        if author_counts else None
    )
    # A "top" author nobody repeats is only the alphabetical tie-break winner
    # (qa-wrapups Observation 2), so it is not shown.
    if top_author and top_author[1] < 2:
        top_author = None

    if finished_total > 0:
        showing = "finished"
        tile_rows = db.execute(
            "SELECT id, title, cover_path, media_type FROM items_live "
            f"WHERE reading_status = 'read' AND date_finished IS NOT NULL AND {date_finished_prefix} "
            "ORDER BY date_finished DESC, id DESC LIMIT ?",
            (key, cap),
        ).fetchall()
    elif added_total > 0:
        showing = "added"
        tile_rows = db.execute(
            "SELECT id, title, cover_path, media_type FROM items_live "
            f"WHERE {created_at_prefix} ORDER BY created_at DESC, id DESC LIMIT ?",
            (key, cap),
        ).fetchall()
    else:
        showing = None
        tile_rows = []

    tiles = [
        {"id": row["id"], "title": row["title"], "cover_path": row["cover_path"],
         "media_type": row["media_type"]}
        for row in tile_rows
    ]

    return Summary(
        finished=finished,
        finished_total=finished_total,
        added_total=added_total,
        top_author=top_author,
        showing=showing,
        tiles=tiles,
    )


def latest_period(db, today: date) -> Period:
    """The newest month, up to `today`'s, in which anything was finished or added.

    The page opens here rather than on the current month, which a household that
    catalogues in bursts usually finds empty (qa-wrapups Observation 1). Falls back
    to the current month when the library has nothing dated at all.
    """
    row = db.execute(
        "SELECT MAX(m) AS m FROM ("
        "SELECT substr(date_finished, 1, 7) AS m FROM items_live "
        "WHERE reading_status = 'read' AND date_finished IS NOT NULL "
        "UNION ALL SELECT substr(created_at, 1, 7) FROM items_live WHERE created_at IS NOT NULL"
        ") WHERE m <= ?",
        (today.strftime("%Y-%m"),),
    ).fetchone()
    return parse_period(row["m"], today)


def earliest_year(db, today: date) -> int:
    """The first year any live item was added or finished; `today`'s year when empty."""
    row = db.execute(
        "SELECT MIN(y) AS y FROM ("
        "SELECT substr(created_at, 1, 4) AS y FROM items_live WHERE created_at IS NOT NULL "
        "UNION ALL SELECT substr(date_finished, 1, 4) FROM items_live WHERE date_finished IS NOT NULL)"
    ).fetchone()
    try:
        return min(int(row["y"]), today.year)
    except (TypeError, ValueError):
        return today.year


def period_options(today: date, first_year: int) -> list[tuple[str, str]]:
    """Picker choices, newest first: each year, then its months up to `today`."""
    options: list[tuple[str, str]] = []
    for year in range(today.year, first_year - 1, -1):
        options.append((str(year), f"{year} — whole year"))
        last_month = today.month if year == today.year else 12
        for month in range(last_month, 0, -1):
            options.append((f"{year}-{month:02d}", f"{_MONTH_NAMES[month - 1]} {year}"))
    return options
