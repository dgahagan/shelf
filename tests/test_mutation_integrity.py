"""Merging two catalogue rows must not destroy the merged row's records (#86).

Every child table of `items` is ON DELETE CASCADE, so a merge that deletes the
row without re-pointing its children loses them with no error. These tests pin
each child table, and the conflict cases where a naive UPDATE would raise
IntegrityError instead.
"""

import pytest

from tests.conftest import (
    _assert_ownership_partition,
    _insert_borrower,
    _insert_item,
    _insert_location,
)


def _tag(db, item_id, name):
    db.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
    tag_id = db.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()["id"]
    db.execute("INSERT INTO item_tags (item_id, tag_id) VALUES (?, ?)", (item_id, tag_id))
    return tag_id


def _copy(db, item_id, number, **kwargs):
    fields = {"item_id": item_id, "copy_number": number}
    fields.update(kwargs)
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    cursor = db.execute(f"INSERT INTO item_copies ({cols}) VALUES ({marks})", list(fields.values()))
    return cursor.lastrowid


def _merge(client, keep, others):
    return client.post("/api/items/merge", json={"keep_id": keep, "merge_ids": others}).json()


class TestChildRecordsSurviveAMerge:
    def test_loan_tag_and_link_all_survive_on_the_kept_row(self, admin_client, db):
        """The case from the issue: one merge, three child tables, no warning."""
        keep = _insert_item(db, title="Keep", isbn=None)
        other = _insert_item(db, title="Other", isbn=None)
        third = _insert_item(db, title="Linked", isbn=None)
        borrower = _insert_borrower(db, "Reader")
        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, due_date) VALUES (?, ?, '2026-01-01')",
            (other, borrower),
        )
        tag_id = _tag(db, other, "signed")
        db.execute("INSERT INTO item_links (item_a_id, item_b_id) VALUES (?, ?)", (other, third))
        db.commit()

        assert _merge(admin_client, keep, [other]) == {"ok": True, "merged": 1}

        assert db.execute(
            "SELECT borrower_id FROM checkouts WHERE item_id = ?", (keep,)
        ).fetchone()["borrower_id"] == borrower
        assert db.execute(
            "SELECT 1 FROM item_tags WHERE item_id = ? AND tag_id = ?", (keep, tag_id)
        ).fetchone() is not None
        assert db.execute(
            "SELECT 1 FROM item_links WHERE item_a_id = ? AND item_b_id = ?", (keep, third)
        ).fetchone() is not None

    def test_physical_copies_survive_with_their_acquisition_detail(self, admin_client, db):
        """item_copies is the 0.36.0 casualty — price and provenance are unrecoverable."""
        keep = _insert_item(db, title="Keep", isbn=None)
        other = _insert_item(db, title="Other", isbn=None)
        location = _insert_location(db, "Shelf A")
        _copy(db, keep, 1, is_primary=1)
        _copy(db, other, 1, is_primary=1, location_id=location, acquisition_price=42.5,
              provenance="Grandfather's copy", condition="good", copy_barcode="C-1")
        db.commit()

        assert _merge(admin_client, keep, [other]) == {"ok": True, "merged": 1}

        rows = db.execute(
            "SELECT copy_number, is_primary, acquisition_price, provenance, copy_barcode "
            "FROM item_copies WHERE item_id = ? ORDER BY copy_number", (keep,)
        ).fetchall()
        assert len(rows) == 2
        moved = rows[1]
        assert moved["acquisition_price"] == 42.5
        assert moved["provenance"] == "Grandfather's copy"
        assert moved["copy_barcode"] == "C-1"
        # Renumbered above the kept row's own copies, and demoted: the partial
        # unique index allows exactly one primary per item.
        assert moved["copy_number"] == 2
        assert moved["is_primary"] == 0
        assert db.execute(
            "SELECT COUNT(*) AS n FROM item_copies WHERE item_id = ? AND is_primary = 1", (keep,)
        ).fetchone()["n"] == 1

    def test_merged_row_primary_is_kept_when_the_target_has_none(self, admin_client, db):
        keep = _insert_item(db, title="Keep", isbn=None)
        other = _insert_item(db, title="Other", isbn=None)
        _copy(db, other, 1, is_primary=1)
        db.commit()

        assert _merge(admin_client, keep, [other]) == {"ok": True, "merged": 1}

        assert db.execute(
            "SELECT COUNT(*) AS n FROM item_copies WHERE item_id = ? AND is_primary = 1", (keep,)
        ).fetchone()["n"] == 1


class TestConflictsThatWouldRaise:
    def test_a_tag_both_rows_carry_is_not_duplicated(self, admin_client, db):
        keep = _insert_item(db, title="Keep", isbn=None)
        other = _insert_item(db, title="Other", isbn=None)
        tag_id = _tag(db, keep, "signed")
        _tag(db, other, "signed")
        _tag(db, other, "first-edition")
        db.commit()

        assert _merge(admin_client, keep, [other]) == {"ok": True, "merged": 1}

        names = [
            row["name"] for row in db.execute(
                "SELECT t.name FROM item_tags it JOIN tags t ON t.id = it.tag_id "
                "WHERE it.item_id = ? ORDER BY t.name", (keep,)
            ).fetchall()
        ]
        assert names == ["first-edition", "signed"]
        assert db.execute(
            "SELECT COUNT(*) AS n FROM item_tags WHERE item_id = ? AND tag_id = ?", (keep, tag_id)
        ).fetchone()["n"] == 1

    def test_a_link_between_the_two_rows_does_not_become_a_self_link(self, admin_client, db):
        keep = _insert_item(db, title="Keep", isbn=None)
        other = _insert_item(db, title="Other", isbn=None)
        db.execute("INSERT INTO item_links (item_a_id, item_b_id) VALUES (?, ?)", (keep, other))
        db.commit()

        assert _merge(admin_client, keep, [other]) == {"ok": True, "merged": 1}

        assert db.execute(
            "SELECT COUNT(*) AS n FROM item_links WHERE item_a_id = ? OR item_b_id = ?", (keep, keep)
        ).fetchone()["n"] == 0

    def test_a_link_both_rows_share_is_not_duplicated(self, admin_client, db):
        keep = _insert_item(db, title="Keep", isbn=None)
        other = _insert_item(db, title="Other", isbn=None)
        third = _insert_item(db, title="Shared", isbn=None)
        db.execute("INSERT INTO item_links (item_a_id, item_b_id) VALUES (?, ?)", (keep, third))
        db.execute("INSERT INTO item_links (item_a_id, item_b_id) VALUES (?, ?)", (other, third))
        db.commit()

        assert _merge(admin_client, keep, [other]) == {"ok": True, "merged": 1}

        assert db.execute(
            "SELECT COUNT(*) AS n FROM item_links WHERE item_a_id = ? AND item_b_id = ?", (keep, third)
        ).fetchone()["n"] == 1
        assert db.execute("SELECT COUNT(*) AS n FROM item_links").fetchone()["n"] == 1

    def test_copy_numbers_are_renumbered_rather_than_colliding(self, admin_client, db):
        keep = _insert_item(db, title="Keep", isbn=None)
        other = _insert_item(db, title="Other", isbn=None)
        _copy(db, keep, 1, is_primary=1)
        _copy(db, keep, 2)
        _copy(db, other, 1, is_primary=1)
        _copy(db, other, 2)
        db.commit()

        assert _merge(admin_client, keep, [other]) == {"ok": True, "merged": 1}

        numbers = [
            row["copy_number"] for row in db.execute(
                "SELECT copy_number FROM item_copies WHERE item_id = ? ORDER BY copy_number", (keep,)
            ).fetchall()
        ]
        assert numbers == [1, 2, 3, 4]


class TestUnsafeMergesAreRefused:
    def test_merging_an_item_into_itself_does_not_delete_it(self, admin_client, db):
        keep = _insert_item(db, title="Keep", isbn=None)
        db.commit()

        body = _merge(admin_client, keep, [keep])
        assert body["ok"] is False
        assert body["message"] == "Cannot merge an item into itself"
        assert db.execute("SELECT 1 FROM items WHERE id = ?", (keep,)).fetchone() is not None

    def test_repeated_ids_are_merged_once(self, admin_client, db):
        keep = _insert_item(db, title="Keep", isbn=None)
        other = _insert_item(db, title="Other", isbn=None)
        db.commit()

        assert _merge(admin_client, keep, [other, other, other]) == {"ok": True, "merged": 1}

    def test_two_open_loans_are_refused_before_anything_is_written(self, admin_client, db):
        keep = _insert_item(db, title="Keep", isbn=None)
        other = _insert_item(db, title="Other", isbn=None)
        borrower = _insert_borrower(db, "Reader")
        for item_id in (keep, other):
            db.execute(
                "INSERT INTO checkouts (item_id, borrower_id) VALUES (?, ?)", (item_id, borrower)
            )
        db.commit()

        body = _merge(admin_client, keep, [other])
        assert body["ok"] is False
        assert "both on loan" in body["message"]
        assert '"Keep"' in body["message"] and '"Other"' in body["message"]
        assert db.execute("SELECT 1 FROM items WHERE id = ?", (other,)).fetchone() is not None
        assert db.execute("SELECT COUNT(*) AS n FROM checkouts").fetchone()["n"] == 2

    def test_a_closed_loan_on_each_row_is_not_a_conflict(self, admin_client, db):
        keep = _insert_item(db, title="Keep", isbn=None)
        other = _insert_item(db, title="Other", isbn=None)
        borrower = _insert_borrower(db, "Reader")
        for item_id in (keep, other):
            db.execute(
                "INSERT INTO checkouts (item_id, borrower_id, checked_in) "
                "VALUES (?, ?, '2026-01-02')", (item_id, borrower)
            )
        db.commit()

        assert _merge(admin_client, keep, [other]) == {"ok": True, "merged": 1}
        assert db.execute(
            "SELECT COUNT(*) AS n FROM checkouts WHERE item_id = ?", (keep,)
        ).fetchone()["n"] == 2


class TestTheReportedCount:
    def test_an_id_that_names_no_row_is_not_counted_as_merged(self, admin_client, db):
        keep = _insert_item(db, title="Keep", isbn=None)
        other = _insert_item(db, title="Other", isbn=None)
        db.commit()

        assert _merge(admin_client, keep, [other, 999_999]) == {"ok": True, "merged": 1}

    def test_merging_only_bogus_ids_reports_nothing_merged(self, admin_client, db):
        keep = _insert_item(db, title="Keep", isbn=None)
        db.commit()

        assert _merge(admin_client, keep, [999_998, 999_999]) == {"ok": True, "merged": 0}


class TestRoleGating:
    @pytest.mark.parametrize("client_name", ["editor_client", "viewer_client"])
    def test_only_an_admin_may_merge(self, request, client_name, db):
        client = request.getfixturevalue(client_name)
        keep = _insert_item(db, title="Keep", isbn=None)
        other = _insert_item(db, title="Other", isbn=None)
        db.commit()

        resp = client.post("/api/items/merge", json={"keep_id": keep, "merge_ids": [other]})
        assert resp.status_code == 403
        assert db.execute("SELECT 1 FROM items WHERE id = ?", (other,)).fetchone() is not None


class TestWishlistMembershipSurvivesAMerge:
    """The live defect this plan closes (#86's seventh child table).

    `reparent_children` covered six of the child tables and not `list_items`,
    so merging a wishlisted row into another row silently dropped the want —
    in the merge route today, and irreversibly in the boot-time kids_book
    rewrite that lands next.
    """

    def _wishlist(self, db, item_id):
        db.execute(
            "INSERT OR IGNORE INTO list_items (list_id, item_id) "
            "SELECT id, ? FROM lists WHERE slug = 'wishlist'",
            (item_id,),
        )

    def _is_wishlisted(self, db, item_id):
        return db.execute(
            "SELECT 1 FROM list_items li JOIN lists l ON l.id = li.list_id "
            "WHERE l.slug = 'wishlist' AND li.item_id = ?",
            (item_id,),
        ).fetchone() is not None

    def test_merging_a_wishlisted_row_keeps_the_want_on_an_unowned_keeper(
        self, admin_client, db
    ):
        keep = _insert_item(db, title="Keep", isbn=None, owned=0)
        other = _insert_item(db, title="Other", isbn=None, owned=0)
        self._wishlist(db, other)
        db.commit()

        assert _merge(admin_client, keep, [other]) == {"ok": True, "merged": 1}

        assert self._is_wishlisted(db, keep), (
            "the merged row's wishlist membership must move onto the kept "
            "row — before this it was dropped with the cascade"
        )

    def test_an_owned_keeper_sheds_the_inherited_want(self, admin_client, db):
        """`owned = 1` never coexists with wishlist membership."""
        keep = _insert_item(db, title="Keep", isbn=None, owned=1)
        other = _insert_item(db, title="Other", isbn=None, owned=0)
        self._wishlist(db, other)
        db.commit()

        assert _merge(admin_client, keep, [other]) == {"ok": True, "merged": 1}

        assert not self._is_wishlisted(db, keep)
        _assert_ownership_partition(db)

    def test_a_list_both_rows_are_on_collapses_to_one_row(self, admin_client, db):
        keep = _insert_item(db, title="Keep", isbn=None, owned=0)
        other = _insert_item(db, title="Other", isbn=None, owned=0)
        self._wishlist(db, keep)
        self._wishlist(db, other)
        db.commit()

        assert _merge(admin_client, keep, [other]) == {"ok": True, "merged": 1}

        rows = db.execute(
            "SELECT COUNT(*) AS c FROM list_items WHERE item_id = ?", (keep,)
        ).fetchone()["c"]
        assert rows == 1, "PRIMARY KEY (list_id, item_id) — no duplicate row"
        assert db.execute(
            "SELECT COUNT(*) AS c FROM list_items WHERE item_id = ?", (other,)
        ).fetchone()["c"] == 0


class TestAuditEntriesSurviveAMerge:
    """A merge re-points the merged-away record's audit entries (duplicate-audit T2)."""

    def test_entries_follow_the_kept_row_and_prices_go(self, admin_client, db):
        from app.services import audits, item_copies

        loc = _insert_location(db, "Shelf A")
        keep = _insert_item(db, title="Keep", isbn=None)
        husk = _insert_item(db, title="Husk", isbn=None)
        # The kept copy shelves elsewhere: an item-level entry accounts for
        # every copy of its item at the audited location, so a second copy
        # there would legitimately raise Found after the merge.
        keep_copy = item_copies.insert_copy(
            db, {"item_id": keep, "copy_number": 1, "location_id": None})
        husk_copy = item_copies.insert_copy(
            db, {"item_id": husk, "copy_number": 1, "location_id": loc})
        audit_id = audits.create_session(db, scope="location", location_id=loc)
        entries = [
            audits.add_entry(db, audit_id, item_id=husk, copy_id=husk_copy,
                             location_id=loc, raw_code="C1", result="confirmed"),
            audits.add_entry(db, audit_id, item_id=husk, copy_id=None,
                             location_id=loc, raw_code="I1", result="confirmed"),
        ]
        db.execute("INSERT INTO price_history (item_id, price) VALUES (?, 9.5)", (husk,))
        db.commit()

        assert keep_copy and len(entries) == 2
        before = audits.build_report(db, audits.get_session(db, audit_id)).counts["found"]
        assert before > 0

        assert _merge(admin_client, keep, [husk]) == {"ok": True, "merged": 1}

        rows = db.execute(
            "SELECT item_id FROM audit_entries WHERE session_id = ?", (audit_id,)
        ).fetchall()
        assert len(rows) == 2 and {r["item_id"] for r in rows} == {keep}

        report = audits.build_report(db, audits.get_session(db, audit_id))
        assert report.counts["found"] == before
        assert not any(line.get("item_id") is None for line in report.found), (
            "no entry may render as a record since deleted")

        assert db.execute(
            "SELECT 1 FROM price_history WHERE item_id = ?", (husk,)
        ).fetchone() is None


class TestARefusedMergeChangesNothing:
    """A later target's refusal must undo the targets merged before it (duplicate-audit B1)."""

    def test_three_way_merge_refused_on_the_last_target(self, admin_client, db):
        loc = _insert_location(db, "Shelf A")
        keep = _insert_item(db, title="Atomic triplet", isbn=None)
        first = _insert_item(db, title="Atomic triplet", isbn=None, publisher="First publisher")
        bad = _insert_item(db, title="Atomic triplet", isbn=None)
        # The invalid value is planted under the write funnel, as legacy rows hold it.
        db.execute("UPDATE items SET isbn = 'not-an-isbn' WHERE id = ?", (bad,))
        from app.services import item_copies
        item_copies.insert_copy(db, {"item_id": first, "copy_number": 1, "location_id": loc})
        db.commit()
        before = [dict(r) for r in db.execute(
            "SELECT id, publisher, isbn FROM items ORDER BY id").fetchall()]
        assert len(before) == 3

        result = _merge(admin_client, keep, [first, bad])

        assert result["ok"] is False and result["item_id"] == bad
        after = [dict(r) for r in db.execute(
            "SELECT id, publisher, isbn FROM items ORDER BY id").fetchall()]
        assert after == before
        assert db.execute(
            "SELECT item_id FROM item_copies WHERE item_id = ?", (first,)
        ).fetchone() is not None
