"""Probable duplicate records, grouped for the Duplicates page.

Two keys, both read through ``items_live`` and computed per request:

- **identifier** (certain) — ``to_isbn13`` of the first of ``isbn``,
  ``isbn10``, ``upc`` it accepts. The permissive helper is deliberate: it is
  what puts a pre-#20 row holding a UPC in ``isbn`` beside its correctly filed
  twin, which ``canonical_isbn_pair`` would reject as a non-ISBN.
- **title_author** (probable) — ``(fold_title(title), first author's
  name_key, media_type)``, only among rows with no identifier at all.

Invariants the page and its tests rest on:

- A group never crosses ``media_type``: a paperback and an ebook of one work
  are Related Media, not duplicates.
- The periodical and music families take no part — their identity lives on
  issue and release records, not on the item row.
- A title group never holds a row that has an identifier: two rows with
  different ISBNs and one title are two editions, the case a
  ``(title, media_type)`` unique index was rejected over.
- An empty fold never groups. ``fold_title`` maps every CJK title to ``""``;
  grouping on it would make one "duplicate" of every such title.
- Nothing is cached and no key is stored (G13, G57). The page is opened
  rarely, and a stored key is a second answer that drifts from the title.

A pair the user called "not duplicates" lives in ``duplicate_dismissals`` as
``(min id, max id)``. A group is hidden while every pair in it is dismissed,
so a third record joining it re-surfaces the group. Dismissal rows outlive a
trashed member (they cascade only on purge) and are read joined through
``items_live`` on both sides, so they never render while either side is in
Trash and are back in force when it is restored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import combinations

from app.config import MUSIC_MEDIA_TYPES, PERIODICAL_MEDIA_TYPES
from app.services import authors, item_merge
from app.services.isbn import to_isbn13
from app.services.title_match import fold_title

MAX_IDS = 50
_EXCLUDED_MEDIA_TYPES = PERIODICAL_MEDIA_TYPES | MUSIC_MEDIA_TYPES
_IDENTIFIER_COLUMNS = ("isbn", "isbn10", "upc")
_COLUMNS = (
    "id", "title", "subtitle", "authors", "isbn", "isbn10", "upc", "media_type",
    "cover_path", "created_at", "publisher", "publish_year", "page_count",
    "description", "series_name", "narrator",
)


@dataclass(frozen=True)
class Member:
    id: int
    title: str
    subtitle: str | None
    authors: str | None
    isbn: str | None
    isbn10: str | None
    upc: str | None
    media_type: str
    cover_path: str | None
    created_at: str | None
    copy_count: int
    rooms: tuple[str, ...]
    tag_count: int
    on_loan: bool
    #: ``(field, value)`` for every ``MERGE_FILLABLE`` field this record has
    #: non-empty, in sorted field order — what a merge could copy from it.
    values: tuple[tuple[str, object], ...]

    @property
    def filled(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.values)


@dataclass(frozen=True)
class Group:
    kind: str  # "identifier" | "title_author"
    key: str
    media_type: str
    members: tuple[Member, ...]

    @property
    def member_ids(self) -> tuple[int, ...]:
        return tuple(m.id for m in self.members)


def identifier_key(row) -> str | None:
    """The 13-digit key of the first identifier ``to_isbn13`` accepts.

    Empties are skipped before the call — ``to_isbn13(None)`` raises. A value
    it rejects (an ASIN in ``isbn``) is skipped and the next column tried.
    """
    for column in _IDENTIFIER_COLUMNS:
        value = row[column]
        if not value:
            continue
        key = to_isbn13(str(value))
        if key:
            return key
    return None


def title_author_key(row) -> tuple[str, str, str] | None:
    """``(fold, first author's name_key, media_type)``, or None on an empty fold."""
    folded = fold_title(row["title"] or "")
    if not folded:
        return None
    parsed = authors.parse(row["authors"])
    return (folded, parsed[0].name_key if parsed else "", row["media_type"])


def _ids_param(ids) -> str:
    # One bound JSON array instead of one placeholder per id: the statement
    # count stays fixed however many members there are, and no group size
    # can reach SQLite's variable limit.
    return json.dumps(sorted(ids))


def _card_facts(db, ids: set[int]) -> tuple[dict, dict, dict, set]:
    """Copies, rooms, tags and loans for the grouped members — four reads."""
    if not ids:
        return {}, {}, {}, set()
    param = _ids_param(ids)
    copies: dict[int, int] = {}
    for row in db.execute(
        "SELECT item_id, COUNT(*) AS n FROM copies_live "
        "WHERE item_id IN (SELECT value FROM json_each(?)) GROUP BY item_id",
        (param,),
    ):
        copies[row["item_id"]] = row["n"]
    rooms: dict[int, list[str]] = {}
    for row in db.execute(
        "SELECT DISTINCT c.item_id, l.name FROM copies_live c "
        "JOIN locations l ON l.id = c.location_id "
        "WHERE c.item_id IN (SELECT value FROM json_each(?)) "
        "ORDER BY c.item_id, l.name COLLATE NOCASE",
        (param,),
    ):
        rooms.setdefault(row["item_id"], []).append(row["name"])
    tags: dict[int, int] = {}
    for row in db.execute(
        "SELECT item_id, COUNT(*) AS n FROM item_tags "
        "WHERE item_id IN (SELECT value FROM json_each(?)) GROUP BY item_id",
        (param,),
    ):
        tags[row["item_id"]] = row["n"]
    loans = item_merge.active_loan_ids(db, sorted(ids))
    return copies, rooms, tags, loans


def _dismissed(db) -> set[tuple[int, int]]:
    return {
        (row["item_a_id"], row["item_b_id"])
        for row in db.execute(
            "SELECT d.item_a_id, d.item_b_id FROM duplicate_dismissals d "
            "JOIN items_live a ON a.id = d.item_a_id "
            "JOIN items_live b ON b.id = d.item_b_id"
        )
    }


def _collect(rows, key_fn) -> dict:
    buckets: dict = {}
    for row in rows:
        key = key_fn(row)
        if key is not None:
            buckets.setdefault(key, []).append(row)
    return {k: v for k, v in buckets.items() if len(v) >= 2}


def _visible(buckets: dict, dismissed: set) -> list[tuple]:
    out = []
    for key, rows in buckets.items():
        rows = sorted(rows, key=lambda r: r["id"])
        ids = [r["id"] for r in rows]
        if all(pair in dismissed for pair in combinations(ids, 2)):
            continue
        out.append((key, rows))
    out.sort(key=lambda kr: ((kr[1][0]["title"] or "").casefold(), kr[1][0]["id"]))
    return out


def groups(db, *, cap: int = 200) -> tuple[list[Group], list[Group], dict]:
    """Identifier groups, title groups, and how many of each fell past ``cap``.

    A fixed number of statements per call, whatever the group count: one
    candidate read, one dismissals read, then four card reads over the
    grouped members only.
    """
    rows = [
        row for row in db.execute(f"SELECT {', '.join(_COLUMNS)} FROM items_live")
        if row["media_type"] not in _EXCLUDED_MEDIA_TYPES
    ]
    keyed = {row["id"]: identifier_key(row) for row in rows}
    by_identifier = _collect(rows, lambda r: (keyed[r["id"]], r["media_type"])
                             if keyed[r["id"]] else None)
    by_title = _collect([r for r in rows if keyed[r["id"]] is None], title_author_key)

    dismissed = _dismissed(db)
    ident = _visible(by_identifier, dismissed)
    title = _visible(by_title, dismissed)
    hidden = {"identifier": max(0, len(ident) - cap),
              "title_author": max(0, len(title) - cap)}
    ident, title = ident[:cap], title[:cap]

    ids = {r["id"] for _, rs in ident + title for r in rs}
    copies, rooms, tags, loans = _card_facts(db, ids)
    fillable = sorted(item_merge.MERGE_FILLABLE)

    def member(row) -> Member:
        return Member(
            id=row["id"], title=row["title"], subtitle=row["subtitle"],
            authors=row["authors"], isbn=row["isbn"], isbn10=row["isbn10"],
            upc=row["upc"], media_type=row["media_type"],
            cover_path=row["cover_path"], created_at=row["created_at"],
            copy_count=copies.get(row["id"], 0),
            rooms=tuple(rooms.get(row["id"], ())),
            tag_count=tags.get(row["id"], 0),
            on_loan=row["id"] in loans,
            values=tuple((f, row[f]) for f in fillable if row[f]),
        )

    def build(kind, found) -> list[Group]:
        return [
            Group(kind=kind,
                  key=key[0] if kind == "identifier" else " / ".join(key[:2]),
                  media_type=rs[0]["media_type"],
                  members=tuple(member(r) for r in rs))
            for key, rs in found
        ]

    return build("identifier", ident), build("title_author", title), hidden


def dismissed_pairs(db, limit: int = 200) -> list[dict]:
    """Dismissed pairs whose two records are both live, newest first."""
    return [
        dict(row)
        for row in db.execute(
            "SELECT d.item_a_id AS a_id, a.title AS a_title, "
            "d.item_b_id AS b_id, b.title AS b_title, d.created_at "
            "FROM duplicate_dismissals d "
            "JOIN items_live a ON a.id = d.item_a_id "
            "JOIN items_live b ON b.id = d.item_b_id "
            "ORDER BY d.created_at DESC, d.item_a_id, d.item_b_id LIMIT ?",
            (limit,),
        )
    ]


def parse_ids(raw, *, exact: int | None = None) -> list[int] | str:
    """Validate a request's ``item_ids``: a list of distinct positive ints.

    Returns the ids ascending, or the refusal message the route shows.
    """
    if not isinstance(raw, list) or not all(
        isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in raw
    ):
        return "Invalid request"
    ids = sorted(set(raw))
    if len(ids) < 2:
        return "Pick at least two records"
    if exact is not None and len(ids) != exact:
        return "Invalid request"
    if len(ids) > MAX_IDS:
        return "Too many records in one group"
    return ids


def dismiss(db, item_ids: list[int]) -> str | None:
    """Write every pair of ``item_ids`` as dismissed; a refusal message or None.

    Runs inside the caller's ``BEGIN IMMEDIATE`` (G18): the liveness read and
    the inserts must see one state. On a refusal nothing has been written,
    and the caller rolls back.
    """
    live = db.execute(
        "SELECT COUNT(*) AS n FROM items_live WHERE id IN (SELECT value FROM json_each(?))",
        (_ids_param(item_ids),),
    ).fetchone()["n"]
    if live != len(set(item_ids)):
        return "One of these records no longer exists — reload the page"
    db.executemany(
        "INSERT OR IGNORE INTO duplicate_dismissals (item_a_id, item_b_id) VALUES (?, ?)",
        list(combinations(sorted(set(item_ids)), 2)),
    )
    return None


def restore(db, a: int, b: int) -> None:
    """Remove one dismissed pair. Runs inside the caller's ``BEGIN IMMEDIATE``."""
    db.execute(
        "DELETE FROM duplicate_dismissals WHERE item_a_id = ? AND item_b_id = ?",
        (min(a, b), max(a, b)),
    )
