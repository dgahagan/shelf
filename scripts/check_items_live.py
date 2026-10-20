#!/usr/bin/env python3
"""Tripwire lint: every read of `items` or `item_copies` in app/ must go
through its TEMP view (app/database.py::get_db()), not the physical table.

T1 added `deleted_at` to `items`/`item_copies`; the earlier soft-delete-seam
plan's own T2 made get_db() create a per-connection `CREATE TEMP VIEW IF NOT
EXISTS items_live AS SELECT * FROM items WHERE deleted_at IS NULL` and T4-T6
repointed the ~172 existing direct reads in app/ onto that view, so the
items census is 0 and this script has guarded it there ever since — a new
direct read of `items` in app/ reds it.

This plan's own T1 added the second view, `copies_live` — a copy is live
only if it AND its item are untrashed (see the CREATE in get_db()). Its T2
taught the same script the second relation, item_copies -> copies_live, as a
parallel set of functions (`find_copy_violations`, `copy_allowlist_mismatches`)
that share the items functions' normaliser and span-matcher rather than
duplicating them. T3-T5 repointed every non-exempt read, driving the copies
census to 0, and this task (T6) is what makes main() fail on it — a new
direct read of item_copies in app/ now reds the build exactly as a new direct
read of items already does.

Only app/**/*.py is scanned. Tests legitimately read the physical tables (a
later plan needs them to) and are out of scope.

G53: prose in app/ that needs to talk about this should say "the items
table"/"items_live" or "the item_copies table"/"copies_live", never write
the literal `FROM items` / `JOIN items` / `FROM item_copies` / `JOIN
item_copies` construct in a comment or docstring — this guard strips
`#`-comment *lines* but not inline prose inside a docstring, and a comment
quoting either construct must not trip it.

Run directly (exit 1 on items violations, items mismatches, copies
violations, or copies allowlist mismatches) or via
tests/test_items_live_lint.py.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Strip a string literal's prefix together with its opening quote, before
# the bare-quote strip below. Skipping this turns `f"FROM items i "` into
# `fFROM items i ` and `\bFROM` no longer matches, because `f` and `F` are
# both word characters with no boundary between them. Three statements have
# exactly this shape today: app/routers/items.py:901, app/routers/pages.py:70,
# app/routers/series.py:83. (An equivalent fix is a prefix-aware matcher —
# `(?<!\w)(?:[rubf]{1,2})?(FROM|JOIN)\s+items\b` — this file picks the
# prefix-strip approach instead.)
_STRING_PREFIX_QUOTE = re.compile(r'\b[rRbBuUfF]{1,2}"')

# The forbidden construct. Excluded, each pinned in
# tests/test_items_live_lint.py even though only the first exists in app/
# today:
#   - preceded by "DELETE " (`DELETE FROM items ...`) — a write, stays on
#     the physical table.
#   - preceded by "yield " (`yield from items`).
#   - followed by " import" (`from items import x`).
# `\b` does not split on `_`, so items_live / item_copies / item_tags /
# item_links never match.
_ITEMS_READ = re.compile(
    r"(?<!DELETE )(?<!yield )\b(FROM|JOIN)\s+items\b(?! import)", re.I
)

VIOLATION_MSG = (
    "{path}:{line}: reads the items table directly — read through "
    "items_live (writes and the allowlisted lookups in "
    "scripts/check_items_live.py stay on items; prose quoting the "
    "FROM/JOIN construct inside a docstring trips this too, see G53)"
)

# The item_copies twin of _ITEMS_READ. No `yield`/`import` exclusion is
# needed here — nothing in app/ does `yield from item_copies` or
# `from item_copies import x` today, unlike the items case (see the
# `_ITEMS_READ` comment above) — but the DELETE exclusion still applies:
# app/services/item_copies.py has three `DELETE FROM item_copies ...`
# statements (writes, stay on the physical table). `\b` does not split on
# `_`, so `copies_live` never matches.
_COPIES_READ = re.compile(r"(?<!DELETE )\b(FROM|JOIN)\s+item_copies\b", re.I)

COPIES_VIOLATION_MSG = (
    "{path}:{line}: reads the item_copies table directly — read through "
    "copies_live (writes and the allowlisted lookups in "
    "scripts/check_items_live.py stay on item_copies; prose quoting the "
    "FROM/JOIN construct inside a docstring trips this too, see G53)"
)

#: repo-relative path -> {statement substring: how many reads it may suppress}.
#: A substring identifies a legitimate direct read of the physical `items`
#: table, and is normalised the same way the scanner reads source (quotes
#: stripped, whitespace collapsed to single spaces). Mirrors
#: RAW_UPDATE_ALLOWLIST in tests/test_item_write.py — each entry was read at
#: its call site and carries a one-line reason. Allowlisted by
#: repository-relative path, never by basename (G88).
#:
#: An entry must SPAN the read it excuses (see `_spanning`), so it has to
#: quote enough of its statement to contain the FROM/JOIN clause outright.
#: Keep each one long enough that a *different* statement cannot reproduce
#: it and allowlist itself: the count beside it is the structural half of
#: that guard, and `allowlist_mismatches()` reds when a new read rides along
#: inside an existing entry's text.
ALLOWLIST: dict[str, dict[str, int]] = {
    "app/database.py": {
        # The items_live view CREATE in get_db() itself — the seam reads the
        # physical table by definition. Without this entry the lint reds on
        # the statement that defines it.
        "SELECT * FROM items WHERE deleted_at IS NULL": 1,
        # The copies_live view CREATE in get_db() — it joins the items
        # relation so that trashing an item hides its copies with no write
        # to them, so the seam's own definition reads the physical table.
        "FROM item_copies c JOIN items i ON i.id = c.item_id "
        "WHERE c.deleted_at IS NULL AND i.deleted_at IS NULL": 1,
        # Migrations 20 and 21 (UPC re-filing) — the two statements share
        # this prefix and diverge after it, so one substring, two hits.
        "AND NOT EXISTS (SELECT 1 FROM items o WHERE o.upc =": 2,
        # Migration 26 — backfill primary copies from legacy locations.
        "SELECT i.id, 1, i.location_id, 1 FROM items i": 1,
        # Migration 36 — seed the wishlist from every unowned item.
        "SELECT (SELECT id FROM lists WHERE slug = 'wishlist'), id "
        "FROM items WHERE owned = 0": 1,
        # gc_orphaned_series_meta: a soft-deleted item keeps its series
        # alive so a restore finds it intact.
        "SELECT 1 FROM items WHERE series_name = ? COLLATE NOCASE": 1,
        # _retire_kids_book's unlocked short-circuit and its re-read under
        # the lock. A trashed kids_book row must be rewritten too: leaving
        # one behind means a row whose media_type the write funnel refuses
        # if it is ever restored.
        "FROM items WHERE media_type = 'kids_book'": 2,
        # _retire_kids_book's twin lookup. It exists to predict the
        # UNIQUE(isbn, media_type) collision the rewrite would otherwise
        # hand to the database, and a trashed row still holds its unique
        # slot (G107).
        "SELECT id FROM items WHERE media_type = 'book' AND": 1,
    },
    "app/services/item_write.py": {
        # _trashed_twin — the insert funnel's collision-with-Trash lookup.
        # It must read the physical table because the UNIQUE constraints do:
        # a trashed row still holds its (isbn, media_type) / (upc,
        # media_type) slot, so items_live would hide exactly the row whose
        # slot the INSERT is about to collide with (G107).
        # `LIMIT 1` is load-bearing *here*, not only in SQLite: without it
        # this text is a prefix of refuse_trash_collision's statements below
        # and would silently span those reads too, which is the exact
        # ride-along G105 exists to catch.
        "SELECT id, title FROM items "
        "WHERE isbn = ? AND media_type = ? AND deleted_at IS NOT NULL "
        "LIMIT 1": 1,
        "SELECT id, title FROM items "
        "WHERE upc = ? AND media_type = ? AND deleted_at IS NOT NULL "
        "LIMIT 1": 1,
        # refuse_trash_collision — the update funnel's mirror of the same
        # rule. `AND id != ?` excludes the row being edited, so re-saving an
        # item's own unchanged identifiers is never a collision. Physical for
        # the same reason as the two above.
        "SELECT id, title FROM items WHERE isbn = ? AND media_type = ? "
        "AND deleted_at IS NOT NULL AND id != ?": 1,
        "SELECT id, title FROM items WHERE upc = ? AND media_type = ? "
        "AND deleted_at IS NOT NULL AND id != ?": 1,
        # trashed_title — names the trashed row blocking an edit, for the
        # edit page's refusal arm. Physical by definition: a live row has no
        # title to report here.
        "SELECT title FROM items WHERE id = ? AND deleted_at IS NOT NULL": 1,
    },
    "app/services/author_index.py": {
        # reindex_items: the author index covers trashed rows too (soft
        # delete writes nothing to children), so the existence check before
        # linking finds the rows the view hides (G107).
        "SELECT id FROM items WHERE id IN (": 1,
        # rebuild_all: the boot build indexes every physical row, trashed
        # ones included, for the same reason.
        "SELECT id, authors FROM items WHERE authors IS NOT NULL": 1,
    },
    "app/services/trash.py": {
        # expired_count's two halves. It counts exactly the rows the views
        # hide: trashed items, and trashed copies whose item is live (the
        # item join is physical so the copy half can ask that itself).
        "(SELECT COUNT(*) FROM items i WHERE": 1,
        "(SELECT COUNT(*) FROM item_copies c JOIN items i "
        "ON i.id = c.item_id AND i.deleted_at IS NULL WHERE": 1,
        # expired_ids — what Empty expired purges, chosen under its lock.
        # Same two halves as the count, returning ids.
        "SELECT i.id FROM items i WHERE": 1,
        "SELECT c.id FROM item_copies c JOIN items i ON i.id = c.item_id "
        "AND i.deleted_at IS NULL WHERE": 1,
        # copy_state — tells restore_copy's three None outcomes apart; must
        # see a trashed copy and a trashed item, which the views hide.
        "FROM item_copies c JOIN items i ON i.id = c.item_id WHERE c.id = ?": 1,
        # purge_item's guard — a purge starts from the row items_live hides.
        "SELECT id FROM items WHERE id = ? AND deleted_at IS NOT NULL": 1,
        # listing()'s item half — the Trash page itself, so it must see the
        # rows items_live hides by definition.
        "FROM items i WHERE i.deleted_at IS NOT NULL": 1,
        # listing()'s copy half — the JOIN to items is part of the read
        # (G107): a copy's group heading needs to know whether its item is
        # live or trashed, which items_live cannot answer either way. The
        # full span (through the WHERE) keeps this text from also spanning
        # copy_state's shorter "...c.item_id WHERE c.id = ?" match above —
        # a short prefix here would ride along on that one too (G105).
        "FROM item_copies c JOIN items i ON i.id = c.item_id "
        "LEFT JOIN locations l ON l.id = c.location_id "
        "WHERE c.deleted_at IS NOT NULL": 1,
    },
    "app/services/audiobookshelf.py": {
        # The ABS external-id matcher. Physical so a trashed row is SEEN and
        # skipped — through the view an ISBN-less item would miss and the
        # next sync would insert a duplicate. `abs_id` is a plain index, not
        # unique, so the read orders live rows first and a trashed row is
        # acted on only when no live row shares the id (claude-R4).
        "FROM items WHERE abs_id = ? "
        "ORDER BY deleted_at IS NOT NULL, id LIMIT 1": 1,
    },
    "app/services/komga_records.py": {
        # _existing_record. The JOIN is part of the read (G107): joining the
        # view is what hid a trashed item's record, and komga_id is the
        # record table's PRIMARY KEY — so a hidden record meant a fresh
        # insert, a PK violation, and the whole block rolled back.
        "SELECT kr.*, i.source, i.media_type, i.deleted_at FROM komga_records kr "
        "JOIN items i ON i.id = kr.item_id WHERE kr.komga_id = ?": 1,
    },
    "app/services/romm_records.py": {
        # _existing_record — same reasoning as Komga's, and RomM has no
        # IntegrityError handler at all, so the rollback was unconditional.
        "SELECT rr.*, i.source, i.deleted_at FROM romm_records rr "
        "JOIN items i ON i.id = rr.item_id WHERE rr.romm_id = ?": 1,
    },
    "app/routers/hardcover.py": {
        # _trashed_by_hardcover_id — run only after every live strategy in
        # _find_existing_item (or add-to-shelf's two live guards) has missed,
        # which is the live-wins rule for a non-unique key (claude-R4,
        # claude-M2). Physical because it exists to find the row the view
        # hides.
        "SELECT id FROM items WHERE hardcover_book_id = ? "
        "AND deleted_at IS NOT NULL LIMIT 1": 1,
    },
    "app/services/barcode_resolve.py": {
        # resolve() (moved from items_scan_modes._find_item_by_barcode) —
        # the existing-item scan modes must find a row in Trash so they can report it and offer Restore instead of
        # "not in your collection". Ordered live-first: an ISBN held by a
        # live row and a trashed one (different media types) answers live.
        "SELECT i.*, l.name as location_name FROM items i "
        "LEFT JOIN locations l ON i.location_id = l.id WHERE i.isbn = ?": 1,
        "SELECT i.*, l.name as location_name FROM items i "
        "LEFT JOIN locations l ON i.location_id = l.id WHERE i.upc = ?": 1,
        # resolve()'s copy-barcode read — finds the row the view hides, not a
        # UNIQUE prediction: a trashed copy, or a copy of a trashed item, must
        # still resolve so the scan reports it. Physical on both tables (G107).
        "SELECT i.*, l.name AS location_name, c.id AS copy_id, "
        "c.copy_number, c.location_id AS copy_location_id, "
        "cl.name AS copy_location_name, "
        "COALESCE(i.deleted_at, c.deleted_at) AS trash_stamp, "
        "(i.deleted_at IS NULL AND c.deleted_at IS NOT NULL) AS copy_trashed "
        "FROM item_copies c JOIN items i ON i.id = c.item_id": 1,
    },
    "app/routers/items_csv.py": {
        # CSV dedup — same reason as _find_item_by_barcode above. `isbn IN
        # (?, ?)` can match two different rows (the ISBN-13 and ISBN-10 forms
        # can each be held by a different item), and the title/author
        # fallback below is covered by no UNIQUE constraint at all, so both
        # reads order a live row first and only act on a trashed hit when no
        # live row matches (claude-R4, "live wins").
        "SELECT id, deleted_at FROM items WHERE media_type = ? AND isbn IN (?, ?) "
        "ORDER BY deleted_at IS NOT NULL, id LIMIT 1": 1,
        "SELECT id, deleted_at FROM items WHERE TRIM(title) = TRIM(?) COLLATE NOCASE AND "
        "TRIM(COALESCE(authors, '')) = TRIM(?) COLLATE NOCASE AND media_type = ? AND "
        "(isbn IS NULL OR isbn = '') ORDER BY deleted_at IS NOT NULL, id LIMIT 1": 1,
        # CSV dedup on UPC — predicts UNIQUE(upc, media_type)
        # (idx_items_upc_type), which a trashed row still holds (G107).
        "SELECT id, deleted_at FROM items WHERE upc = ? AND media_type = ? "
        "ORDER BY deleted_at IS NOT NULL, id LIMIT 1": 1,
        # export_csv's editor/admin branch — the CSV export carries Trash for
        # editor/admin (design plan-soft-delete-export). The viewer branch
        # stays on items_live, so this text spans only the physical-table
        # read a non-viewer export takes.
        "SELECT i.*, l.name as location_name, {lists.WISHLISTED_SQL} AS wishlisted "
        "FROM items i LEFT JOIN locations l ON i.location_id = l.id "
        "ORDER BY i.title": 1,
    },
    "app/routers/item_copies.py": {
        # _barcode_conflict — copy_barcode is UNIQUE collection-wide, so this
        # predicts the raw constraint and must join the physical items table,
        # not items_live, or a trashed item's copy would be hidden and the
        # insert would fail on the UNIQUE with a 500 instead of returning
        # this conflict response.
        "SELECT c.id AS copy_id, c.item_id, i.title FROM item_copies c "
        "JOIN items i ON i.id = c.item_id WHERE c.copy_barcode = ?": 1,
    },
    "app/services/archive.py": {
        # _build_items — the portable archive carries Trash — design
        # plan-soft-delete-export. A trashed item's row must be seen so it is
        # exported (and so id_map carries it, dragging its tags/reading
        # log/checkouts along).
        "FROM items i LEFT JOIN locations ON locations.id = i.location_id "
        "ORDER BY i.id": 1,
        # plan_archive's and apply_plan's pre-import bound — one statement
        # text, two reads. Physical so a trashed row whose id is above the
        # live maximum is still inside the bound the trashed-twin lookup
        # below uses; through the view it would be invisible to it.
        "SELECT COALESCE(MAX(id), 0) AS m FROM items": 2,
        # _trashed_twin_lookup — finds the row the view hides, so an archive
        # record can be classified `restore` (or left alone in Trash) before
        # anything is written (G85). NOT a UNIQUE-prediction read: the
        # title/author base is covered by no constraint at all, and all three
        # exist to find a trashed row, not to foresee a collision (G107).
        "SELECT id, cover_path FROM items WHERE isbn = ? AND media_type = ? "
        "AND deleted_at IS NOT NULL AND id <= ? ORDER BY id LIMIT 1": 1,
        "SELECT id, cover_path FROM items WHERE upc = ? AND media_type = ? "
        "AND deleted_at IS NOT NULL AND id <= ? ORDER BY id LIMIT 1": 1,
        "SELECT id, cover_path FROM items WHERE (isbn IS NULL OR isbn = '') "
        "AND media_type = ? AND title = ? COLLATE NOCASE "
        "AND COALESCE(authors, '') = ? COLLATE NOCASE "
        "AND deleted_at IS NOT NULL AND id <= ? ORDER BY id LIMIT 1": 1,
    },
}

#: The item_copies twin of ALLOWLIST — same shape, same G88/G53 rules, a
#: separate dict because find_violations()/allowlist_mismatches() keep their
#: current meaning (items only, per this task's spec) and must not fold
#: item_copies reads in. Eight entries, each at its current spelling in the
#: tree today — allowlisted by repository-relative path, never by basename
#: (G88): app/services/item_copies.py and app/routers/item_copies.py are
#: both keys here on purpose, and an entry under one must never excuse a
#: read in the other.
COPIES_ALLOWLIST: dict[str, dict[str, int]] = {
    "app/services/copy_labels.py": {
        # next_number — a trashed copy still holds its copy_barcode UNIQUE
        # slot, so the next generated number must see it (G107's first
        # class: predicting the constraint).
        "SELECT copy_barcode FROM item_copies WHERE copy_barcode LIKE ?": 1,
    },
    "app/services/barcode_resolve.py": {
        # resolve()'s copy-barcode read — finds the row the view hides, not a
        # UNIQUE prediction: a trashed copy, or a copy of a trashed item, must
        # still resolve so the scan reports it. Same statement as its ALLOWLIST entry.
        "SELECT i.*, l.name AS location_name, c.id AS copy_id, "
        "c.copy_number, c.location_id AS copy_location_id, "
        "cl.name AS copy_location_name, "
        "COALESCE(i.deleted_at, c.deleted_at) AS trash_stamp, "
        "(i.deleted_at IS NULL AND c.deleted_at IS NOT NULL) AS copy_trashed "
        "FROM item_copies c JOIN items i ON i.id = c.item_id": 1,
    },
    "app/database.py": {
        # The copies_live view CREATE in get_db() itself — the seam reads
        # the physical table by definition, same reasoning as the
        # items_live entry above.
        "SELECT c.* FROM item_copies c JOIN items i ON i.id = c.item_id": 1,
        # Migration 26 — backfill primary copies from legacy locations; the
        # NOT EXISTS guard reads item_copies to decide whether a row needs
        # backfilling at all.
        "WHERE i.owned = 1 AND i.location_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM item_copies c WHERE c.item_id = i.id)": 1,
    },
    "app/services/item_copies.py": {
        # backfill_legacy_locations — the app-level rerun of migration 26's
        # backfill (idempotent), same NOT EXISTS guard and the same quoted
        # text as the app/database.py migration entry above; this is fine,
        # they are separate path keys.
        "WHERE i.owned = 1 AND i.location_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM item_copies c WHERE c.item_id = i.id)": 1,
        # sync_primary_location — numbers a new primary copy above whatever
        # copy_number this item already has.
        "SELECT COALESCE(MAX(copy_number), 0) + 1 AS n FROM item_copies "
        "WHERE item_id = ?": 1,
        # add_copy — the numbering half only. A trashed copy still holds its
        # (item_id, copy_number) slot, so numbering must see what the UNIQUE
        # constraint sees. The "does this item have a copy" half is a
        # separate statement and reads the view.
        "SELECT COALESCE(MAX(copy_number), 0) AS highest "
        "FROM item_copies WHERE item_id = ?": 1,
        # restore_copy — NOT the predict-a-UNIQUE-violation class the three
        # entries above are. This read exists to find the row `copies_live`
        # is deliberately hiding: a restore has to start from the trashed
        # copy, which by definition no view will return. Reading the view
        # here would make the function a silent no-op.
        "SELECT id, item_id, location_id FROM item_copies "
        "WHERE id = ? AND deleted_at IS NOT NULL": 1,
        # purge_copy — the same class as restore_copy's read: a purge starts
        # from the trashed copy no view will return.
        "SELECT id FROM item_copies WHERE id = ? AND deleted_at IS NOT NULL": 1,
    },
    "app/services/item_merge.py": {
        # _reparent_copies — numbers the losing item's copies above the
        # keeper's own highest copy_number so nothing collides.
        "SELECT COALESCE(MAX(copy_number), 0) AS n FROM item_copies "
        "WHERE item_id = ?": 1,
        # _reparent_copies — the row-selection read. NOT the
        # predict-a-UNIQUE-violation class the entry above (and most of this
        # allowlist) is: this one exists so a trashed copy of the losing item
        # is not left invisibly parented to it and destroyed by the caller's
        # cascading `DELETE FROM items`. `copies_live` would hide exactly
        # that row, and a trashed copy is restorable, so the loss would be
        # silent and irreversible — see `reparent_children`'s docstring,
        # which already forbids losing anything restorable (G107).
        "SELECT id FROM item_copies WHERE item_id = ? "
        "ORDER BY copy_number, id": 1,
    },
    "app/routers/item_copies.py": {
        # _barcode_conflict — copy_barcode is UNIQUE collection-wide, so the
        # conflict check has to read item_copies directly to find the other
        # item holding the barcode. (The join is now `items`, not
        # `items_live` — allowlisted separately under the items ALLOWLIST
        # above, for the same reason.)
        "SELECT c.id AS copy_id, c.item_id, i.title FROM item_copies c "
        "JOIN items i ON i.id = c.item_id WHERE c.copy_barcode = ?": 1,
    },
    "app/services/trash.py": {
        # expired_count's copy half — finds the trashed copies the view
        # hides, counted only while their item is live.
        "(SELECT COUNT(*) FROM item_copies c JOIN items i "
        "ON i.id = c.item_id AND i.deleted_at IS NULL WHERE": 1,
        # expired_ids' copy half and copy_state — both find trashed copies
        # the view hides (see the items ALLOWLIST entries for this path).
        "SELECT c.id FROM item_copies c JOIN items i ON i.id = c.item_id "
        "AND i.deleted_at IS NULL WHERE": 1,
        "FROM item_copies c JOIN items i ON i.id = c.item_id WHERE c.id = ?": 1,
        # listing()'s copy half — same reasoning as the items ALLOWLIST
        # entry above (a copy group must see a trashed copy no view returns,
        # and its item's live/trashed state for the heading link); same full
        # span for the same reason.
        "FROM item_copies c JOIN items i ON i.id = c.item_id "
        "LEFT JOIN locations l ON l.id = c.location_id "
        "WHERE c.deleted_at IS NOT NULL": 1,
    },
    "app/services/archive.py": {
        # Archive import — same copy_barcode UNIQUE conflict check as
        # _barcode_conflict above, on the import path instead of the API.
        "SELECT item_id FROM item_copies WHERE copy_barcode = ?": 1,
        # _copies_by_item — the portable archive carries Trash — design
        # plan-soft-delete-export. A trashed item's trashed copies must be
        # seen so they are exported too (they are already demoted, so no
        # further transform is needed).
        "c.deleted_at AS deleted_at FROM item_copies c LEFT JOIN locations "
        "ON locations.id = c.location_id "
        "ORDER BY c.item_id, c.copy_number, c.id": 1,
    },
}


def _normalise_file(path: Path) -> tuple[str, list[tuple[int, int]]]:
    """Return (joined_buffer, offsets) for one file.

    Comment-only lines are dropped first (G53). Each string literal's
    prefix is stripped together with its opening quote (see
    `_STRING_PREFIX_QUOTE`), then any remaining `"` characters are stripped,
    whitespace is collapsed to single spaces, and the surviving lines are
    joined with a single separating space — so a statement split across
    adjacent string literals or an f-string fragment that opens on `FROM`
    reads as one piece of text. `offsets` maps each surviving line's start
    position in the joined buffer to its 1-based source line number.
    """
    lines = path.read_text().splitlines()
    buf_parts: list[str] = []
    offsets: list[tuple[int, int]] = []
    pos = 0
    for i, raw_line in enumerate(lines, 1):
        if raw_line.lstrip().startswith("#"):
            continue
        unprefixed = _STRING_PREFIX_QUOTE.sub("", raw_line)
        collapsed = re.sub(r"\s+", " ", unprefixed.replace('"', "")).strip()
        if not collapsed:
            continue
        offsets.append((pos, i))
        buf_parts.append(collapsed)
        pos += len(collapsed) + 1  # +1 for the joining space
    return " ".join(buf_parts), offsets


def _line_for(offsets: list[tuple[int, int]], offset: int) -> int:
    line_no = offsets[0][1] if offsets else 1
    for start, ln in offsets:
        if start > offset:
            break
        line_no = ln
    return line_no


def _spanning(buf: str, sub: str, match: re.Match) -> bool:
    """True when some occurrence of `sub` in `buf` CONTAINS the whole match.

    This is the suppression rule, and it is deliberately not a proximity
    test. An earlier revision checked whether an allowlisted substring
    appeared anywhere in a ±200/300-character window around the match, which
    excused any new direct read that happened to land beside an allowlisted
    statement — two reads in the same short function, or a literal a few
    lines from the view's own CREATE, went unreported. Requiring the entry
    to span the match ties each exemption to the one statement it names.
    """
    idx = buf.find(sub)
    while idx != -1:
        if idx <= match.start() and match.end() <= idx + len(sub):
            return True
        idx = buf.find(sub, idx + 1)
    return False


def _find_violations(
    root: Path, pattern: re.Pattern, allowlist: dict[str, dict[str, int]], message: str
) -> list[str]:
    """Shared scan behind both find_violations() and find_copy_violations().

    One `_spanning` call, one normaliser (`_normalise_file`) — both
    relations scan the same normalised text and suppress the same way, they
    differ only in which pattern and which allowlist they carry (G88).
    """
    violations = []
    for path in sorted((root / "app").rglob("*.py")):
        rel = str(path.relative_to(root))
        buf, offsets = _normalise_file(path)
        allowed = allowlist.get(rel, {})
        for match in pattern.finditer(buf):
            if any(_spanning(buf, sub, match) for sub in allowed):
                continue
            line = _line_for(offsets, match.start())
            violations.append(message.format(path=rel, line=line))
    return violations


def find_violations(root: Path = ROOT) -> list[str]:
    return _find_violations(root, _ITEMS_READ, ALLOWLIST, VIOLATION_MSG)


def find_copy_violations(root: Path = ROOT) -> list[str]:
    """The item_copies twin of find_violations(). Deliberately not folded
    into it. Gates main() from this task onward — see the module docstring
    and main() below."""
    return _find_violations(root, _COPIES_READ, COPIES_ALLOWLIST, COPIES_VIOLATION_MSG)


def _allowlist_mismatches(
    root: Path, pattern: re.Pattern, allowlist: dict[str, dict[str, int]]
) -> list[str]:
    """Shared scan behind both allowlist_mismatches() and
    copy_allowlist_mismatches().

    Every allowlist entry must span exactly the number of reads declared
    beside it. A count of 0 is the stale case — an entry the code no longer
    produces, sitting there silently over-permissive (mirrors
    test_raw_update_allowlist_has_no_stale_entries in
    tests/test_item_write.py). A count ABOVE the declared one is the
    ride-along case: a new direct read written inside text an existing entry
    already covers, which would otherwise be exempted without anyone
    deciding it should be. Both are mismatches and both must fail.
    """
    buf_by_path: dict[str, str] = {}
    for path in sorted((root / "app").rglob("*.py")):
        rel = str(path.relative_to(root))
        buf_by_path[rel] = _normalise_file(path)[0]

    mismatches = []
    for rel, expected in allowlist.items():
        buf = buf_by_path.get(rel, "")
        matches = list(pattern.finditer(buf))
        for sub, want in expected.items():
            got = sum(1 for m in matches if _spanning(buf, sub, m))
            if got != want:
                mismatches.append(
                    f"{rel}: {sub!r} spans {got} read(s), expected {want}"
                )
    return mismatches


def allowlist_mismatches(root: Path = ROOT) -> list[str]:
    return _allowlist_mismatches(root, _ITEMS_READ, ALLOWLIST)


def copy_allowlist_mismatches(root: Path = ROOT) -> list[str]:
    """The item_copies twin of allowlist_mismatches(). Gates main() from
    this task onward, same as find_copy_violations() above — the copies
    allowlist must stay accurate and the copies census must stay at 0."""
    return _allowlist_mismatches(root, _COPIES_READ, COPIES_ALLOWLIST)


def main() -> int:
    violations = find_violations()
    mismatches = allowlist_mismatches()
    copy_violations = find_copy_violations()
    copy_mismatches = copy_allowlist_mismatches()

    if violations:
        print(f"items_live lint: {len(violations)} violation(s)\n")
        for v in violations:
            print(f"  {v}")
    if mismatches:
        print(f"items_live lint: {len(mismatches)} allowlist mismatch(es)\n")
        for m in mismatches:
            print(f"  {m}")
    if copy_violations:
        print(
            f"copies_live lint: {len(copy_violations)} violation(s) — reads the "
            "item_copies table directly instead of copies_live (the "
            "constraint-mirror exemptions in COPIES_ALLOWLIST — the reads "
            "that stay physical to predict a UNIQUE violation, since a "
            "trashed copy still holds its copy_number and copy_barcode — "
            "are the only reads excused)\n"
        )
        for v in copy_violations:
            print(f"  {v}")
    if copy_mismatches:
        print(f"copies_live lint: {len(copy_mismatches)} allowlist mismatch(es)\n")
        for m in copy_mismatches:
            print(f"  {m}")

    if violations or mismatches or copy_violations or copy_mismatches:
        return 1
    print("items_live lint: every read goes through the view.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
