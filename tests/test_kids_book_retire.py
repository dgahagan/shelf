"""The boot-time `kids_book` retirement (`app.database._retire_kids_book`).

Every test here drives the step through `init_db()` / `get_db()`, never a
bare `sqlite3.connect`. Two reasons, and both have bitten before:

- `reparent_children` reads `copies_live`, a TEMP view only `get_db()`
  creates. ~25 test sites call `_run_migrations` on a bare connection and
  stay green only because they never reach a merge.
- The step takes `BEGIN IMMEDIATE`. An open fixture transaction makes it
  wait out the full busy timeout, so seeds are committed before it runs.

Seeds use raw `_insert_item(media_type="kids_book")`, which bypasses the
write funnel — that is the point: the funnel refuses the value this step
exists to remove.
"""



from app.database import _retire_kids_book, _run_migrations, get_db, init_db
from tests.conftest import _assert_ownership_partition, _insert_item


def _kids(db, title="Kids Book", isbn=None, **kwargs):
    return _insert_item(db, title=title, isbn=isbn, media_type="kids_book", **kwargs)


def _tags_of(db, item_id):
    return {
        r["name"]
        for r in db.execute(
            "SELECT t.name FROM tags t JOIN item_tags it ON it.tag_id = t.id "
            "WHERE it.item_id = ?",
            (item_id,),
        )
    }


def _media_types(db):
    return [r["media_type"] for r in db.execute("SELECT media_type FROM items")]


class TestTheRewrite:
    def test_a_kids_book_becomes_a_book_tagged_kids(self, db):
        item = _kids(db, isbn="9789000020010")
        db.commit()

        init_db()

        with get_db() as fresh:
            row = fresh.execute(
                "SELECT media_type FROM items_live WHERE id = ?", (item,)
            ).fetchone()
            assert row["media_type"] == "book"
            assert _tags_of(fresh, item) == {"Kids"}
            assert "kids_book" not in _media_types(fresh)

    def test_the_kids_tag_is_globally_scoped(self, db):
        _kids(db, isbn="9789000020027")
        db.commit()

        init_db()

        with get_db() as fresh:
            scope = fresh.execute(
                "SELECT media_type FROM tags WHERE name = 'Kids'"
            ).fetchone()["media_type"]
            assert scope is None

    def test_an_existing_lower_case_kids_tag_is_reused(self, db):
        item = _kids(db, isbn="9789000020034")
        db.execute("INSERT INTO tags (name) VALUES ('kids')")
        db.commit()

        init_db()

        with get_db() as fresh:
            names = [
                r["name"]
                for r in fresh.execute(
                    "SELECT name FROM tags WHERE name = 'Kids'"
                )
            ]
            assert names == ["kids"], "tags.name is UNIQUE COLLATE NOCASE"
            assert _tags_of(fresh, item) == {"kids"}

    def test_a_row_with_no_copy_row_is_rewritten(self, db):
        """An item with zero item_copies rows is a real state (G86)."""
        item = _kids(db, isbn="9789000020041")
        db.commit()
        with get_db() as check:
            assert check.execute(
                "SELECT COUNT(*) AS c FROM copies_live WHERE item_id = ?", (item,)
            ).fetchone()["c"] == 0

        init_db()

        with get_db() as fresh:
            assert fresh.execute(
                "SELECT media_type FROM items_live WHERE id = ?", (item,)
            ).fetchone()["media_type"] == "book"

    def test_a_trashed_row_is_rewritten_and_stays_trashed(self, db):
        """Leaving one behind means a row whose media_type the write funnel
        refuses if it is ever restored."""
        item = _kids(db, isbn="9789000020058")
        db.execute(
            "UPDATE items SET deleted_at = '2026-01-01' WHERE id = ?", (item,)
        )
        db.commit()

        init_db()

        with get_db() as fresh:
            row = fresh.execute(
                "SELECT media_type, deleted_at FROM items WHERE id = ?", (item,)
            ).fetchone()
            assert row["media_type"] == "book"
            assert row["deleted_at"] == "2026-01-01", "still trashed"
            assert _tags_of(fresh, item) == {"Kids"}


class TestBlankIdentifiers:
    def test_blank_identifiers_are_not_twins_and_come_out_null(self, db):
        """UNIQUE(isbn, media_type) allows one '' row per type, so read
        literally a blank-ISBN kids book and an unrelated blank-ISBN book
        would be twins and get irreversibly merged."""
        book = _insert_item(db, title="Unrelated", isbn="", media_type="book")
        kid = _kids(db, title="Blank Kid", isbn="")
        db.commit()

        init_db()

        with get_db() as fresh:
            assert fresh.execute(
                "SELECT COUNT(*) AS c FROM items_live"
            ).fetchone()["c"] == 2, "no merge happened"
            assert fresh.execute(
                "SELECT isbn FROM items_live WHERE id = ?", (kid,)
            ).fetchone()["isbn"] is None
            assert fresh.execute(
                "SELECT title FROM items_live WHERE id = ?", (book,)
            ).fetchone()["title"] == "Unrelated"

    def test_a_blank_upc_is_not_a_twin_either(self, db):
        _insert_item(db, title="Unrelated", isbn=None, upc="", media_type="book")
        kid = _kids(db, title="Blank UPC Kid", isbn=None, upc="")
        db.commit()

        init_db()

        with get_db() as fresh:
            assert fresh.execute(
                "SELECT COUNT(*) AS c FROM items_live"
            ).fetchone()["c"] == 2
            assert fresh.execute(
                "SELECT upc FROM items_live WHERE id = ?", (kid,)
            ).fetchone()["upc"] is None


class TestTheTwinMerge:
    def test_an_isbn_twin_absorbs_tags_copies_and_history(self, db):
        isbn = "9789000020065"
        twin = _insert_item(db, title="The Book", isbn=isbn, media_type="book")
        kid = _kids(db, title="The Kids Book", isbn=isbn)

        db.execute("INSERT INTO tags (name) VALUES ('Bedtime')")
        bedtime = db.execute(
            "SELECT id FROM tags WHERE name = 'Bedtime'"
        ).fetchone()["id"]
        db.execute(
            "INSERT INTO item_tags (item_id, tag_id) VALUES (?, ?)", (kid, bedtime)
        )
        db.execute(
            "INSERT INTO item_copies (item_id, copy_number, is_primary) "
            "VALUES (?, 1, 1)",
            (kid,),
        )
        db.execute(
            "INSERT INTO scan_log (isbn, result, item_id) VALUES (?, 'added', ?)",
            (isbn, kid),
        )
        db.execute(
            "INSERT INTO reading_log (item_id, status) VALUES (?, 'finished')",
            (kid,),
        )
        db.commit()

        init_db()

        with get_db() as fresh:
            assert fresh.execute(
                "SELECT COUNT(*) AS c FROM items_live"
            ).fetchone()["c"] == 1
            assert _tags_of(fresh, twin) == {"Bedtime", "Kids"}
            assert fresh.execute(
                "SELECT COUNT(*) AS c FROM copies_live WHERE item_id = ?", (twin,)
            ).fetchone()["c"] == 1
            assert fresh.execute(
                "SELECT COUNT(*) AS c FROM scan_log WHERE item_id = ?", (twin,)
            ).fetchone()["c"] == 1
            assert fresh.execute(
                "SELECT COUNT(*) AS c FROM reading_log WHERE item_id = ?", (twin,)
            ).fetchone()["c"] == 1

    def test_copies_are_renumbered_with_one_primary(self, db):
        isbn = "9789000020072"
        twin = _insert_item(db, title="The Book", isbn=isbn, media_type="book")
        kid = _kids(db, title="The Kids Book", isbn=isbn)
        for item in (twin, kid):
            db.execute(
                "INSERT INTO item_copies (item_id, copy_number, is_primary) "
                "VALUES (?, 1, 1)",
                (item,),
            )
        db.commit()

        init_db()

        with get_db() as fresh:
            numbers = [
                r["copy_number"]
                for r in fresh.execute(
                    "SELECT copy_number FROM copies_live WHERE item_id = ? "
                    "ORDER BY copy_number",
                    (twin,),
                )
            ]
            assert numbers == [1, 2]
            assert fresh.execute(
                "SELECT COUNT(*) AS c FROM copies_live "
                "WHERE item_id = ? AND is_primary = 1",
                (twin,),
            ).fetchone()["c"] == 1

    def test_both_open_checkouts_survive(self, db):
        """A user-initiated merge refuses this; a boot step has nobody to
        refuse to, so it keeps both rows."""
        isbn = "9789000020089"
        twin = _insert_item(db, title="The Book", isbn=isbn, media_type="book")
        kid = _kids(db, title="The Kids Book", isbn=isbn)
        borrower = db.execute(
            "INSERT INTO borrowers (name) VALUES ('Reader') RETURNING id"
        ).fetchone()["id"]
        for item in (twin, kid):
            db.execute(
                "INSERT INTO checkouts (item_id, borrower_id) VALUES (?, ?)",
                (item, borrower),
            )
        db.commit()

        init_db()

        with get_db() as fresh:
            open_loans = fresh.execute(
                "SELECT COUNT(*) AS c FROM checkouts "
                "WHERE item_id = ? AND checked_in IS NULL",
                (twin,),
            ).fetchone()["c"]
            assert open_loans == 2, "neither loan may be lost"

    def test_a_trashed_twin_is_still_a_twin(self, db):
        """G107 — the twin lookup predicts a UNIQUE(isbn, media_type)
        collision, and a trashed row still occupies its unique slot. Reading
        the view here would report "no twin", and the rewrite would then hand
        the collision to the database instead of merging.
        """
        isbn = "9789000020171"
        twin = _insert_item(db, title="The Book", isbn=isbn, media_type="book")
        kid = _kids(db, title="The Kids Book", isbn=isbn)
        db.execute(
            "UPDATE items SET deleted_at = '2026-01-01' WHERE id = ?", (twin,)
        )
        db.commit()

        init_db()  # must not raise UNIQUE constraint failed

        with get_db() as fresh:
            assert fresh.execute(
                "SELECT COUNT(*) AS c FROM items WHERE id = ?", (kid,)
            ).fetchone()["c"] == 0, "the kids row merged into the trashed twin"
            row = fresh.execute(
                "SELECT media_type, deleted_at FROM items WHERE id = ?", (twin,)
            ).fetchone()
            assert row["media_type"] == "book"
            assert row["deleted_at"] == "2026-01-01"
            assert _tags_of(fresh, twin) == {"Kids"}
            assert "kids_book" not in _media_types(fresh)

    def test_a_upc_twin_with_a_null_isbn_merges(self, db):
        upc = "0012345678905"
        twin = _insert_item(db, title="The Book", isbn=None, upc=upc, media_type="book")
        _kids(db, title="The Kids Book", isbn=None, upc=upc)
        db.commit()

        init_db()

        with get_db() as fresh:
            assert fresh.execute(
                "SELECT COUNT(*) AS c FROM items_live"
            ).fetchone()["c"] == 1
            assert _tags_of(fresh, twin) == {"Kids"}


class TestOwnershipOnAMerge:
    """`owned = 1` never coexists with wishlist membership, and a one-way
    rewrite must not quietly turn something owned into something wanted."""

    def test_an_owned_kids_book_raises_an_unowned_twin(self, db):
        isbn = "9789000020096"
        twin = _insert_item(
            db, title="The Book", isbn=isbn, media_type="book",
            owned=0, wishlisted=True,
        )
        _kids(db, title="The Kids Book", isbn=isbn, owned=1)
        db.commit()

        init_db()

        with get_db() as fresh:
            assert fresh.execute(
                "SELECT owned FROM items_live WHERE id = ?", (twin,)
            ).fetchone()["owned"] == 1
            assert fresh.execute(
                "SELECT COUNT(*) AS c FROM list_items WHERE item_id = ?", (twin,)
            ).fetchone()["c"] == 0, "an owned row sheds the wishlist membership"
            _assert_ownership_partition(fresh)

    def test_an_unowned_kids_book_keeps_the_want_on_an_unowned_twin(self, db):
        isbn = "9789000020102"
        twin = _insert_item(
            db, title="The Book", isbn=isbn, media_type="book", owned=0
        )
        _kids(db, title="The Kids Book", isbn=isbn, owned=0, wishlisted=True)
        db.commit()

        init_db()

        with get_db() as fresh:
            assert fresh.execute(
                "SELECT COUNT(*) AS c FROM list_items WHERE item_id = ?", (twin,)
            ).fetchone()["c"] == 1
            assert fresh.execute(
                "SELECT owned FROM items_live WHERE id = ?", (twin,)
            ).fetchone()["owned"] == 0


class TestIdempotenceAndLogging:
    def test_the_log_line_names_both_counts(self, db):
        isbn = "9789000020119"
        _insert_item(db, title="The Book", isbn=isbn, media_type="book")
        _kids(db, title="Twin Kid", isbn=isbn)
        _kids(db, title="Lone Kid", isbn="9789000020126")
        db.commit()

        with get_db() as fresh:
            lines = _run_migrations(fresh)

        assert any(
            line == "Retired kids_book: 1 rewritten, 1 merged into existing books"
            for line in lines
        ), lines

    def test_the_line_reaches_log_entries_after_init_db(self, db):
        """G3's deterministic half.

        The record only lands because `init_db` emits it *after* the
        migration transaction has committed and its connection closed. Were
        it emitted inside, `SQLiteHandler`'s own second connection would
        block on the write lock, time out, and the handler would swallow
        the failure — leaving no row and nothing red.

        `app.log_handler` is imported rather than `app.main` (G14): the
        handler resolves `get_db` lazily inside `emit`, so it honours the
        per-test tmp data dir.
        """
        import logging

        from app.log_handler import SQLiteHandler

        _kids(db, isbn="9789000020133")
        db.commit()

        handler = SQLiteHandler()
        db_logger = logging.getLogger("app.database")
        previous_level = db_logger.level
        previous_propagate = db_logger.propagate
        db_logger.addHandler(handler)
        # Production configures INFO in app/main.py; the default here is
        # WARNING, which would filter the record before any handler sees it
        # and make this pin vacuously green.
        db_logger.setLevel(logging.INFO)
        # Another test in this worker process may already have installed the
        # app's own SQLiteHandler on a parent logger, which would write the
        # same record a second time and make the count 2. Stop propagating
        # so exactly one handler — this one — writes.
        db_logger.propagate = False
        try:
            init_db()
        finally:
            db_logger.removeHandler(handler)
            db_logger.setLevel(previous_level)
            db_logger.propagate = previous_propagate

        with get_db() as fresh:
            hits = fresh.execute(
                "SELECT COUNT(*) AS c FROM log_entries "
                "WHERE message LIKE 'Retired kids_book:%'"
            ).fetchone()["c"]
        assert hits == 1, (
            "the line must be emitted by init_db's caller, after the "
            "migration transaction closed (G3)"
        )

    def test_a_second_boot_returns_no_line_and_changes_nothing(self, db):
        isbn = "9789000020140"
        _insert_item(db, title="The Book", isbn=isbn, media_type="book")
        _kids(db, title="Twin Kid", isbn=isbn)
        db.commit()

        init_db()
        with get_db() as fresh:
            before = fresh.execute(
                "SELECT id, title, media_type, owned FROM items ORDER BY id"
            ).fetchall()
            tags_before = fresh.execute(
                "SELECT item_id, tag_id FROM item_tags ORDER BY item_id, tag_id"
            ).fetchall()

        with get_db() as second:
            lines = _run_migrations(second)

        assert not any("Retired kids_book" in line for line in lines), lines
        with get_db() as fresh:
            assert fresh.execute(
                "SELECT id, title, media_type, owned FROM items ORDER BY id"
            ).fetchall() == before
            assert fresh.execute(
                "SELECT item_id, tag_id FROM item_tags ORDER BY item_id, tag_id"
            ).fetchall() == tags_before

    def test_a_fresh_database_creates_no_kids_tag(self, db):
        """The short-circuit returns before the tag insert."""
        init_db()

        with get_db() as fresh:
            assert fresh.execute(
                "SELECT COUNT(*) AS c FROM tags WHERE name = 'Kids'"
            ).fetchone()["c"] == 0


class TestTransactionShape:
    def test_an_empty_game_platforms_table_does_not_wedge_the_boot(self, db):
        """On a fresh database `_seed_game_platforms` leaves an implicit
        transaction open, and a bare BEGIN IMMEDIATE inside one raises
        `cannot start a transaction within a transaction`."""
        _kids(db, isbn="9789000020157")
        db.execute("DELETE FROM game_platforms")
        db.commit()

        init_db()  # must not raise

        with get_db() as fresh:
            assert "kids_book" not in _media_types(fresh)

    def test_the_rows_are_read_under_the_write_lock(self, db):
        """The short-circuit runs unlocked; the re-read must not (G18)."""
        _kids(db, isbn="9789000020164")
        db.commit()

        statements = []
        with get_db() as conn:
            conn.set_trace_callback(statements.append)
            _retire_kids_book(conn)
            conn.set_trace_callback(None)

        begins = [
            i for i, s in enumerate(statements) if "BEGIN IMMEDIATE" in s.upper()
        ]
        reads = [
            i
            for i, s in enumerate(statements)
            if "ORDER BY id" in s and "kids_book" in s
        ]
        assert begins and reads, statements
        assert begins[0] < reads[0], (
            "the row SELECT must run after BEGIN IMMEDIATE — a snapshot "
            "taken before the lock is stale by the time it is granted"
        )
