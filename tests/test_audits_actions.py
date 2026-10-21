"""Audit report row actions: resolve, mark missing, notes, delete (T6).

Refusals the user can correct answer 200 with `data-testid="audit-error"`
(htmx never swaps a 4xx body); a not-found answers 404 with an `HX-Trigger`
toast. Every route is a Collector tool.
"""
import json

import pytest

from app.services import audits, item_copies
from tests.conftest import _insert_borrower, _insert_item, _insert_location
from tests.test_features_gate import HX, off


def _copy(db, item_id, number, location_id):
    return item_copies.insert_copy(db, {
        "item_id": item_id, "copy_number": number, "location_id": location_id,
        "is_primary": 1 if number == 1 else 0,
    })


def _elsewhere(db):
    """An item filed at Den with two copies, seen while auditing Hall."""
    here = _insert_location(db, "Hall")
    there = _insert_location(db, "Den")
    item = _insert_item(db, title="Wanderer", isbn="9780000400001", location_id=there)
    primary = _copy(db, item, 1, there)
    second = _copy(db, item, 2, there)
    audit = audits.create_session(db, scope="location", location_id=here)
    entry = audits.add_entry(db, audit, item_id=item, copy_id=None, location_id=here,
                             raw_code="9780000400001", result="elsewhere")
    db.commit()
    assert audit and entry and primary and second
    return dict(here=here, there=there, item=item, primary=primary, second=second,
                audit=audit, entry=entry)


def _loc(db, copy_id):
    return db.execute("SELECT location_id FROM item_copies WHERE id = ?", (copy_id,)).fetchone()[0]


def _seam(db, item_id):
    return db.execute("SELECT location_id FROM items WHERE id = ?", (item_id,)).fetchone()[0]


def _entries(db, audit, result):
    return db.execute("SELECT * FROM audit_entries WHERE session_id = ? AND result = ?",
                      (audit, result)).fetchall()


def _toast(resp):
    return json.loads(resp.headers["HX-Trigger"])["showToast"]


class TestResolve:
    def test_secondary_copy_moves_alone_and_the_seam_stays(self, editor_client, db):
        s = _elsewhere(db)
        db.execute("UPDATE item_copies SET missing_since = '2020-01-01' WHERE id = ?", (s["second"],))
        db.commit()
        resp = editor_client.post(f"/api/audits/{s['audit']}/resolve",
                                  data={"entry_id": s["entry"], "copy_id": s["second"]})
        assert resp.status_code == 200
        assert _loc(db, s["second"]) == s["here"]
        assert _loc(db, s["primary"]) == s["there"]
        assert _seam(db, s["item"]) == s["there"]
        relocated = _entries(db, s["audit"], "relocated")
        assert len(relocated) == 1
        assert (relocated[0]["copy_id"], relocated[0]["location_id"]) == (s["second"], s["here"])
        assert db.execute("SELECT missing_since FROM item_copies WHERE id = ?",
                          (s["second"],)).fetchone()[0] is None
        assert 'data-audit-section="moved" data-audit-count="1"' in resp.text
        assert 'data-audit-section="elsewhere" data-audit-count="0"' in resp.text

    def test_a_replayed_resolution_moves_no_second_copy(self, editor_client, db):
        s = _elsewhere(db)
        first = editor_client.post(f"/api/audits/{s['audit']}/resolve",
                                   data={"entry_id": s["entry"], "copy_id": s["primary"]})
        assert first.status_code == 200
        replay = editor_client.post(f"/api/audits/{s['audit']}/resolve",
                                    data={"entry_id": s["entry"], "copy_id": s["second"]})
        assert replay.status_code == 200
        assert 'data-testid="audit-error"' in replay.text
        assert "already resolved" in replay.text
        assert _loc(db, s["primary"]) == s["here"]
        assert _loc(db, s["second"]) == s["there"]
        assert len(_entries(db, s["audit"], "relocated")) == 1

    def test_a_rescan_after_a_resolution_can_be_resolved(self, editor_client, db):
        s = _elsewhere(db)
        editor_client.post(f"/api/audits/{s['audit']}/resolve",
                           data={"entry_id": s["entry"], "copy_id": s["primary"]})
        again = audits.add_entry(db, s["audit"], item_id=s["item"], copy_id=None,
                                 location_id=s["here"], raw_code="9780000400001",
                                 result="elsewhere")
        db.commit()
        resp = editor_client.post(f"/api/audits/{s['audit']}/resolve",
                                  data={"entry_id": again, "copy_id": s["second"]})
        assert 'data-testid="audit-error"' not in resp.text
        assert _loc(db, s["second"]) == s["here"]

    def test_primary_copy_moves_the_seam(self, editor_client, db):
        s = _elsewhere(db)
        resp = editor_client.post(f"/api/audits/{s['audit']}/resolve",
                                  data={"entry_id": s["entry"], "copy_id": s["primary"]})
        assert resp.status_code == 200
        assert _loc(db, s["primary"]) == s["here"]
        assert _seam(db, s["item"]) == s["here"]
        assert len(_entries(db, s["audit"], "relocated")) == 1

    def test_the_report_offers_one_button_per_copy(self, editor_client, db):
        s = _elsewhere(db)
        html = editor_client.get(f"/audits/{s['audit']}").text
        for cid in (s["primary"], s["second"]):
            assert f'data-testid="audit-resolve-{cid}"' in html
        assert "It was this one" in html

    def test_a_copy_of_another_item_is_refused_inline(self, editor_client, db):
        s = _elsewhere(db)
        other = _insert_item(db, title="Other", isbn="9780000400002", location_id=s["there"])
        stranger = _copy(db, other, 1, s["there"])
        db.commit()
        resp = editor_client.post(f"/api/audits/{s['audit']}/resolve",
                                  data={"entry_id": s["entry"], "copy_id": stranger})
        assert resp.status_code == 200
        assert 'data-testid="audit-error"' in resp.text
        assert _loc(db, stranger) == s["there"]
        assert _entries(db, s["audit"], "relocated") == []

    def test_a_deleted_location_is_refused_inline(self, editor_client, db):
        from app.services import locations as locations_svc
        s = _elsewhere(db)
        locations_svc.delete_location(db, s["here"])
        db.commit()
        resp = editor_client.post(f"/api/audits/{s['audit']}/resolve",
                                  data={"entry_id": s["entry"], "copy_id": s["second"]})
        assert resp.status_code == 200
        assert "That location was deleted" in resp.text
        assert _loc(db, s["second"]) == s["there"]
        assert _entries(db, s["audit"], "relocated") == []

    def test_unknown_entry_and_copy_are_404_with_a_toast(self, editor_client, db):
        s = _elsewhere(db)
        for data in ({"entry_id": 4242, "copy_id": s["second"]},
                     {"entry_id": s["entry"], "copy_id": 4242},
                     {"entry_id": "", "copy_id": ""}):
            resp = editor_client.post(f"/api/audits/{s['audit']}/resolve", data=data)
            assert resp.status_code == 404, data
            assert _toast(resp)["type"] == "error"
        assert editor_client.post("/api/audits/4242/resolve", data={}).status_code == 404

    def test_a_failure_inside_the_transaction_leaves_the_copy_unmoved(
            self, editor_client, db, monkeypatch):
        s = _elsewhere(db)

        def boom(*a, **k):
            raise RuntimeError("entry write failed")
        monkeypatch.setattr(audits, "add_entry", boom)
        with pytest.raises(RuntimeError):
            editor_client.post(f"/api/audits/{s['audit']}/resolve",
                               data={"entry_id": s["entry"], "copy_id": s["second"]})
        monkeypatch.undo()
        assert _loc(db, s["second"]) == s["there"]
        assert _entries(db, s["audit"], "relocated") == []


def _missing_setup(db):
    here = _insert_location(db, "Hall")
    lost = _insert_item(db, title="Lost", isbn="9780000400011", location_id=here)
    lost_copy = _copy(db, lost, 1, here)
    lost2 = _copy(db, lost, 2, here)
    loaned = _insert_item(db, title="Loaned", isbn="9780000400012", location_id=here)
    loaned_copy = _copy(db, loaned, 1, here)
    borrower = _insert_borrower(db)
    db.execute("INSERT INTO checkouts (item_id, borrower_id) VALUES (?, ?)", (loaned, borrower))
    found = _insert_item(db, title="Found", isbn="9780000400013", location_id=here)
    found_copy = _copy(db, found, 1, here)
    old = _insert_item(db, title="Old Loss", isbn="9780000400014", location_id=here)
    old_copy = _copy(db, old, 1, here)
    db.execute("UPDATE item_copies SET missing_since = '2020-01-01' WHERE id = ?", (old_copy,))
    audit = audits.create_session(db, scope="location", location_id=here)
    audits.add_entry(db, audit, item_id=found, copy_id=found_copy, location_id=here,
                     raw_code="x", result="confirmed")
    db.commit()
    return dict(here=here, audit=audit, lost=lost_copy, lost2=lost2, loaned=loaned_copy,
                found=found_copy, old=old_copy)


def _mark(db, copy_id):
    return db.execute("SELECT missing_since FROM item_copies WHERE id = ?", (copy_id,)).fetchone()[0]


class TestMarkMissing:
    def test_only_expected_and_unaccounted_copies_are_marked(self, editor_client, db):
        s = _missing_setup(db)
        resp = editor_client.post(
            f"/api/audits/{s['audit']}/missing",
            data={"copy_id": [s["lost"], s["loaned"], s["found"], s["old"], 999999]})
        assert resp.status_code == 200
        assert _mark(db, s["lost"]) is not None
        assert _mark(db, s["loaned"]) is None
        assert _mark(db, s["found"]) is None
        assert _mark(db, s["old"]) == "2020-01-01"
        assert _mark(db, s["lost2"]) is None

    def test_a_repeated_param_marks_every_listed_copy(self, editor_client, db):
        s = _missing_setup(db)
        editor_client.post(f"/api/audits/{s['audit']}/missing",
                           data={"copy_id": [s["lost"], s["lost2"]]})
        assert _mark(db, s["lost"]) is not None and _mark(db, s["lost2"]) is not None

    def test_works_on_a_finished_audit(self, editor_client, db):
        s = _missing_setup(db)
        audits.finish_session(db, s["audit"])
        db.commit()
        resp = editor_client.post(f"/api/audits/{s['audit']}/missing", data={"copy_id": [s["lost"]]})
        assert resp.status_code == 200
        assert _mark(db, s["lost"]) is not None

    def test_the_report_renders_the_controls(self, editor_client, db):
        s = _missing_setup(db)
        html = editor_client.get(f"/audits/{s['audit']}").text
        assert 'data-testid="audit-mark-all"' in html
        assert f'data-testid="audit-mark-missing-' in html
        assert f'value="{s["lost"]}"' in html

    def test_a_deleted_location_renders_no_mark_missing(self, editor_client, db):
        from app.services import locations as locations_svc
        s = _missing_setup(db)
        locations_svc.delete_location(db, s["here"])
        db.commit()
        html = editor_client.get(f"/audits/{s['audit']}").text
        assert 'data-testid="audit-mark-all"' not in html
        assert 'data-testid="audit-mark-missing-' not in html

    def test_unknown_audit_is_404(self, editor_client):
        resp = editor_client.post("/api/audits/4242/missing", data={"copy_id": [1]})
        assert resp.status_code == 404 and "HX-Trigger" in resp.headers


class TestNotes:
    def test_save(self, editor_client, db):
        audit = audits.create_session(db, scope="library", location_id=None)
        db.commit()
        resp = editor_client.post(f"/api/audits/{audit}/notes", data={"notes": "Shelf 3 was wet."})
        assert resp.status_code == 200
        assert "Shelf 3 was wet." in resp.text
        assert audits.get_session(db, audit)["notes"] == "Shelf 3 was wet."

    def test_too_long_is_refused_inline_and_saves_nothing(self, editor_client, db):
        audit = audits.create_session(db, scope="library", location_id=None)
        audits.set_notes(db, audit, "keep me")
        db.commit()
        resp = editor_client.post(f"/api/audits/{audit}/notes", data={"notes": "x" * 2001})
        assert resp.status_code == 200
        assert 'data-testid="audit-error"' in resp.text
        assert audits.get_session(db, audit)["notes"] == "keep me"
        ok = editor_client.post(f"/api/audits/{audit}/notes", data={"notes": "y" * 2000})
        assert 'data-testid="audit-error"' not in ok.text

    def test_notes_are_escaped_and_unknown_is_404(self, editor_client, db):
        audit = audits.create_session(db, scope="library", location_id=None)
        db.commit()
        resp = editor_client.post(f"/api/audits/{audit}/notes",
                                  data={"notes": "</textarea><script>1</script>"})
        assert "<script>1" not in resp.text
        assert editor_client.post("/api/audits/4242/notes", data={"notes": "a"}).status_code == 404


class TestDelete:
    def test_editor_is_forbidden(self, editor_client, db):
        s = _missing_setup(db)
        assert editor_client.delete(f"/api/audits/{s['audit']}").status_code == 403
        assert db.execute("SELECT COUNT(*) FROM audit_sessions").fetchone()[0] == 1

    def test_admin_deletes_session_and_entries_but_marks_stay(self, admin_client, db):
        s = _missing_setup(db)
        admin_client.post(f"/api/audits/{s['audit']}/missing", data={"copy_id": [s["lost"]]})
        assert db.execute("SELECT COUNT(*) FROM audit_entries").fetchone()[0] == 1
        resp = admin_client.delete(f"/api/audits/{s['audit']}")
        assert resp.status_code == 200
        assert resp.headers["HX-Redirect"] == "/audits"
        assert db.execute("SELECT COUNT(*) FROM audit_sessions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM audit_entries").fetchone()[0] == 0
        assert _mark(db, s["lost"]) is not None

    def test_unknown_is_404_and_only_admins_see_the_control(self, admin_client, db):
        s = _missing_setup(db)
        assert admin_client.delete("/api/audits/4242").status_code == 404
        assert 'data-testid="audit-delete"' in admin_client.get(f"/audits/{s['audit']}").text

    def test_editor_page_has_no_delete_control(self, editor_client, db):
        s = _missing_setup(db)
        assert 'data-testid="audit-delete"' not in editor_client.get(f"/audits/{s['audit']}").text


class TestUnexpectedLinks:
    def test_add_and_trash_links(self, editor_client, db):
        here = _insert_location(db, "Hall")
        trashed = _insert_item(db, title="Gone", isbn="9780000400021", location_id=here)
        audit = audits.create_session(db, scope="location", location_id=here)
        audits.add_entry(db, audit, item_id=None, copy_id=None, location_id=here,
                         raw_code="1234 5&6", result="not_owned")
        audits.add_entry(db, audit, item_id=trashed, copy_id=None, location_id=here,
                         raw_code="9780000400021", result="in_trash")
        db.commit()
        html = editor_client.get(f"/audits/{audit}").text
        assert 'href="/scan?mode=add&amp;code=1234%205%266"' in html
        assert 'data-testid="audit-add-1"' in html
        assert 'href="/trash"' in html

    def test_a_purged_in_trash_entry_offers_no_trash_link(self, editor_client, db):
        here = _insert_location(db, "Hall")
        audit = audits.create_session(db, scope="location", location_id=here)
        audits.add_entry(db, audit, item_id=None, copy_id=None, location_id=here,
                         raw_code="9780000400022", result="in_trash")
        db.commit()
        html = editor_client.get(f"/audits/{audit}").text
        assert "a record since deleted" in html
        assert 'data-testid="audit-trash-1"' not in html  # the nav's own Trash link stays
        assert "(in Trash)" not in html


class TestFlagOff:
    def test_every_new_route_refuses(self, admin_client, db):
        s = _elsewhere(db)
        off("collector")
        base = f"/api/audits/{s['audit']}"
        calls = (("POST", f"{base}/resolve", {"entry_id": s["entry"], "copy_id": s["second"]}),
                 ("POST", f"{base}/missing", {"copy_id": [s["second"]]}),
                 ("POST", f"{base}/notes", {"notes": "a"}),
                 ("DELETE", base, None))
        for method, path, data in calls:
            resp = admin_client.request(method, path, data=data, headers=HX)
            assert resp.status_code == 403, path
        assert _loc(db, s["second"]) == s["there"]
        assert db.execute("SELECT COUNT(*) FROM audit_sessions").fetchone()[0] == 1
