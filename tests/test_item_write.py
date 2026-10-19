"""The single item write path — the structural fix for G25 — and, since
issue #54, the single value stage and update funnel that sit on it.

`INSERT INTO items` existed at 13 sites, so adding a column to `items` meant
auditing all 13 and deciding capture-or-gap at each. G25's own Verify line
said to retire the entry if the count ever dropped to 1-2; these tests are
what hold it there.
"""

import ast
import re
import sqlite3
from pathlib import Path

import pytest

from app.database import get_db
from app.services.item_write import (
    READING_STATUSES,
    InvalidIsbn,
    InvalidOwned,
    InvalidReadingStatus,
    InvalidWishlisted,
    ItemValueError,
    UnknownLocationError,
    UnknownMediaType,
    UnknownPlatform,
    insert_item,
    item_columns,
    promote_wishlisted,
    reset_column_cache,
    update_item_fields,
    update_items_fields,
)
from tests.conftest import _insert_item, _insert_location

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"

#: Every raw `UPDATE items SET` outside item_write.py must be system-managed
#: (never a user-typed value) and named here — repo-relative path -> a set of
#: SET-clause substrings that identify each legitimate statement in that
#: file. Derived from `grep -rn "UPDATE items SET" app/ --include=*.py`
#: outside item_write.py (31 hits at the time of writing); each entry below
#: was read at its call site and carries a one-line reason. A dynamic
#: `f"UPDATE items SET {...}"` matches nothing here and is therefore always a
#: violation.
RAW_UPDATE_ALLOWLIST: dict[str, set[str]] = {
    # Cover pipeline (download/upload/remove/sync fallback) — cover_path is
    # never typed by a user, only fetched or uploaded as an image.
    "app/routers/items_covers.py": {
        "cover_path = ?",
        # Removing a cover also clears any "not available" verdict, so the item
        # returns to the review queue — a flag set by a button, never typed.
        "cover_path = NULL, cover_edition_isbn = NULL, cover_edition_language = NULL, cover_review_dismissed = 0, ",
    },
    "app/routers/items_common.py": {"cover_path = ?"},
    # The cover review queue's select/upload verbs — the same fetched-or-
    # uploaded image path as every other entry here, never a typed value.
    "app/routers/cover_review_actions.py": {
        "cover_path = ?",
        # The "not available" verdict: a flag set by a button, not a value a
        # user types into a field.
        "cover_review_dismissed = 1, ",
    },
    "app/routers/items.py": {
        "cover_path = ?",
        # Synopsis fetch: backfills a missing description from a provider
        # lookup, not a user edit — user edits go through item_write.
        "description = ?, updated_at",
    },
    "app/routers/store.py": {"cover_path = ?"},
    "app/routers/items_catalog.py": {"cover_path = ?"},
    # Photo-intake confirm downloads a disc/game cover directly rather than
    # through the cover queue, which stays book-only (G29).
    "app/routers/intake.py": {"cover_path = ?"},
    "app/routers/hardcover.py": {
        "cover_path = ?",
        # IDs Hardcover assigns after a sync match, not user input.
        "hardcover_book_id = ?, hardcover_user_book_id = ?",
    },
    "app/services/audiobookshelf.py": {"cover_path = ?"},
    "app/services/archive.py": {"cover_path = ?"},
    # ISBNdb price lookup for the insurance valuation report — a computed
    # estimate, not a user-entered value.
    "app/routers/valuation.py": {"estimated_value = ?, value_updated_at"},
    # Cascade when a location is deleted: clears the now-dangling FK on every
    # item that pointed at it — not a per-item user edit.
    "app/routers/locations.py": {"location_id = NULL WHERE location_id"},
    # Same cascade shape for a deleted game platform.
    "app/routers/platforms.py": {"platform = NULL WHERE platform"},
    # Series rename/disband is keyed by series *name*, a bulk system cascade
    # over every item in the group, not a single item's user-edited field.
    "app/routers/series.py": {
        "series_name = ? WHERE series_name",
        "series_name = NULL WHERE series_name",
    },
    # Startup migrations: one-time data repairs replayed from schema_version,
    # not request-path writes of a user-supplied value.
    "app/database.py": {
        "upc = '0' || upc",
        "upc = isbn, isbn = NULL, isbn10 = NULL",
        "language = CASE",
        # _retire_kids_book's row rewrite: a one-time vocabulary repair at
        # boot, not a request-path write of a user-supplied value. The
        # NULLIFs collapse a blank identifier so the row stops occupying
        # the single '' slot UNIQUE(isbn, media_type) allows.
        "media_type = 'book', isbn = NULLIF",
        # The same step's ownership raise on a merge. An owned kids book
        # merging into an unowned twin must not silently become a wish;
        # the value is the step's own literal 1, never user input.
        "owned = 1 WHERE id = ?",
    },
}


#: The `item_copies` analogue of RAW_UPDATE_ALLOWLIST — same shape, same
#: G53 comment-stripping guard below. After T2 moved all six raw write sites
#: in `app/` onto `item_copies.insert_copy()`/`update_copy()`, no raw
#: `UPDATE item_copies SET` remains outside `item_copies.py` at all — this
#: empty dict is the correct and intended state, not an unfinished stub. A
#: future raw update site earns an entry here only under the same bar
#: RAW_UPDATE_ALLOWLIST holds for `items`: system-managed, never a
#: user-typed value, read at its call site with a one-line reason.
RAW_COPY_UPDATE_ALLOWLIST: dict[str, set[str]] = {}


#: The one module allowed to write item_copies rows, as a repository-relative
#: path. Matched exactly rather than by basename: `path.name` exempts *any*
#: file called item_copies.py, so a router or service of that name could carry
#: raw INSERT/UPDATE statements and both guards would wave it through — which
#: is what M1 was.
ITEM_COPIES_MODULE = "app/services/item_copies.py"

#: The `deleted_at` assignment guard's pattern, shared by the guard and its
#: own bypass pins so the two cannot drift apart.
#:
#: `\s*` around the underscore is not decoration. `_normalised_source` joins
#: physical lines with a **space**, which is right for a statement split
#: across adjacent string literals the way M1's was
#: (`"INSERT INTO " "item_copies ..."` — SQL tokens are space-separated
#: anyway). It is wrong for a split *inside an identifier*: Python
#: concatenates `"deleted" "_at = ..."` into `deleted_at = ...`, but the
#: buffer holds `deleted _at = ...` and a `deleted_at` needle sails past it.
#: Found while writing the bypass pin below, which is what the pin is for.
#:
#: What it still does not defend: a split at some *other* character
#: (`"delet" "ed_at"`). Defeating that needs a whitespace-free buffer with its
#: own offset map, which is more machinery than the risk earns — the guard is
#: a tripwire against an ordinary fifth writer, not against someone
#: deliberately hiding one.
DELETED_AT_ASSIGNMENT = re.compile(r"deleted\s*_\s*at\s*=")


def _raw_update_hits(path: Path, needle: str = "UPDATE items SET",
                     flags: int = 0) -> list[tuple[int, str]]:
    """(line_number, clause_tail) for every occurrence of `needle` in `path`.

    Comment-only lines are dropped first (G53 — a comment quoting the
    construct must not trip this guard). Double-quote characters — the only
    Python string delimiter this codebase uses for SQL text — are then
    stripped before joining lines with a single space, so a statement split
    across adjacent string literals (`"UPDATE items SET cover_path = ? "`
    `"WHERE id = ?"` on consecutive lines, the way audiobookshelf.py used to
    write its statement) or a triple-quoted multi-line string (the
    migrations in database.py) still reads as one fragment. `line_number` is
    where the `needle` text itself starts; `clause_tail` is enough of what
    follows to test allowlist substrings against. `needle` defaults to the
    `items` guard's construct; the `item_copies` guard passes
    `"UPDATE item_copies SET"` instead.
    """
    buf, line_for = _normalised_source(path)
    hits = []
    for m in re.finditer(re.escape(needle), buf, flags):
        hits.append((line_for(m.start()), buf[m.end():m.end() + 300]))
    return hits


def _normalised_source(path: Path):
    """The comment-stripped, quote-collapsed buffer, and an offset→line map.

    Split out of `_raw_update_hits` so a guard that needs a real **regex**
    rather than a literal needle scans exactly the same bytes. `_raw_update_hits`
    re-escapes its needle, so it cannot express one; the `deleted_at` guard
    below has to. Sharing the buffer is what keeps G53 (a comment quoting the
    construct is not a write) and the adjacent-literal rule true of both.
    """
    lines = path.read_text().splitlines()
    buf_parts: list[str] = []
    offsets: list[tuple[int, int]] = []
    pos = 0
    for i, raw_line in enumerate(lines, 1):
        if raw_line.lstrip().startswith("#"):
            continue
        collapsed = re.sub(r"\s+", " ", raw_line.replace('"', "")).strip()
        if not collapsed:
            continue
        offsets.append((pos, i))
        buf_parts.append(collapsed)
        pos += len(collapsed) + 1  # +1 for the joining space
    buf = " ".join(buf_parts)

    def _line_for(offset: int) -> int:
        line_no = offsets[0][1] if offsets else 1
        for start, ln in offsets:
            if start > offset:
                break
            line_no = ln
        return line_no

    return buf, _line_for


def _enclosing_function(path: Path, line_no: int) -> str | None:
    """The name of the innermost function containing `line_no`, via `ast`.

    Returns `None` for a line at module level. Used by the `deleted_at` guard
    to say not merely *which file* writes the column but *which function* — the
    claim the design makes is "exactly four functions", and a file-level pin
    would pass a fifth writer added to either module.
    """
    tree = ast.parse(path.read_text())
    best: tuple[int, str] | None = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(node, "end_lineno", None)
        if end is None or not (node.lineno <= line_no <= end):
            continue
        # Innermost wins: the deepest node whose span contains the line.
        if best is None or node.lineno > best[0]:
            best = (node.lineno, node.name)
    return best[1] if best else None


class TestSingleWritePath:
    def test_only_item_write_inserts_items(self):
        """The gate that keeps G25 retired."""
        offenders = []
        for path in APP_DIR.rglob("*.py"):
            if path.name == "item_write.py":
                continue
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"INSERT\s+INTO\s+items\b", line, re.I):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{i}")
        assert not offenders, (
            "Item rows must be created through "
            "app.services.item_write.insert_item(), not raw SQL:\n  "
            + "\n  ".join(offenders)
        )

    def test_item_write_holds_exactly_one_insert(self):
        src = (APP_DIR / "services" / "item_write.py").read_text()
        # The module docstring mentions the statement; count real code only.
        code = "\n".join(
            l for l in src.splitlines() if not l.lstrip().startswith("#")
        )
        statements = re.findall(r'f"INSERT INTO items', code)
        assert len(statements) == 1

    def test_item_write_holds_exactly_one_update(self):
        """The update funnel is one statement too — `update_item_fields` and
        `update_items_fields` share the builder, so the value stage cannot be
        bypassed by one of them drifting."""
        src = (APP_DIR / "services" / "item_write.py").read_text()
        code = "\n".join(
            l for l in src.splitlines() if not l.lstrip().startswith("#")
        )
        statements = re.findall(r'f"UPDATE items SET', code)
        assert len(statements) == 1

    def test_only_item_write_updates_user_fields(self):
        """The value-stage counterpart to `test_only_item_write_inserts_items`.

        A raw `UPDATE items SET` outside item_write.py that is not in
        RAW_UPDATE_ALLOWLIST is a value-funnel bypass — exactly the hole
        issue #54's write funnel exists to close (G60)."""
        offenders = []
        for path in APP_DIR.rglob("*.py"):
            if path.name == "item_write.py":
                continue
            rel = str(path.relative_to(REPO_ROOT))
            allowed = RAW_UPDATE_ALLOWLIST.get(rel, set())
            for line_no, clause in _raw_update_hits(path):
                if not any(sub in clause for sub in allowed):
                    offenders.append(f"{rel}:{line_no}")
        assert not offenders, (
            "Raw `UPDATE items SET` outside item_write.py must be "
            "system-managed and listed in RAW_UPDATE_ALLOWLIST — "
            "app/services/item_write.py holds the one user-value update "
            "path:\n  " + "\n  ".join(offenders)
        )

    def test_raw_update_allowlist_has_no_stale_entries(self):
        """Every allowlist entry must still match at least one real hit — an
        entry the code no longer produces is silently over-permissive."""
        clauses_by_path: dict[str, list[str]] = {}
        for path in APP_DIR.rglob("*.py"):
            if path.name == "item_write.py":
                continue
            hits = _raw_update_hits(path)
            if hits:
                rel = str(path.relative_to(REPO_ROOT))
                clauses_by_path[rel] = [clause for _, clause in hits]

        stale = []
        for rel, substrings in RAW_UPDATE_ALLOWLIST.items():
            clauses = clauses_by_path.get(rel, [])
            for sub in substrings:
                if not any(sub in c for c in clauses):
                    stale.append(f"{rel}: {sub!r}")
        assert not stale, (
            "Stale RAW_UPDATE_ALLOWLIST entries (no matching hit found) — "
            "remove them:\n  " + "\n  ".join(stale)
        )

    def test_only_item_copies_module_inserts_copies(self):
        """The `item_copies` analogue of test_only_item_write_inserts_items.

        `app/database.py` is allowlisted by path rather than by substring —
        unlike RAW_UPDATE_ALLOWLIST it need not be, since it holds exactly
        one such statement: migration 26's backfill, which runs before any
        application code (including this funnel) is importable. See
        item_copies.py's module docstring."""
        offenders = []
        for path in APP_DIR.rglob("*.py"):
            rel = str(path.relative_to(REPO_ROOT))
            if rel in (ITEM_COPIES_MODULE, "app/database.py"):
                continue
            # Scanned through the same comment-stripped, quote-collapsed buffer
            # the update guard uses, not line by line: a statement split across
            # adjacent string literals ("INSERT INTO " "item_copies (...)")
            # reads as one fragment there and as two harmless lines here. M1.
            for line_no, _ in _raw_update_hits(path, "INSERT INTO item_copies", re.I):
                offenders.append(f"{rel}:{line_no}")
        assert not offenders, (
            "item_copies rows must be created through "
            "app.services.item_copies.insert_copy(), not raw SQL:\n  "
            + "\n  ".join(offenders)
        )

    def test_only_item_copies_module_deletes_copies(self):
        """The `item_copies` analogue of test_only_item_copies_module_inserts_copies,
        for the delete arm of the funnel.

        No migration removes item_copies rows — migration 26 only backfills
        with an `INSERT ... SELECT` — so unlike the insert guard above,
        `app/database.py` earns no exemption here; verified by reading, not
        assumed. Only ITEM_COPIES_MODULE is exempt, and by repository-relative
        path rather than by `Path.name` (G88): a same-named file anywhere else
        under `app/` — a future router, say — must not walk through this the
        way M1's insert/update bypasses did. Scanned through the same
        comment-stripped, quote-collapsed buffer as the other two guards
        (G53), so neither an explanatory comment nor a statement split across
        adjacent string literals produces a false result either way."""
        offenders = []
        for path in APP_DIR.rglob("*.py"):
            rel = str(path.relative_to(REPO_ROOT))
            if rel == ITEM_COPIES_MODULE:
                continue
            for line_no, _ in _raw_update_hits(path, "DELETE FROM item_copies", re.I):
                offenders.append(f"{rel}:{line_no}")
        assert not offenders, (
            "item_copies rows must be removed through the delete arm of "
            "app.services.item_copies (purge_copy / delete_copies_for_item), "
            "not raw SQL:\n  "
            + "\n  ".join(offenders)
        )

    def test_only_item_copies_module_updates_copies(self):
        """The `item_copies` analogue of test_only_item_write_updates_user_fields."""
        offenders = []
        for path in APP_DIR.rglob("*.py"):
            rel = str(path.relative_to(REPO_ROOT))
            if rel == ITEM_COPIES_MODULE:
                continue
            allowed = RAW_COPY_UPDATE_ALLOWLIST.get(rel, set())
            for line_no, clause in _raw_update_hits(path, "UPDATE item_copies SET"):
                if not any(sub in clause for sub in allowed):
                    offenders.append(f"{rel}:{line_no}")
        assert not offenders, (
            "Raw `UPDATE item_copies SET` outside item_copies.py must be "
            "system-managed and listed in RAW_COPY_UPDATE_ALLOWLIST — "
            "app/services/item_copies.py holds the one write path:\n  "
            + "\n  ".join(offenders)
        )

    def test_raw_copy_update_allowlist_has_no_stale_entries(self):
        """The `item_copies` analogue of
        test_raw_update_allowlist_has_no_stale_entries.

        Meaningful even though RAW_COPY_UPDATE_ALLOWLIST is currently empty:
        the loop below still walks every file under `app/` and computes its
        real hits before comparing, rather than short-circuiting on the dict
        being empty — so an entry added here later without a matching hit is
        caught exactly as it would be for RAW_UPDATE_ALLOWLIST."""
        clauses_by_path: dict[str, list[str]] = {}
        for path in APP_DIR.rglob("*.py"):
            rel = str(path.relative_to(REPO_ROOT))
            if rel == ITEM_COPIES_MODULE:
                continue
            hits = _raw_update_hits(path, "UPDATE item_copies SET")
            if hits:
                clauses_by_path[rel] = [clause for _, clause in hits]

        stale = []
        for rel, substrings in RAW_COPY_UPDATE_ALLOWLIST.items():
            clauses = clauses_by_path.get(rel, [])
            for sub in substrings:
                if not any(sub in c for c in clauses):
                    stale.append(f"{rel}: {sub!r}")
        assert not stale, (
            "Stale RAW_COPY_UPDATE_ALLOWLIST entries (no matching hit found) "
            "— remove them:\n  " + "\n  ".join(stale)
        )

    def test_the_copy_module_is_exempted_by_path_not_by_basename(self):
        """M1's first bypass: `path.name == "item_copies.py"` exempted any file
        of that name anywhere under app/, so a router called item_copies.py
        could carry raw statements and both guards stayed green."""
        assert ITEM_COPIES_MODULE == "app/services/item_copies.py"
        assert (REPO_ROOT / ITEM_COPIES_MODULE).is_file()
        # The exemption must not match a same-named file in another package.
        assert "app/routers/item_copies.py" != ITEM_COPIES_MODULE

    def test_deleted_at_is_written_by_exactly_four_functions(self):
        """The Trash twin of the insert/update source pins.

        `deleted_at` is the column the whole soft-delete program rests on, and
        a fifth writer anywhere under `app/` would put a row into Trash
        without the demote, the seam settle or the guarded rowcount that make
        the collision rules hold. The claim is about **functions**, not files:
        a file-level pin would wave through a new writer added to either
        module, which is exactly where one would be added.

        Behaviour, not only spelling — both funnels also refuse `deleted_at`
        as a caller-supplied field name (`_MANAGED` / `_MANAGED_ON_UPDATE` in
        each module), so the column cannot be reached through
        `update_item_fields(db, id, {"deleted_at": ...})` either. Those are
        pinned by the managed-column parametrisations below.
        """
        pattern = DELETED_AT_ASSIGNMENT
        expected = {
            "app/services/item_write.py": {"trash_item", "restore_item"},
            "app/services/item_copies.py": {"trash_copy", "restore_copy"},
        }
        found: dict[str, list[str]] = {}
        for path in APP_DIR.rglob("*.py"):
            rel = str(path.relative_to(REPO_ROOT))
            buf, line_for = _normalised_source(path)
            for m in pattern.finditer(buf):
                line_no = line_for(m.start())
                fn = _enclosing_function(path, line_no)
                found.setdefault(rel, []).append(f"{fn} ({rel}:{line_no})")

        assert set(found) == set(expected), (
            "`deleted_at` is assigned outside the two funnel modules — it may "
            "be written only by item_write.trash_item/restore_item and "
            "item_copies.trash_copy/restore_copy:\n  "
            + "\n  ".join(
                f"{rel}: {hits}" for rel, hits in sorted(found.items())
                if rel not in expected
            )
        )
        for rel, names in expected.items():
            hits = found[rel]
            assert len(hits) == 2, (
                f"{rel} holds {len(hits)} `deleted_at` assignments, expected "
                f"2 (one per funnel function): {hits}"
            )
            enclosing = {h.split(" (")[0] for h in hits}
            assert enclosing == names, (
                f"{rel}'s `deleted_at` assignments must live in {sorted(names)}, "
                f"found {sorted(enclosing)}"
            )

    def test_the_deleted_at_guard_is_exempted_by_path_not_by_basename(self, tmp_path):
        """G88's bypass, checked for this guard rather than assumed from the
        others: the exemption is a repo-relative path, so a same-named file in
        another package must still be seen."""
        fake_pkg = tmp_path / "services"
        fake_pkg.mkdir()
        impostor = fake_pkg / "item_write.py"
        impostor.write_text(
            'def sneak(db, item_id):\n'
            '    db.execute("UPDATE items SET deleted_at = NULL WHERE id = ?")\n'
        )
        buf, line_for = _normalised_source(impostor)
        hits = list(DELETED_AT_ASSIGNMENT.finditer(buf))
        assert len(hits) == 1
        # Seen, and attributed to its real enclosing function — not waved
        # through because the basename matches an exempt module.
        assert _enclosing_function(impostor, line_for(hits[0].start())) == "sneak"
        assert str(impostor).endswith("item_write.py")
        assert "app/services/item_write.py" not in str(impostor)

    def test_the_deleted_at_guard_sees_an_adjacent_literal_split(self, tmp_path):
        """The other half of G53/M1: a statement split across adjacent string
        literals must not hide the assignment."""
        split = tmp_path / "split.py"
        split.write_text(
            'def sneak(db):\n'
            '    db.execute(\n'
            '        "UPDATE items SET deleted"\n'
            '        "_at = datetime(\'now\') WHERE id = ?"\n'
            '    )\n'
        )
        buf, _ = _normalised_source(split)
        assert DELETED_AT_ASSIGNMENT.search(buf), (
            "the normalised buffer must join adjacent literals, or a writer "
            "can split the column name across two of them and vanish"
        )

        # G53 still holds the other way: a comment is not a write.
        quoted = tmp_path / "quoted.py"
        quoted.write_text("# deleted_at = datetime('now') is described here\n")
        buf2, _ = _normalised_source(quoted)
        assert not DELETED_AT_ASSIGNMENT.search(buf2)

    def test_items_rows_are_deleted_by_exactly_three_functions(self):
        """The permanent-delete twin of the `deleted_at` pin.

        After soft-delete-trash T4 every user-facing delete moves a row to
        Trash. Three functions still remove an `items` row for real:
        `merge_items` (the husk, after its children are reparented),
        `_retire_kids_book` (a twin folded into its book), and
        `trash.purge_item` (Trash's Delete permanently). A fourth is a new way
        to lose data and should be a decision, not a drive-by — so the claim
        is about functions, not files. Scanned through the normalised buffer
        (G53: the explanatory comment at `items.py` that spells the statement
        is not a delete; a docstring would be).
        """
        pattern = re.compile(r"DELETE\s+FROM\s+items\b", re.I)
        expected = {
            ("app/routers/items.py", "merge_items"),
            ("app/database.py", "_retire_kids_book"),
            ("app/services/trash.py", "purge_item"),
        }
        found = set()
        for path in APP_DIR.rglob("*.py"):
            rel = str(path.relative_to(REPO_ROOT))
            buf, line_for = _normalised_source(path)
            for m in pattern.finditer(buf):
                found.add((rel, _enclosing_function(path, line_for(m.start()))))
        assert found == expected, (
            "`DELETE FROM items` may live only in merge_items, "
            "_retire_kids_book and trash.purge_item — found: "
            + ", ".join(f"{rel}::{fn}" for rel, fn in sorted(found, key=str))
        )

    def test_the_items_delete_guard_is_exempted_by_path_not_by_basename(self, tmp_path):
        """G88: a same-named file elsewhere is attributed by its real path, so
        a `trash.py` in another package gets no free pass."""
        fake_pkg = tmp_path / "services"
        fake_pkg.mkdir()
        impostor = fake_pkg / "trash.py"
        impostor.write_text(
            'def purge_item(db, item_id):\n'
            '    db.execute("DELETE FROM items WHERE id = ?", (item_id,))\n'
        )
        buf, line_for = _normalised_source(impostor)
        hits = list(re.finditer(r"DELETE\s+FROM\s+items\b", buf))
        assert len(hits) == 1
        assert "app/services/trash.py" not in str(impostor)

    def test_the_items_delete_guard_sees_an_adjacent_literal_split(self, tmp_path):
        split = tmp_path / "split.py"
        split.write_text(
            'def sneak(db):\n'
            '    db.execute(\n'
            '        "DELETE FROM "\n'
            '        "items WHERE id = ?"\n'
            '    )\n'
        )
        buf, line_for = _normalised_source(split)
        m = re.search(r"DELETE\s+FROM\s+items\b", buf)
        assert m, "adjacent literals must join, or a delete can hide across two"
        assert _enclosing_function(split, line_for(m.start())) == "sneak"

    def test_insert_detection_sees_adjacent_string_literals(self, tmp_path):
        """M1's second bypass: the insert guard scanned one physical line at a
        time, so a statement split across adjacent literals was invisible to it
        while the update guard's consolidated scanner saw it."""
        split = tmp_path / "split.py"
        split.write_text(
            'db.execute(\n'
            '    "INSERT INTO "\n'
            '    "item_copies (item_id, copy_number) VALUES (?, ?)",\n'
            ')\n'
        )
        assert _raw_update_hits(split, "INSERT INTO item_copies", re.I)

        lowered = tmp_path / "lowered.py"
        lowered.write_text('db.execute("insert into item_copies (item_id) VALUES (?)")\n')
        assert _raw_update_hits(lowered, "INSERT INTO item_copies", re.I)

        # G53 still holds: a comment quoting the construct is not a write.
        quoted = tmp_path / "quoted.py"
        quoted.write_text("# INSERT INTO item_copies is described here, not run\n")
        assert not _raw_update_hits(quoted, "INSERT INTO item_copies", re.I)


class TestInsertItem:
    def test_returns_the_new_id(self, db):
        item_id = insert_item(db, title="Dune")
        assert isinstance(item_id, int)
        row = db.execute("SELECT title FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["title"] == "Dune"

    def test_accepts_dict_kwargs_or_both(self, db):
        a = insert_item(db, {"title": "A", "isbn": "9780000000026"})
        b = insert_item(db, title="B", isbn="9780000000002")
        c = insert_item(db, {"title": "C"}, isbn="9780000000033")
        for item_id, isbn in ((a, "9780000000026"), (b, "9780000000002"), (c, "9780000000033")):
            row = db.execute("SELECT isbn FROM items WHERE id = ?", (item_id,)).fetchone()
            assert row["isbn"] == isbn

    def test_kwargs_win_over_the_dict(self, db):
        item_id = insert_item(db, {"title": "from dict"}, title="from kwarg")
        row = db.execute("SELECT title FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["title"] == "from kwarg"

    def test_omitted_columns_take_their_schema_defaults(self, db):
        """Defaults live in SCHEMA alone — not restated here, not in 13 sites."""
        item_id = insert_item(db, title="Bare")
        row = db.execute(
            "SELECT media_type, source, owned, created_at, updated_at "
            "FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        assert row["media_type"] == "book"
        assert row["source"] == "manual"
        assert row["owned"] == 1
        assert row["created_at"] and row["updated_at"]

    def test_explicit_none_is_stored_not_defaulted(self, db):
        item_id = insert_item(db, title="Explicit", publisher=None)
        row = db.execute("SELECT publisher FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["publisher"] is None


class TestLoudFailures:
    def test_unknown_field_raises(self, db):
        """The failure G25 describes, inverted: a typo must not be dropped."""
        with pytest.raises(ValueError, match="not on the items table"):
            insert_item(db, title="X", publsher="typo")

    def test_error_names_the_offending_field_and_points_at_g1(self, db):
        with pytest.raises(ValueError) as exc:
            insert_item(db, title="X", nonexistent_column=1)
        message = str(exc.value)
        assert "nonexistent_column" in message
        assert "SCHEMA and MIGRATIONS" in message

    @pytest.mark.parametrize("managed", ["id", "deleted_at"])
    def test_managed_columns_are_refused(self, db, managed):
        """`deleted_at` joins `id` here so "exactly four writers" is true of
        behaviour, not only of the spelling the source pin greps for: the
        funnel builds its column list from caller-supplied names, so without
        this an insert could set the column directly."""
        with pytest.raises(ValueError, match="database"):
            insert_item(db, title="X", **{managed: 999})

    def test_missing_title_raises(self, db):
        with pytest.raises(ValueError, match="title"):
            insert_item(db, isbn="9780000000118")
        with pytest.raises(ValueError, match="title"):
            insert_item(db, title="")

    def test_integrity_errors_still_reach_the_caller(self, db):
        """Sites catch IntegrityError to show a duplicate card rather than a
        500 — the wrapper must not swallow it."""
        insert_item(db, title="First", isbn="9780000000125", media_type="book")
        with pytest.raises(sqlite3.IntegrityError):
            insert_item(db, title="Second", isbn="9780000000125", media_type="book")


class TestColumnDiscovery:
    @pytest.fixture(autouse=True)
    def _cold_cache(self):
        """`insert_item` caches the column set in a module global, and
        `make test` runs `--dist loadfile` — one worker, file order. Without
        this, an earlier test in the file warms the cache with the real
        columns and the assertions below never execute the live read at all:
        hardcoding the column set left all 18 tests green and failed only when
        the one test ran alone."""
        reset_column_cache()
        yield
        reset_column_cache()

    def test_columns_come_from_the_live_table(self, db):
        cols = item_columns(db)
        live = {r[1] for r in db.execute("PRAGMA table_info(items)")}
        assert cols == live

    def test_covers_every_column_a_caller_might_set(self, db):
        cols = item_columns(db)
        for name in ("title", "isbn", "language", "owned", "platform",
                     "hardcover_user_book_id", "abs_library_id", "manual_value"):
            assert name in cols

    def test_a_new_column_is_accepted_without_editing_this_module(self, db):
        """The reason the column set is read rather than transcribed: a
        migration adding a column must not need a change here."""
        reset_column_cache()
        db.execute("ALTER TABLE items ADD COLUMN test_only_column TEXT")
        try:
            item_id = insert_item(db, title="New col", test_only_column="value")
            row = db.execute(
                "SELECT test_only_column FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            assert row["test_only_column"] == "value"
        finally:
            reset_column_cache()

    def test_stale_cache_self_heals(self, db):
        """A column added after the cache was warmed must still be accepted."""
        reset_column_cache()
        insert_item(db, title="warm the cache")
        db.execute("ALTER TABLE items ADD COLUMN late_column TEXT")
        try:
            item_id = insert_item(db, title="late", late_column="ok")
            row = db.execute(
                "SELECT late_column FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            assert row["late_column"] == "ok"
        finally:
            reset_column_cache()


class TestCallerContract:
    def test_caller_owns_the_transaction(self, db):
        """insert_item takes a connection rather than opening one, so a site
        can insert and write its tags/scan-log in the same transaction, and so
        lastrowid stays meaningful (G16, G18)."""
        import inspect

        params = list(inspect.signature(insert_item).parameters)
        assert params[0] == "db"

    def test_works_inside_the_app_connection_helper(self, admin_user):
        with get_db() as db:
            item_id = insert_item(db, title="Via get_db")
            assert db.execute(
                "SELECT 1 FROM items WHERE id = ?", (item_id,)
            ).fetchone()


# ---------------------------------------------------------------------------
# The value stage (issue #54). Every pin below asserts on the STORED ROW, not on
# the call's return — G31: a pin that reads back what it passed in defends
# nothing. `_insert_item` (raw SQL) seeds rows the funnel would refuse, which
# is exactly how legacy junk gets into a real database.
# ---------------------------------------------------------------------------


def _row(db, item_id, *cols):
    return db.execute(
        f"SELECT {', '.join(cols)} FROM items WHERE id = ?", (item_id,)
    ).fetchone()


class TestValueStageProbe:
    """The design's probe: `insert_item` accepted all three of these on `main`."""

    def test_invalid_isbn_checksum_raises(self, db):
        with pytest.raises(InvalidIsbn) as exc:
            insert_item(db, title="X", isbn="9780441172710")
        assert isinstance(exc.value, ItemValueError)
        assert isinstance(exc.value, ValueError)
        assert exc.value.code == "invalid_isbn"
        assert exc.value.field == "isbn"
        assert "9780441172710" in str(exc.value)
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0

    def test_unknown_media_type_raises(self, db):
        with pytest.raises(UnknownMediaType) as exc:
            insert_item(db, title="X", media_type="not_a_type")
        assert isinstance(exc.value, ItemValueError)
        assert isinstance(exc.value, ValueError)
        assert exc.value.code == "unknown_media_type"
        assert exc.value.field == "media_type"
        assert "not_a_type" in str(exc.value)

    def test_unknown_location_raises_before_the_foreign_key(self, db):
        with pytest.raises(UnknownLocationError) as exc:
            insert_item(db, title="X", location_id=999999)
        assert isinstance(exc.value, ItemValueError)
        assert isinstance(exc.value, ValueError)
        assert exc.value.code == "unknown_location"
        assert exc.value.field == "location_id"
        assert "999999" in str(exc.value)

    def test_every_subclass_is_importable_from_item_write(self):
        from app.services import item_write

        for name in ("ItemValueError", "InvalidIsbn", "UnknownMediaType",
                     "UnknownLocationError", "UnknownPlatform",
                     "InvalidReadingStatus", "InvalidOwned",
                     "InvalidWishlisted"):
            cls = getattr(item_write, name)
            assert issubclass(cls, ValueError)
            assert isinstance(cls.code, str) and cls.code


class TestCanonicalIsbnRewrite:
    def test_isbn10_stores_the_pair(self, db):
        item_id = insert_item(db, title="X", isbn="054792822X")
        row = _row(db, item_id, "isbn", "isbn10")
        assert (row["isbn"], row["isbn10"]) == ("9780547928227", "054792822X")

    def test_inconsistent_isbn10_is_overwritten(self, db):
        item_id = insert_item(db, title="X", isbn="9780547928227", isbn10="wrong")
        row = _row(db, item_id, "isbn", "isbn10")
        assert (row["isbn"], row["isbn10"]) == ("9780547928227", "054792822X")

    def test_979_has_no_isbn10(self, db):
        item_id = insert_item(db, title="X", isbn="9791234567896", isbn10="junk")
        row = _row(db, item_id, "isbn", "isbn10")
        assert row["isbn"] == "9791234567896"
        assert row["isbn10"] is None

    def test_isbn10_alone_stores_both(self, db):
        item_id = insert_item(db, title="X", isbn10="054792822X")
        row = _row(db, item_id, "isbn", "isbn10")
        assert (row["isbn"], row["isbn10"]) == ("9780547928227", "054792822X")

    def test_blank_isbn_clears_both(self, db):
        item_id = insert_item(db, title="X", isbn="", isbn10="054792822X")
        row = _row(db, item_id, "isbn", "isbn10")
        assert row["isbn"] is None
        assert row["isbn10"] is None

    def test_explicit_none_clears_both(self, db):
        item_id = insert_item(db, title="X", isbn=None, isbn10="054792822X")
        row = _row(db, item_id, "isbn", "isbn10")
        assert row["isbn"] is None
        assert row["isbn10"] is None


class TestPlatformStatusOwned:
    def test_unseeded_platform_raises(self, db):
        with pytest.raises(UnknownPlatform) as exc:
            insert_item(db, title="X", media_type="video_game", platform="ps9")
        assert exc.value.code == "unknown_platform"
        assert "ps9" in str(exc.value)

    def test_seeded_platform_is_stored(self, db):
        slug = db.execute("SELECT slug FROM game_platforms LIMIT 1").fetchone()["slug"]
        item_id = insert_item(db, title="X", media_type="video_game", platform=slug)
        assert _row(db, item_id, "platform")["platform"] == slug

    def test_blank_platform_stores_none(self, db):
        item_id = insert_item(db, title="X", media_type="video_game", platform="")
        assert _row(db, item_id, "platform")["platform"] is None

    def test_out_of_domain_reading_status_raises(self, db):
        with pytest.raises(InvalidReadingStatus) as exc:
            insert_item(db, title="X", reading_status="done")
        assert exc.value.code == "invalid_reading_status"

    @pytest.mark.parametrize("status", READING_STATUSES)
    def test_each_reading_status_is_stored(self, db, status):
        item_id = insert_item(db, title="X", reading_status=status)
        assert _row(db, item_id, "reading_status")["reading_status"] == status

    def test_blank_reading_status_stores_none(self, db):
        item_id = insert_item(db, title="X", reading_status="")
        assert _row(db, item_id, "reading_status")["reading_status"] is None

    @pytest.mark.parametrize("given,stored", [(True, 1), ("1", 1), (0, 0), (False, 0), ("0", 0)])
    def test_owned_is_coerced_to_int(self, db, given, stored):
        item_id = insert_item(db, title="X", owned=given)
        value = _row(db, item_id, "owned")["owned"]
        assert value == stored and isinstance(value, int)

    @pytest.mark.parametrize("bad", [2, "yes", -1, "true"])
    def test_out_of_domain_owned_raises(self, db, bad):
        with pytest.raises(InvalidOwned) as exc:
            insert_item(db, title="X", owned=bad)
        assert exc.value.code == "invalid_owned"


class TestUpdateItemFields:
    def test_changing_isbn_rewrites_isbn10(self, db):
        """#54's edit repro at the funnel level."""
        item_id = _insert_item(db, isbn="9780000000002", isbn10="0000000027")
        update_item_fields(db, item_id, {"isbn": "054792822X"})
        row = _row(db, item_id, "isbn", "isbn10")
        assert (row["isbn"], row["isbn10"]) == ("9780547928227", "054792822X")

    def test_a_field_not_present_is_not_validated(self, db):
        """Legacy junk in `isbn` must not block an edit to `notes`."""
        item_id = _insert_item(db, isbn="B00EXAMPLE")
        update_item_fields(db, item_id, {"notes": "x"})
        row = _row(db, item_id, "isbn", "notes")
        assert row["isbn"] == "B00EXAMPLE"
        assert row["notes"] == "x"

    def test_bad_value_raises_and_row_is_unchanged(self, db):
        item_id = _insert_item(db, title="Before", isbn="9780000000002")
        with pytest.raises(InvalidIsbn):
            update_item_fields(db, item_id, {"title": "After", "isbn": "9780441172710"})
        row = _row(db, item_id, "title", "isbn")
        assert (row["title"], row["isbn"]) == ("Before", "9780000000002")

    def test_unknown_column_raises_naming_it(self, db):
        item_id = _insert_item(db)
        with pytest.raises(ValueError, match="nonexistent_column"):
            update_item_fields(db, item_id, {"nonexistent_column": 1})

    @pytest.mark.parametrize("managed", ["id", "created_at", "deleted_at"])
    def test_managed_columns_are_refused(self, db, managed):
        """`deleted_at` is the one that matters for Trash: without it,
        `update_item_fields(db, id, {"deleted_at": ...})` would route around
        `trash_item` entirely and the four-writer claim would be a statement
        about source text rather than about what the code can do."""
        item_id = _insert_item(db)
        with pytest.raises(ValueError, match="database"):
            update_item_fields(db, item_id, {managed: 1})

    def test_empty_fields_is_a_touch(self, db):
        item_id = _insert_item(db)
        db.execute(
            "UPDATE items SET updated_at = '2000-01-01 00:00:00' WHERE id = ?",
            (item_id,),
        )
        update_item_fields(db, item_id, {})
        assert _row(db, item_id, "updated_at")["updated_at"] != "2000-01-01 00:00:00"

    def test_updated_at_always_moves(self, db):
        item_id = _insert_item(db)
        db.execute(
            "UPDATE items SET updated_at = '2000-01-01 00:00:00' WHERE id = ?",
            (item_id,),
        )
        update_item_fields(db, item_id, {"notes": "n"})
        assert _row(db, item_id, "updated_at")["updated_at"] != "2000-01-01 00:00:00"

    def test_unknown_location_raises_on_update(self, db):
        item_id = _insert_item(db)
        with pytest.raises(UnknownLocationError):
            update_item_fields(db, item_id, {"location_id": 999999})
        assert _row(db, item_id, "location_id")["location_id"] is None


class TestUpdateItemsFields:
    def test_updates_every_id_in_one_call(self, db, monkeypatch):
        ids = [_insert_item(db, title=f"T{i}", isbn=None) for i in range(3)]
        loc = _insert_location(db)
        from app.services import item_write

        calls = []
        real = item_write.validate_item_fields

        def counting(db_, fields):
            calls.append(dict(fields))
            return real(db_, fields)

        monkeypatch.setattr(item_write, "validate_item_fields", counting)
        update_items_fields(db, ids, {"location_id": loc})
        for item_id in ids:
            assert _row(db, item_id, "location_id")["location_id"] == loc
        assert len(calls) == 1

    def test_bad_value_moves_nothing(self, db):
        ids = [_insert_item(db, title=f"T{i}", isbn=None) for i in range(2)]
        with pytest.raises(UnknownMediaType):
            update_items_fields(db, ids, {"media_type": "widget"})
        for item_id in ids:
            assert _row(db, item_id, "media_type")["media_type"] == "book"

    def test_empty_id_list_is_a_no_op(self, db):
        update_items_fields(db, [], {"media_type": "widget"})  # not even validated


def _is_member(db, item_id):
    from app.services import lists
    return lists.is_member(db, lists.WISHLIST, item_id)


def _snapshot(db):
    """Everything the refusal pins must prove was left untouched."""
    return (
        db.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"],
        db.execute(
            "SELECT id, owned, title FROM items ORDER BY id"
        ).fetchall(),
        db.execute("SELECT COUNT(*) AS c FROM item_copies").fetchone()["c"],
        db.execute(
            "SELECT list_id, item_id FROM list_items ORDER BY list_id, item_id"
        ).fetchall(),
    )


class TestWishlisted:
    """The virtual `wishlisted` field on all three funnels (#125)."""

    def test_insert_unowned_and_wishlisted_creates_membership(self, db):
        item_id = insert_item(db, title="Wanted", owned=0, wishlisted=True)
        assert _is_member(db, item_id)

    def test_insert_via_the_dict_shape_creates_membership(self, db):
        item_id = insert_item(db, {"title": "Wanted", "owned": 0, "wishlisted": True})
        assert _is_member(db, item_id)

    def test_insert_unowned_without_the_field_creates_no_membership(self, db):
        """The deliberate gap: owned = 0 alone says nothing about the list.
        A writer that forgets the field is a bug the per-writer pins catch."""
        item_id = insert_item(db, title="Unowned", owned=0)
        assert not _is_member(db, item_id)

    def test_wishlisted_false_removes_membership(self, db):
        item_id = insert_item(db, title="Wanted", owned=0, wishlisted=True)
        update_item_fields(db, item_id, {"wishlisted": False})
        assert not _is_member(db, item_id)

    def test_bulk_update_sets_membership_both_ways(self, db):
        ids = [insert_item(db, title=f"W{i}", owned=0, wishlisted=True)
               for i in range(3)]
        update_items_fields(db, ids, {"wishlisted": False})
        assert not any(_is_member(db, i) for i in ids)
        update_items_fields(db, ids, {"wishlisted": True})
        assert all(_is_member(db, i) for i in ids)

    def test_promoting_to_owned_removes_membership(self, db):
        item_id = insert_item(db, title="Bought", owned=0, wishlisted=True)
        update_item_fields(db, item_id, {"owned": 1})
        assert not _is_member(db, item_id)

    def test_promoting_to_owned_as_a_string_removes_membership(self, db):
        """The funnel accepts "1" as well as 1, and `_apply_membership` sees
        the caller's raw mapping — `validate_item_fields` normalises a copy.
        A bare `== 1` there leaves the item on the wishlist."""
        item_id = insert_item(db, title="Bought", owned=0, wishlisted=True)
        update_item_fields(db, item_id, {"owned": "1"})
        assert not _is_member(db, item_id)

    def test_a_bad_wishlisted_value_is_refused(self, db):
        with pytest.raises(InvalidWishlisted):
            insert_item(db, title="X", owned=0, wishlisted="yes")

    def test_updating_a_nonexistent_id_writes_no_membership(self, db):
        update_item_fields(db, 99999, {"wishlisted": True})
        assert db.execute(
            "SELECT COUNT(*) AS c FROM list_items WHERE item_id = 99999"
        ).fetchone()["c"] == 0

    def test_wishlisted_never_reaches_the_name_check(self, db):
        """It is not a column; popping it is what keeps `_validated_names`
        from rejecting it as "not on the items table"."""
        item_id = insert_item(db, title="Wanted", owned=0, wishlisted=True)
        assert _row(db, item_id, "title")["title"] == "Wanted"


class TestWishlistedRefusals:
    """The one contradiction the funnel refuses — and it refuses it *before*
    writing anything, so every pin here asserts an unchanged database."""

    def test_insert_owned_and_wishlisted_writes_nothing(self, db):
        before = _snapshot(db)
        with pytest.raises(InvalidWishlisted):
            insert_item(db, {"title": "Contradiction", "owned": 1, "wishlisted": True})
        assert _snapshot(db) == before

    @pytest.mark.parametrize("owned", ["1", True, 1])
    def test_the_refusal_uses_the_same_coercion_as_owned(self, db, owned):
        before = _snapshot(db)
        with pytest.raises(InvalidWishlisted):
            insert_item(db, {"title": "Contradiction", "owned": owned,
                             "wishlisted": True})
        assert _snapshot(db) == before

    def test_insert_wishlisted_with_no_owned_key_writes_nothing(self, db):
        """`owned` omitted means the SCHEMA default, which is 1 — so this is
        the forbidden state even though no `owned` key was submitted. The
        pre-fix check read `values.get("owned")` and let this through."""
        before = _snapshot(db)
        with pytest.raises(InvalidWishlisted):
            insert_item(db, title="Wanted", wishlisted=True)
        assert _snapshot(db) == before

    def test_wishlisting_an_owned_row_writes_nothing(self, db):
        item_id = insert_item(db, title="Owned", owned=1)
        before = _snapshot(db)
        with pytest.raises(InvalidWishlisted):
            update_item_fields(db, item_id, {"wishlisted": True})
        assert _snapshot(db) == before

    def test_a_mixed_bulk_selection_does_not_half_apply(self, db):
        unowned_a = insert_item(db, title="A", owned=0)
        owned = insert_item(db, title="B", owned=1)
        unowned_b = insert_item(db, title="C", owned=0)
        before = _snapshot(db)
        with pytest.raises(InvalidWishlisted):
            update_items_fields(db, [unowned_a, owned, unowned_b],
                                {"wishlisted": True})
        assert _snapshot(db) == before
        assert not any(_is_member(db, i) for i in (unowned_a, owned, unowned_b))


class TestPromoteWishlisted:
    """#125: Add mode's "I bought it" transition, on the caller's connection."""

    def _state(self, db, item_id):
        from app.services import lists

        owned = db.execute("SELECT owned FROM items WHERE id = ?", (item_id,)).fetchone()["owned"]
        return owned, lists.is_member(db, lists.WISHLIST, item_id)

    def test_a_member_becomes_owned_and_leaves_the_wishlist(self, db):
        item_id = _insert_item(db, title="Bought It", owned=0, wishlisted=True)
        assert promote_wishlisted(db, item_id) is True
        assert self._state(db, item_id) == (1, False)

    def test_a_neither_row_is_left_alone(self, db):
        item_id = _insert_item(db, title="Read Elsewhere", owned=0)
        assert promote_wishlisted(db, item_id) is False
        assert self._state(db, item_id) == (0, False)

    def test_an_owned_row_is_left_alone(self, db):
        item_id = _insert_item(db, title="Already Mine", owned=1)
        before = db.execute("SELECT updated_at FROM items WHERE id = ?", (item_id,)).fetchone()[0]
        assert promote_wishlisted(db, item_id) is False
        assert self._state(db, item_id) == (1, False)
        after = db.execute("SELECT updated_at FROM items WHERE id = ?", (item_id,)).fetchone()[0]
        assert after == before


class TestTheRetiredMediaTypeAlias:
    """`kids_book` is accepted on input and stored as `book`.

    The funnel is the backstop: routes canonicalise earlier, above their own
    duplicate guards, but this is what guarantees that nothing retired can
    reach the table by any path — including one added later that forgets.
    """

    def _media_type(self, db, item_id):
        return db.execute(
            "SELECT media_type FROM items WHERE id = ?", (item_id,)
        ).fetchone()["media_type"]

    def test_insert_stores_the_canonical_value(self, db):
        item_id = insert_item(db, title="Goodnight Moon", media_type="kids_book")
        assert self._media_type(db, item_id) == "book"

    def test_update_stores_the_canonical_value(self, db):
        item_id = insert_item(db, title="Goodnight Moon", media_type="book")
        update_item_fields(db, item_id, {"media_type": "kids_book"})
        assert self._media_type(db, item_id) == "book"

    def test_bulk_update_stores_the_canonical_value(self, db):
        first = insert_item(db, title="One", isbn=None, media_type="book")
        second = insert_item(db, title="Two", isbn=None, media_type="book")
        update_items_fields(db, [first, second], {"media_type": "kids_book"})
        assert self._media_type(db, first) == "book"
        assert self._media_type(db, second) == "book"

    def test_an_unknown_value_still_raises(self, db):
        """Canonicalising must not become a way to smuggle a bad value in."""
        with pytest.raises(UnknownMediaType):
            insert_item(db, title="X", media_type="not_a_type")


#: The author index (#117b) is written only by `app/services/author_index.py`.
AUTHOR_INDEX_WRITE = re.compile(
    r"(INSERT\s+INTO|DELETE\s+FROM)\s+(authors|item_authors)\b", re.I)
AUTHOR_INDEX_IMPORT = re.compile(
    r"from\s+app\.services\s+import\s+[^\n]*\bauthor_index\b"
    r"|from\s+app\.services\.author_index\s+import"
    r"|import\s+app\.services\.author_index\b")
AUTHOR_INDEX_WRITER = "app/services/author_index.py"
AUTHOR_INDEX_IMPORTERS = {
    "app/services/item_write.py",  # the funnel keeps the index in step
    "app/database.py",             # the boot-time build
    "app/routers/pages.py",        # read helpers for Browse and the item page
}


def _author_index_offenders(root: Path, repo_root: Path):
    """(writers, importers) outside the allowed sets, by repo-relative path (G88)."""
    writers, importers = [], []
    for path in root.rglob("*.py"):
        rel = path.relative_to(repo_root).as_posix()
        buf, line_for = _normalised_source(path)
        if rel != AUTHOR_INDEX_WRITER:
            writers += [f"{rel}:{line_for(m.start())}"
                        for m in AUTHOR_INDEX_WRITE.finditer(buf)]
        if rel not in AUTHOR_INDEX_IMPORTERS and rel != AUTHOR_INDEX_WRITER:
            importers += [f"{rel}:{line_for(m.start())}"
                          for m in AUTHOR_INDEX_IMPORT.finditer(buf)]
    return writers, importers


class TestAuthorIndexWritePath:
    def test_only_author_index_writes_the_index(self):
        writers, importers = _author_index_offenders(APP_DIR, REPO_ROOT)
        assert not writers, (
            "authors/item_authors are written only by "
            f"{AUTHOR_INDEX_WRITER} (called from item_write):\n  "
            + "\n  ".join(writers))
        assert not importers, (
            f"only {sorted(AUTHOR_INDEX_IMPORTERS)} may import author_index:\n  "
            + "\n  ".join(importers))

    def test_the_guard_sees_a_planted_writer_and_an_impostor(self, tmp_path):
        """G31/G88: a write planted elsewhere — including in a file that only
        shares the writer's basename — and a split literal are both caught."""
        app = tmp_path / "app"
        (app / "services").mkdir(parents=True)
        (app / "routers").mkdir()
        (app / "services" / "author_index.py").write_text(
            'db.execute("INSERT INTO item_authors VALUES (1)")\n')
        (app / "routers" / "author_index.py").write_text(
            'db.execute("DELETE FROM authors WHERE id = ?")\n')
        (app / "services" / "other.py").write_text(
            'from app.services import author_index\n'
            'db.execute("INSERT INTO "\n'
            '           "item_authors (item_id) VALUES (?)")\n')
        writers, importers = _author_index_offenders(app, tmp_path)
        assert sorted(w.split(":")[0] for w in writers) == [
            "app/routers/author_index.py", "app/services/other.py"]
        assert [i.split(":")[0] for i in importers] == ["app/services/other.py"]
