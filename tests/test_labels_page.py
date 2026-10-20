"""The Labels page: bulk assign, the count fragment, and the printable sheet."""

import html
import re
import time

from starlette.routing import Match

from app.database import set_setting
from app.services import copy_labels, item_copies
from tests.conftest import _insert_item, _insert_location
from tests.test_features_gate import assert_disabled_page, off


def _bare_copies(db, n, title="Book"):
    """`n` barcode-less copies spread over items of three copies each."""
    ids = []
    item_id = None
    for i in range(n):
        if i % 3 == 0:
            item_id = _insert_item(db, title=f"{title} {i // 3}", isbn=None)
        ids.append(item_copies.insert_copy(db, {
            "item_id": item_id, "copy_number": i % 3 + 1, "is_primary": 1 if i % 3 == 0 else 0,
        }))
    db.commit()
    return ids


def _assign(client):
    resp = client.post("/api/labels/assign", data={"scope": "all"})
    assert resp.status_code == 200
    return resp


def _href(text, testid):
    m = re.search(rf'href="([^"]+)"[^>]*data-testid="{testid}"', text)
    assert m, f"no {testid} link"
    return html.unescape(m.group(1))


def _symbols(text):
    """Code 128 symbols on the page — the nav's icon SVGs are not labels."""
    return text.count('<svg xmlns="http://www.w3.org/2000/svg" width=')


def _labels(text):
    return re.findall(r'data-label="([^"]+)"', text)


class TestPage:
    def test_editor_sees_the_page(self, editor_client):
        resp = editor_client.get("/labels")
        assert resp.status_code == 200
        assert 'data-testid="labels-assign"' in resp.text
        assert 'action="/labels/sheet"' in resp.text

    def test_viewer_is_turned_away(self, viewer_client):
        resp = viewer_client.get("/labels", follow_redirects=False)
        assert resp.status_code in (303, 403)
        assert 'data-testid="labels-assign"' not in resp.text

    def test_disabled_page_when_collector_is_off(self, admin_client):
        off("collector")
        assert_disabled_page(admin_client.get("/labels"), "Collector tools", enable_form=True)

    def test_count_fragment_reports_the_scope(self, editor_client, db):
        _bare_copies(db, 4)
        resp = editor_client.get("/api/labels/count?scope=all")
        assert "4 copies would get a code" in resp.text
        assert "Choose a location" in editor_client.get("/api/labels/count?scope=location").text

    def test_garbage_scope_is_400(self, editor_client):
        assert editor_client.get("/api/labels/count?scope=nope").status_code == 400


class TestAssign:
    def test_assign_names_the_range_and_links_the_sheet(self, editor_client, db):
        _bare_copies(db, 3)
        resp = _assign(editor_client)
        assert "3 codes assigned, SHF-000001 to SHF-000003" in resp.text
        assert _href(resp.text, "labels-assigned-sheet") == "/labels/sheet?from=SHF-000001&to=SHF-000003"

    def test_assign_tells_the_count_line_to_reread(self, editor_client, db):
        """qa-copy-labels Obs 3: the count line re-reads on this event, so the
        page no longer says "N copies would get a code" beside the result."""
        _bare_copies(db, 2)
        assert _assign(editor_client).headers.get("HX-Trigger") == "labels-assigned"
        page = editor_client.get("/labels").text
        assert "labels-assigned from:body" in page

    def test_nothing_to_assign(self, editor_client):
        assert "Nothing to assign" in _assign(editor_client).text

    def test_invalid_prefix_warning_lands_outside_the_lock(self, editor_client, db):
        """G3: the route reads the prefix before assign_codes' write lock."""
        ids = _bare_copies(db, 1)
        set_setting(db, copy_labels.PREFIX_KEY, "not ok")
        db.commit()

        start = time.monotonic()
        resp = _assign(editor_client)
        elapsed = time.monotonic() - start

        assert "SHF-000001" in resp.text
        assert db.execute("SELECT copy_barcode FROM item_copies WHERE id = ?",
                          (ids[0],)).fetchone()[0] == "SHF-000001"
        assert db.execute("SELECT COUNT(*) FROM log_entries WHERE message LIKE "
                          "'Copy label prefix%'").fetchone()[0] == 1
        assert elapsed < 2.0


class TestSheet:
    def test_one_svg_per_code(self, editor_client, db):
        _bare_copies(db, 5)
        _assign(editor_client)
        resp = editor_client.get("/labels/sheet?from=SHF-000001&to=SHF-000005")
        assert resp.status_code == 200
        assert _symbols(resp.text) == 5
        assert _labels(resp.text) == [f"SHF-{n:06d}" for n in range(1, 6)]
        assert "size: letter" in resp.text

    def test_a4_preset_and_unknown_preset(self, editor_client, db):
        _bare_copies(db, 1)
        _assign(editor_client)
        assert "size: A4" in editor_client.get("/labels/sheet?preset=averyl7160").text
        assert "size: letter" in editor_client.get("/labels/sheet?preset=bogus").text

    def test_start_position_leaves_blank_cells(self, editor_client, db):
        _bare_copies(db, 2)
        _assign(editor_client)
        text = editor_client.get("/labels/sheet?start=5").text
        first_label = text.index("data-label=")
        assert text[:first_label].count("data-label-blank") == 4

    def test_start_is_clamped_to_the_sheet(self, editor_client, db):
        _bare_copies(db, 1)
        _assign(editor_client)
        text = editor_client.get("/labels/sheet?start=99").text
        assert text.count("data-label-blank") == 29

    def test_thirty_one_labels_make_two_sheets(self, editor_client, db):
        _bare_copies(db, 31)
        _assign(editor_client)
        text = editor_client.get("/labels/sheet").text
        assert text.count("data-sheet") == 2
        assert "break-before: page" in text

    def test_a_code_too_long_for_the_label_prints_as_text(self, editor_client, db):
        """qa-copy-labels Obs 2: below the minimum module the symbol is squeezed
        past decoding, so the cell carries the code as text and a note."""
        item_id = _insert_item(db, title="Long", isbn=None)
        item_copies.insert_copy(db, {"item_id": item_id, "copy_number": 1, "is_primary": 1,
                                     "copy_barcode": "LONG-" + "A" * 59})
        db.commit()
        for preset in ("avery5160", "averyl7160"):
            text = editor_client.get(f"/labels/sheet?item_id={item_id}&preset={preset}").text
            assert _symbols(text) == 0
            assert "Code too long to print as a barcode" in text
            assert "LONG-" + "A" * 59 in text

    def test_print_form_options_carry_labels_per_sheet(self, editor_client):
        """qa-copy-labels Obs 5: labels.js caps Start at the chosen stock."""
        text = editor_client.get("/labels").text
        assert 'value="avery5160" data-per-sheet="30"' in text
        assert 'value="averyl7160" data-per-sheet="21"' in text

    def test_empty_selection_says_so(self, editor_client):
        text = editor_client.get("/labels/sheet?item_id=999").text
        assert "No labels match this selection" in text
        assert _symbols(text) == 0

    def test_garbage_integer_is_400(self, editor_client):
        assert editor_client.get("/labels/sheet?offset=abc").status_code == 400
        assert editor_client.get("/labels/sheet?start=-1").status_code == 400

    def test_item_selector(self, editor_client, db):
        _bare_copies(db, 6)
        _assign(editor_client)
        item_id = db.execute("SELECT item_id FROM item_copies WHERE copy_barcode = 'SHF-000004'").fetchone()[0]
        assert _labels(editor_client.get(f"/labels/sheet?item_id={item_id}").text) == [
            "SHF-000004", "SHF-000005", "SHF-000006"]

    def test_direct_offset_renders_the_remainder(self, editor_client, db):
        _bare_copies(db, 301)
        _assign(editor_client)
        assert len(_labels(editor_client.get("/labels/sheet?offset=300").text)) == 1


class TestContinuation:
    """codex-R2: the rows past the cap are reached through the rendered UI."""

    def test_next_and_previous_links_walk_301_labels(self, editor_client, db):
        _bare_copies(db, 301)
        sheet_url = _href(_assign(editor_client).text, "labels-assigned-sheet")

        first = editor_client.get(sheet_url + "&start=3").text
        assert len(_labels(first)) == 300
        assert "labels-sheet-prev" not in first
        next_url = _href(first, "labels-sheet-next")
        assert "from=SHF-000001" in next_url and "to=SHF-000301" in next_url

        second = editor_client.get(next_url).text
        assert _labels(second) == ["SHF-000301"]
        assert "data-label-blank" not in second  # start applies to the first chunk only
        assert "labels-sheet-next" not in second

        back = editor_client.get(_href(second, "labels-sheet-prev")).text
        assert _labels(back) == _labels(first)

    def test_exactly_three_hundred_has_no_next(self, editor_client, db):
        _bare_copies(db, 300)
        sheet_url = _href(_assign(editor_client).text, "labels-assigned-sheet")
        assert "labels-sheet-next" not in editor_client.get(sheet_url).text


def test_route_order_reaches_the_sheet_handler(client):
    """G119: the first full match for GET /labels/sheet must be the sheet."""
    from app.main import app
    from app.routers import labels

    scope = {"type": "http", "method": "GET", "path": "/labels/sheet", "root_path": ""}
    first = next(r for r in app.router.routes
                 if hasattr(r, "matches") and r.matches(scope)[0] == Match.FULL)
    assert first.endpoint is labels.labels_sheet
