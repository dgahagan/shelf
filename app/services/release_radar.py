"""Release radar: watched series, announced volumes, and the daily digest.

Helpers take the caller's connection. `run_pass` owns its connections: it
opens a short `get_db()` block for each read or write and never holds one
across an `await` (G3, G112). No module-level state (G13). Every read of
`items` goes through `items_live` (G105).

A row is *held* when the library already has the book, owned or wishlisted:
by `hardcover_book_id` anywhere in the library, or by casefolded title within
the watched series (the completeness check's rule, `series.py`). A held row
is never inserted, never digested, and hidden from `/upcoming`.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone

from app.database import get_db, get_setting
from app.services import hardcover, lists, notify

logger = logging.getLogger(__name__)

DEFAULT_CAP = 50
MAX_CAP = 200
# How far back a dated announcement may be and still be recorded.
QUALIFY_DAYS = 30
# Unheld, undismissed rows dated longer ago than this are deleted.
PRUNE_DAYS = 60
WEEK_DAYS = 7
VISIBLE_LIMIT = 500

# A pass ends on these: every further call would be refused too.
_ENDING_OUTCOMES = ("rejected", "rate_limited", "no_credential")


def get_cap(db) -> int:
    """Most watches checked per pass; missing or out-of-range reads as the default."""
    raw = get_setting(db, "release_radar_cap")
    try:
        value = int(raw) if raw else DEFAULT_CAP
    except ValueError:
        return DEFAULT_CAP
    return value if 1 <= value <= MAX_CAP else DEFAULT_CAP


def select_watches(db, cap: int) -> list:
    """Watches whose series still has a live item: never-checked first, then
    oldest check, ties by id. That order is the round-robin."""
    return db.execute(
        "SELECT w.id, w.series_name FROM release_watches w "
        "WHERE EXISTS (SELECT 1 FROM items_live i "
        "WHERE i.series_name = w.series_name COLLATE NOCASE) "
        "ORDER BY w.checked_at IS NOT NULL, w.checked_at ASC, w.id ASC "
        "LIMIT ?",
        (cap,),
    ).fetchall()


def _title_key(title) -> str:
    return (title or "").casefold().strip()


def held_keys(db, series_name: str) -> tuple[set, set]:
    """`(hardcover ids held anywhere, casefolded titles held in this series)`.

    Held means owned or wishlisted; an item that is neither does not hold.
    """
    held = f"(i.owned = 1 OR {lists.WISHLISTED_SQL})"
    ids = {
        r[0] for r in db.execute(
            f"SELECT i.hardcover_book_id FROM items_live i "
            f"WHERE i.hardcover_book_id IS NOT NULL AND {held}"
        ).fetchall()
    }
    titles = {
        _title_key(r[0]) for r in db.execute(
            f"SELECT i.title FROM items_live i "
            f"WHERE i.series_name = ? COLLATE NOCASE AND {held}",
            (series_name,),
        ).fetchall()
    }
    return ids, titles


def _is_held(keys: tuple[set, set], hc_book_id, title) -> bool:
    ids, titles = keys
    return hc_book_id in ids or _title_key(title) in titles


def qualifies(row: dict, today: date) -> bool:
    """A parsed Hardcover row worth recording: dated no more than 30 days
    ago, or undated with a release year of this year or later."""
    raw = row.get("release_date")
    if raw:
        try:
            return date.fromisoformat(raw) >= today - timedelta(days=QUALIFY_DAYS)
        except ValueError:
            return False
    year = row.get("release_year")
    return isinstance(year, int) and year >= today.year


def _safe_cover(url) -> str | None:
    # The CSP allows `img-src https:`; anything else would be a violation at render.
    return url if isinstance(url, str) and url.startswith("https://") else None


def upsert_rows(db, watch_id: int, rows: list[dict]) -> None:
    """Insert new (watch, book) pairs and refresh known ones.

    One statement per row, no read-then-write (G18). The update never touches
    `notified_at`, `reminded_at` or `dismissed`, so a date slip does not
    re-announce and a dismissal survives.
    """
    for r in rows:
        authors = r.get("authors")
        if isinstance(authors, (list, tuple)):
            authors = ", ".join(a for a in authors if a) or None
        db.execute(
            "INSERT INTO upcoming_releases (watch_id, hc_book_id, title, authors, "
            "cover_url, series_position, release_date) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(watch_id, hc_book_id) DO UPDATE SET "
            "title = excluded.title, authors = excluded.authors, "
            "cover_url = excluded.cover_url, series_position = excluded.series_position, "
            "release_date = excluded.release_date",
            (watch_id, r["hardcover_book_id"], r["title"], authors,
             _safe_cover(r.get("cover_url")), r.get("series_position"),
             r.get("release_date")),
        )


def mark_watch(db, watch_id: int, state: str) -> None:
    """Record an answered check: `ok` or `not_found`, stamped now."""
    db.execute(
        "UPDATE release_watches SET check_state = ?, checked_at = datetime('now') "
        "WHERE id = ?",
        (state, watch_id),
    )


def _unheld(db, rows) -> list[dict]:
    keys_by_series: dict = {}
    out = []
    for r in rows:
        series = r["series_name"]
        key = series.casefold()
        if key not in keys_by_series:
            keys_by_series[key] = held_keys(db, series)
        if not _is_held(keys_by_series[key], r["hc_book_id"], r["title"]):
            out.append(dict(r))
    return out


_ROW_SELECT = (
    "SELECT u.id, u.watch_id, u.hc_book_id, u.title, u.authors, u.cover_url, "
    "u.series_position, u.release_date, u.notified_at, u.reminded_at, "
    "w.series_name FROM upcoming_releases u "
    "JOIN release_watches w ON w.id = u.watch_id "
    "WHERE u.dismissed = 0 "
)


def digest_rows(db, today: date) -> tuple[list[dict], list[dict]]:
    """`(new, out_this_week)` among undismissed, unheld rows.

    New is every row never announced. Out this week is an already-announced
    row dated today to today+7 that has had no reminder. A new row dated
    this week is in New only; `stamp` reminds it at the same time.
    """
    rows = _unheld(db, db.execute(
        _ROW_SELECT + "ORDER BY u.release_date IS NULL, u.release_date, "
        "w.series_name COLLATE NOCASE, u.series_position, u.id"
    ).fetchall())
    start, end = today.isoformat(), (today + timedelta(days=WEEK_DAYS)).isoformat()
    new = [r for r in rows if r["notified_at"] is None]
    week = [
        r for r in rows
        if r["notified_at"] is not None and r["reminded_at"] is None
        and r["release_date"] is not None and start <= r["release_date"] <= end
    ]
    return new, week


def in_week(row: dict, today: date) -> bool:
    d = row.get("release_date")
    return d is not None and today.isoformat() <= d <= (today + timedelta(days=WEEK_DAYS)).isoformat()


def _position(pos) -> str:
    if pos is None:
        return ""
    return f" #{int(pos)}" if float(pos).is_integer() else f" #{pos}"


def _line(r: dict) -> str:
    return (f"{r['title']} — {r['series_name']}{_position(r['series_position'])} — "
            f"{r['release_date'] or 'date TBA'}")


def build_digest(new: list[dict], this_week: list[dict]) -> tuple[str, str]:
    """Title and body for one digest. Omits an empty section."""
    n = len(new) + len(this_week)
    title = f"Shelf: {n} release update{'' if n == 1 else 's'}"
    parts = []
    if new:
        parts.append("New:\n" + "\n".join(_line(r) for r in new))
    if this_week:
        parts.append("Out this week:\n" + "\n".join(_line(r) for r in this_week))
    return title, "\n\n".join(parts)


def stamp(db, ids_new: list[int], ids_week: list[int]) -> None:
    """Mark rows announced (`notified_at`) and reminded (`reminded_at`)."""
    for rid in ids_new:
        db.execute("UPDATE upcoming_releases SET notified_at = datetime('now') WHERE id = ?", (rid,))
    for rid in ids_week:
        db.execute("UPDATE upcoming_releases SET reminded_at = datetime('now') WHERE id = ?", (rid,))


def prune(db, today: date) -> int:
    """Delete undismissed rows dated more than 60 days ago. A held row is
    hidden anyway and cannot re-qualify, so its deletion loses nothing."""
    cur = db.execute(
        "DELETE FROM upcoming_releases "
        "WHERE release_date < date(?, ?) AND dismissed = 0",
        (today.isoformat(), f"-{PRUNE_DAYS} days"),
    )
    return cur.rowcount


def visible_rows(db, limit: int | None = VISIBLE_LIMIT) -> list[dict]:
    """The `/upcoming` rows: undismissed, unheld, dated first by date, then
    undated, then by series and position. `limit=None` returns every row."""
    rows = db.execute(
        _ROW_SELECT + "ORDER BY u.release_date IS NULL, u.release_date, "
        "w.series_name COLLATE NOCASE, u.series_position, u.id"
    ).fetchall()
    return _unheld(db, rows)[:limit]


def next_release_date(rows: list[dict], today: date) -> str | None:
    """The first release date on or after `today` among `visible_rows` output,
    which is already ordered dated-first ascending; None if there is none."""
    floor = today.isoformat()
    for r in rows:
        if r["release_date"] and r["release_date"] >= floor:
            return r["release_date"]
    return None


_OUTCOME_TEXT = {
    "rejected": "Hardcover refused the token",
    "rate_limited": "Hardcover rate limit",
    "no_credential": "no Hardcover token",
}


def describe_last_run(stamp_value: str | None, summary: str | None) -> str:
    """The Settings "Last run" text, or `Never`.

    `YYYY-MM-DD HH:MM UTC — N checked[, M not found][, K failed][, stopped: why]`.
    A missing or unreadable summary shows the time alone.
    """
    try:
        when = datetime.fromtimestamp(float(stamp_value), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OverflowError, OSError):
        return "Never"
    try:
        info = json.loads(summary)
        text = f"{when} — {int(info['checked'])} checked"
        if int(info.get("not_found") or 0):
            text += f", {int(info['not_found'])} not found on Hardcover"
        if int(info.get("failed") or 0):
            text += f", {int(info['failed'])} failed (no response from Hardcover)"
        why = _OUTCOME_TEXT.get(info.get("outcome"))
        if why:
            text += f", stopped: {why}"
        return text
    except (TypeError, ValueError, KeyError):
        return when


async def run_pass(token: str, *, cap: int, notify_url: str, notify_format: str,
                   today: date | None = None) -> dict:
    """One daily pass. Returns the summary
    `{checked, not_found, failed, outcome, new, reminded, sent}`.

    `checked` counts watches Hardcover answered (found or not found);
    `failed` counts transport failures, which record nothing so the watch
    keeps its place. `outcome` is `complete`, or the outcome that ended the
    pass early (`rejected`, `rate_limited`, `no_credential`).
    """
    today = today or date.today()
    with get_db() as db:
        watches = [dict(w) for w in select_watches(db, cap)]

    checked = not_found = failed = 0
    outcome = "complete"
    for w in watches:
        result = await hardcover.get_series_upcoming(w["series_name"], token, today=today)
        if result.outcome in _ENDING_OUTCOMES:
            outcome = result.outcome
            break
        if result.outcome == "transport_failed":
            failed += 1
            continue
        checked += 1
        if result.outcome == "no_match":
            not_found += 1
            with get_db() as db:
                mark_watch(db, w["id"], "not_found")
            continue
        with get_db() as db:
            keys = held_keys(db, w["series_name"])
            # Admit and refresh are separate: a new book must qualify and be
            # unheld, but a stored one is refreshed whatever its new date, or a
            # slip past the 30-day window would leave a false future date.
            known = {
                r[0] for r in db.execute(
                    "SELECT hc_book_id FROM upcoming_releases WHERE watch_id = ?",
                    (w["id"],),
                ).fetchall()
            }
            rows = [
                r for r in (result.payload or [])
                if r["hardcover_book_id"] in known
                or (qualifies(r, today)
                    and not _is_held(keys, r["hardcover_book_id"], r["title"]))
            ]
            upsert_rows(db, w["id"], rows)
            mark_watch(db, w["id"], "ok")

    with get_db() as db:
        new, week = digest_rows(db, today)
    ids_new = [r["id"] for r in new]
    ids_week = [r["id"] for r in week] + [r["id"] for r in new if in_week(r, today)]

    sent = False
    deliver = True
    if (new or week) and notify_url:
        title, body = build_digest(new, week)
        sent = bool(await notify.send_notification(notify_url, title, body, notify_format))
        deliver = sent
        if not sent:
            logger.warning("Release digest for %d row(s) was not accepted", len(new) + len(week))
    with get_db() as db:
        if deliver:
            stamp(db, ids_new, ids_week)
        prune(db, today)
    return {"checked": checked, "not_found": not_found, "failed": failed,
            "outcome": outcome, "new": len(new), "reminded": len(week), "sent": sent}
