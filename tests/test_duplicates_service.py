"""The duplicate-grouping service (`app/services/duplicates.py`)."""

import pytest

from app.services import duplicates, item_copies, item_write, trash
from app.services.title_match import fold_title
from tests.conftest import _insert_borrower, _insert_item, _insert_location


def _ids(groups):
    return [g.member_ids for g in groups]


def _book(db, title, **kw):
    """An identifier-less book unless the caller passes one."""
    kw.setdefault("isbn", None)
    return _insert_item(db, title=title, **kw)


def _tag(db, item_id, name):
    tag_id = db.execute("INSERT INTO tags (name) VALUES (?)", (name,)).lastrowid
    db.execute("INSERT INTO item_tags (item_id, tag_id) VALUES (?, ?)", (item_id, tag_id))


class TestFoldTitle:
    @pytest.mark.parametrize("raw, folded", [
        ("The Hobbit", "hobbit"),
        ("hobbit", "hobbit"),
        ("A Tale of Two Cities", "tale of two cities"),
        ("An Instance", "instance"),
        ("The", "the"),
        ("Café", "cafe"),
        ("Philosopher's Stone", "philosophers stone"),
        ("三体", ""),
    ])
    def test_fold(self, raw, folded):
        assert fold_title(raw) == folded


class TestIdentifierGroups:
    def test_isbn10_and_isbn13_twins_group(self, db):
        a = _insert_item(db, title="Dune", isbn="0441013597")
        b = _insert_item(db, title="Dune (Ace)", isbn="9780441013593")
        ident, title, _ = duplicates.groups(db)
        assert _ids(ident) == [(a, b)]
        assert ident[0].key == "9780441013593" and ident[0].kind == "identifier"
        assert title == []

    def test_pre_20_upc_in_isbn_beside_a_filed_upc(self, db):
        a = _insert_item(db, title="Alien", isbn="012345678905", media_type="dvd")
        b = _insert_item(db, title="Alien", isbn=None, upc="0012345678905", media_type="dvd")
        ident, _, _ = duplicates.groups(db)
        assert _ids(ident) == [(a, b)]

    def test_never_across_media_type(self, db):
        _insert_item(db, title="Dune", isbn="9780441013593", media_type="book")
        _insert_item(db, title="Dune", isbn="9780441013593", media_type="ebook")
        assert duplicates.groups(db)[0] == []

    def test_periodical_and_music_families_never_appear(self, db):
        _insert_item(db, title="LP", isbn=None, upc="0012345678905", media_type="vinyl")
        _insert_item(db, title="LP", isbn="012345678905", media_type="vinyl")
        _insert_item(db, title="Mag", isbn=None, upc="0098765432109", media_type="magazine")
        _insert_item(db, title="Mag", isbn="098765432109", media_type="magazine")
        _book(db, "LP", media_type="vinyl")
        _book(db, "LP", media_type="vinyl")
        ident, title, _ = duplicates.groups(db)
        assert ident == [] and title == []

    def test_asin_in_isbn_falls_through_to_upc(self, db):
        a = _insert_item(db, title="X", isbn="B00EXAMPLE", upc="0012345678905", media_type="dvd")
        b = _insert_item(db, title="Y", isbn=None, upc=None, media_type="dvd")
        db.execute("UPDATE items SET isbn = '012345678905' WHERE id = ?", (b,))
        row = db.execute("SELECT * FROM items_live WHERE id = ?", (a,)).fetchone()
        assert duplicates.identifier_key(row) == "0012345678905"
        assert _ids(duplicates.groups(db)[0]) == [(a, b)]

    def test_asin_alone_is_identifier_less(self, db):
        a = _insert_item(db, title="Same", isbn="B00EXAMPLE")
        b = _book(db, "Same")
        row = db.execute("SELECT * FROM items_live WHERE id = ?", (a,)).fetchone()
        assert duplicates.identifier_key(row) is None
        assert _ids(duplicates.groups(db)[1]) == [(a, b)]


class TestTitleGroups:
    def test_article_case_and_author_spelling(self, db):
        # name_key agrees for "J.R.R." and "J. R. R." — both are 'j r r tolkien'.
        a = _book(db, "The Hobbit", authors="J.R.R. Tolkien")
        b = _book(db, "hobbit", authors="J. R. R. Tolkien")
        _, title, _ = duplicates.groups(db)
        assert _ids(title) == [(a, b)]
        assert title[0].kind == "title_author" and title[0].media_type == "book"

    def test_no_author_on_both_sides_groups(self, db):
        a, b = _book(db, "Untitled Notes"), _book(db, "Untitled Notes")
        assert _ids(duplicates.groups(db)[1]) == [(a, b)]

    def test_different_authors_do_not_group(self, db):
        _book(db, "Poems", authors="Ann Smith")
        _book(db, "Poems", authors="Bob Jones")
        assert duplicates.groups(db)[1] == []

    def test_a_row_with_an_identifier_never_joins(self, db):
        _insert_item(db, title="Emma", isbn="9780141439587")
        _book(db, "Emma")
        assert duplicates.groups(db) == ([], [], {"identifier": 0, "title_author": 0})

    def test_cjk_titles_do_not_group(self, db):
        _book(db, "三体")
        _book(db, "三体")
        _book(db, "流浪地球")
        assert duplicates.groups(db)[1] == []

    def test_single_article_token_and_diacritics(self, db):
        a, b = _book(db, "The"), _book(db, "the")
        c, d = _book(db, "Café"), _book(db, "Cafe")
        assert sorted(_ids(duplicates.groups(db)[1])) == [(a, b), (c, d)]


class TestDismissals:
    def _three(self, db):
        return [_book(db, "Twin") for _ in range(3)]

    def test_fully_dismissed_hidden_and_third_member_resurfaces(self, db):
        a, b = _book(db, "Twin"), _book(db, "Twin")
        assert duplicates.dismiss(db, [a, b]) is None
        assert duplicates.groups(db)[1] == []
        c = _book(db, "Twin")
        assert _ids(duplicates.groups(db)[1]) == [(a, b, c)]

    def test_dismiss_writes_every_pair_once(self, db):
        a, b, c = self._three(db)
        assert duplicates.dismiss(db, [c, a, b]) is None
        pairs = db.execute(
            "SELECT item_a_id, item_b_id FROM duplicate_dismissals ORDER BY 1, 2"
        ).fetchall()
        assert [tuple(p) for p in pairs] == [(a, b), (a, c), (b, c)]
        assert duplicates.dismiss(db, [a, b, c]) is None
        assert db.execute("SELECT COUNT(*) FROM duplicate_dismissals").fetchone()[0] == 3

    def test_restore_unhides(self, db):
        a, b = _book(db, "Twin"), _book(db, "Twin")
        duplicates.dismiss(db, [a, b])
        duplicates.restore(db, b, a)
        assert _ids(duplicates.groups(db)[1]) == [(a, b)]
        assert duplicates.dismissed_pairs(db) == []

    def test_trash_restore_and_purge(self, db):
        a, b, c = self._three(db)
        duplicates.dismiss(db, [a, b])
        assert item_write.trash_item(db, c)
        # c gone, and {a, b} fully dismissed: hidden.
        assert duplicates.groups(db)[1] == []
        assert item_write.trash_item(db, b)
        assert duplicates.dismissed_pairs(db) == []
        assert db.execute("SELECT COUNT(*) FROM duplicate_dismissals").fetchone()[0] == 1
        assert item_write.restore_item(db, b)
        assert duplicates.groups(db)[1] == []
        assert [(p["a_id"], p["b_id"]) for p in duplicates.dismissed_pairs(db)] == [(a, b)]
        assert item_write.trash_item(db, b)
        assert trash.purge_item(db, b)
        assert db.execute("SELECT COUNT(*) FROM duplicate_dismissals").fetchone()[0] == 0

    def test_dismiss_refuses_a_trashed_or_unknown_id(self, db):
        a, b = _book(db, "Twin"), _book(db, "Twin")
        item_write.trash_item(db, b)
        msg = "One of these records no longer exists — reload the page"
        assert duplicates.dismiss(db, [a, b]) == msg
        assert duplicates.dismiss(db, [a, 999999]) == msg
        assert db.execute("SELECT COUNT(*) FROM duplicate_dismissals").fetchone()[0] == 0

    @pytest.mark.parametrize("raw, exact, answer", [
        ([1], None, "Pick at least two records"),
        ([1, 1], None, "Pick at least two records"),
        (list(range(1, 52)), None, "Too many records in one group"),
        ([1, "2"], None, "Invalid request"),
        ([1, True], None, "Invalid request"),
        ([1, 0], None, "Invalid request"),
        ([1, 2.0], None, "Invalid request"),
        ("1,2", None, "Invalid request"),
        (None, None, "Invalid request"),
        ([1, 2, 3], 2, "Invalid request"),
        ([3, 1, 2], None, [1, 2, 3]),
        ([2, 1], 2, [1, 2]),
        (list(range(1, 51)), None, list(range(1, 51))),
    ])
    def test_parse_ids(self, raw, exact, answer):
        assert duplicates.parse_ids(raw, exact=exact) == answer


class TestCards:
    def test_member_facts(self, db):
        shelf, attic = _insert_location(db, "Shelf"), _insert_location(db, "Attic")
        a = _book(db, "Twin", publisher="Ace", description="d" * 10)
        b = _book(db, "Twin")
        item_copies.insert_copy(db, {"item_id": a, "copy_number": 1, "location_id": shelf})
        item_copies.insert_copy(db, {"item_id": a, "copy_number": 2, "location_id": attic})
        gone = item_copies.insert_copy(db, {"item_id": a, "copy_number": 3, "location_id": attic})
        item_copies.trash_copy(db, gone)
        _tag(db, a, "signed")
        _tag(db, a, "gift")
        borrower = _insert_borrower(db)
        db.execute("INSERT INTO checkouts (item_id, borrower_id) VALUES (?, ?)", (b, borrower))

        (group,) = duplicates.groups(db)[1]
        first, second = group.members
        assert (first.copy_count, first.rooms, first.tag_count, first.on_loan) == (
            2, ("Attic", "Shelf"), 2, False)
        assert (second.copy_count, second.rooms, second.tag_count, second.on_loan) == (
            0, (), 0, True)
        assert first.filled == ("description", "publisher")
        assert dict(first.values)["publisher"] == "Ace"
        assert second.filled == ()


class TestOrderingAndCap:
    def test_order_by_title_then_id_and_cap(self, db):
        for t in ("Zeta", "alpha", "Mid"):
            _book(db, t)
            _book(db, t)
        _, title, hidden = duplicates.groups(db)
        assert [g.members[0].title for g in title] == ["alpha", "Mid", "Zeta"]
        _, capped, hidden = duplicates.groups(db, cap=2)
        assert [g.members[0].title for g in capped] == ["alpha", "Mid"]
        assert hidden == {"identifier": 0, "title_author": 1}

    def test_dismissed_pairs_newest_first(self, db):
        a, b, c, d = (_book(db, t) for t in ("One", "One", "Two", "Two"))
        duplicates.dismiss(db, [a, b])
        duplicates.dismiss(db, [c, d])
        db.execute("UPDATE duplicate_dismissals SET created_at = '2020-01-01' "
                   "WHERE item_a_id = ?", (a,))
        pairs = duplicates.dismissed_pairs(db)
        assert [(p["a_id"], p["b_id"]) for p in pairs] == [(c, d), (a, b)]
        assert pairs[0]["a_title"] == "Two"


def test_statement_count_does_not_grow_with_groups(db):
    def count(n_groups):
        db.execute("DELETE FROM items")
        for i in range(n_groups):
            a, b = _book(db, f"Twin {i}"), _book(db, f"Twin {i}")
            item_copies.insert_copy(db, {"item_id": a, "copy_number": 1})
        statements = []
        db.set_trace_callback(statements.append)
        try:
            ident, title, _ = duplicates.groups(db)
        finally:
            db.set_trace_callback(None)
        assert len(title) == n_groups
        return len(statements)

    assert count(2) == count(20)
