"""The Browse list-view column registry — declared once in app/browse_columns.py.

The list view's `<thead>` and `<td>` cells used to spell out the column set
independently, with two load-more sentinels hard-coding `colspan="7"` to
match. `TestRegistry`/`TestClientConfig`/`TestColumnCount`/`TestTemplateWiring`
assert the registry's own invariants; the groups below (T5) check the
templates and `browse.js` actually derive from it, and pin the header/cell
agreement and load-more sentinel contracts end to end.
"""

import json
import re
from pathlib import Path

import pytest

from app import browse_columns as bc
from app.currency import get_currency
from tests.conftest import _insert_item

REPO_ROOT = Path(__file__).resolve().parents[1]
BROWSE_HTML = REPO_ROOT / "app" / "templates" / "browse.html"
ITEM_GRID_HTML = REPO_ROOT / "app" / "templates" / "fragments" / "item_grid.html"
ITEM_ROW_HTML = REPO_ROOT / "app" / "templates" / "fragments" / "item_row.html"
ITEM_ROWS_PAGE_HTML = REPO_ROOT / "app" / "templates" / "fragments" / "item_rows_page.html"
BROWSE_JS = REPO_ROOT / "static" / "js" / "browse.js"


class TestRegistry:
    def test_names_are_unique(self):
        names = [c.name for c in bc.COLUMNS]
        assert len(names) == len(set(names))

    def test_names_are_identifiers(self):
        for c in bc.COLUMNS:
            assert bc._NAME_RE.match(c.name), c.name

    def test_rejects_a_name_that_could_inject_markup(self):
        with pytest.raises(ValueError):
            bc.BrowseColumn("a'],[data-col='b", "x")

    def test_locked_columns(self):
        locked = {c.name for c in bc.COLUMNS if c.locked}
        assert locked == {"select", "cover", "title"}

    def test_no_locked_column_in_pickable(self):
        for c in bc.PICKABLE:
            assert not c.locked

    def test_pickable_is_exactly_the_non_locked_columns_in_order(self):
        expected = tuple(c for c in bc.COLUMNS if not c.locked)
        assert bc.PICKABLE == expected

    def test_every_non_locked_column_has_a_bool_default_on(self):
        for c in bc.COLUMNS:
            if not c.locked:
                assert isinstance(c.default_on, bool)


class TestClientConfig:
    def test_length_matches_columns(self):
        assert len(bc.client_config()) == len(bc.COLUMNS)

    def test_entry_keys(self):
        for entry in bc.client_config():
            assert set(entry.keys()) == {"name", "label", "locked", "defaultOn"}

    def test_json_serialisable(self):
        json.dumps(bc.client_config())

    def test_order_matches_column_names(self):
        names = [entry["name"] for entry in bc.client_config()]
        assert tuple(names) == bc.COLUMN_NAMES


class TestColumnCount:
    def test_matches_len_columns(self):
        assert bc.column_count() == len(bc.COLUMNS)


class TestTemplateWiring:
    def test_browse_html_has_column_config_block(self):
        assert 'id="browse-column-config"' in BROWSE_HTML.read_text()


class TestSingleDeclaration:
    """No file may re-spell the column set by hand -- everything must derive
    from app/browse_columns.py, the way test_browse_filters.py pins the same
    claim for the filter registry."""

    def test_no_colspan_literal_remains(self):
        for path in (ITEM_GRID_HTML, ITEM_ROWS_PAGE_HTML):
            src = path.read_text()
            literals = re.findall(r'colspan="\d+"', src)
            assert not literals, (
                f"{path.name} still has a hard-coded colspan literal "
                f"{literals!r} -- use colspan=\"{{{{ browse_columns|length }}}}\" "
                "so the sentinel stays derived from app/browse_columns.py."
            )

    def test_no_responsive_hide_class_remains(self):
        """The design's Sec 5 decision: the user's column selection is
        authoritative at every width. A reintroduced `hidden md:table-cell`
        would silently override the picker's x-show."""
        for path in (ITEM_GRID_HTML, ITEM_ROW_HTML, ITEM_ROWS_PAGE_HTML):
            src = path.read_text()
            assert "table-cell" not in src, (
                f"{path.name} has a responsive hide-at-a-breakpoint class -- "
                "column visibility must be owned by the picker alone."
            )

    def test_item_grid_has_exactly_one_th(self):
        # Strip Jinja comments first -- item_grid.html's own docstring-style
        # comment mentions "<th>" in prose, which would otherwise inflate
        # the count without there being a second real header.
        src = re.sub(r"\{#.*?#\}", "", ITEM_GRID_HTML.read_text(), flags=re.S)
        assert len(re.findall(r"<th\b", src)) == 1, (
            "item_grid.html must generate every header from the single "
            "{% for col in browse_columns %} loop -- a second <th> means "
            "someone hand-added a header outside the registry."
        )

    def test_browse_js_has_no_literal_column_name_list(self):
        """`browse.js` must read the column set from `browse-column-config`,
        never carry a second hand-maintained copy of it.

        Matched semantically, not by spelling. This guard used to assert two
        exact substrings (`'author', 'media_type'` and `'author','media_type'`)
        -- the same list written with double quotes, extra whitespace, a line
        break or a different pair of names sailed straight through, so it
        guarded one way of writing the mistake rather than the mistake. Any two
        adjacent quoted registered names are a column list starting to form,
        whatever the quoting.
        """
        src = BROWSE_JS.read_text()
        name = "|".join(re.escape(n) for n in bc.COLUMN_NAMES)
        # ['"]name['"] , ['"]name['"] -- quote style free, and free to differ
        # between the two, with any whitespace (newlines included) around the
        # comma.
        pair = re.compile(rf"""['"](?:{name})['"]\s*,\s*['"](?:{name})['"]""")
        found = pair.search(src)
        assert found is None, (
            f"static/js/browse.js spells out column names literally "
            f"({found.group(0) if found else ''}) -- the column set is "
            "declared once in app/browse_columns.py and reaches the client "
            "through the browse-column-config JSON block."
        )
        assert "browse-column-config" in src


def _thead_data_cols(html):
    m = re.search(r"<thead>(.*?)</thead>", html, re.S)
    assert m, "no <thead> found in response"
    return re.findall(r'data-col="([a-z_]+)"', m.group(1))


def _tbody_rows(html):
    """[(item_id, row_html), ...] for every <tr data-item-id=...> in <tbody>."""
    m = re.search(r"<tbody>(.*?)</tbody>", html, re.S)
    assert m, "no <tbody> found in response"
    return re.findall(r'<tr[^>]*data-item-id="(\d+)"[^>]*>(.*?)</tr>', m.group(1), re.S)


def _row_for(html, item_id):
    for rid, row_html in _tbody_rows(html):
        if int(rid) == item_id:
            return row_html
    raise AssertionError(f"no row found for item {item_id}")


def _cell(row_html, col_name):
    m = re.search(rf'data-col="{col_name}"[^>]*>(.*?)</td>', row_html, re.S)
    assert m, (col_name, row_html)
    return m.group(1)


class TestHeaderCellAgreement:
    """G48: seed, commit, THEN request -- otherwise every assertion below
    compares zero rows to zero rows and passes vacuously."""

    def test_header_and_every_row_match_registry_order(self, admin_client, db):
        rich = _insert_item(
            db, title="Rich Item", isbn="9780000009001", media_type="book",
            manual_value=42.50, estimated_value=10.00, series_name="The Chronicles",
            series_position=2, publisher="Acme Books", publish_year=2001,
            page_count=350, language="eng", platform="PC", upc="012345678905",
        )
        bare = _insert_item(db, title="Bare Item", isbn="9780000009002", media_type="book")
        db.commit()

        seeded = {rich, bare}
        row_count = db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        assert row_count == len(seeded)

        for url in ("/browse?view=list", "/api/search?view=list&page=1"):
            resp = admin_client.get(url)
            assert resp.status_code == 200, url
            html = resp.text

            header_cols = _thead_data_cols(html)
            assert tuple(header_cols) == bc.COLUMN_NAMES, (url, header_cols)

            rows = _tbody_rows(html)
            assert len(rows) == len(seeded), (url, len(rows))
            seen_ids = set()
            for item_id, row_html in rows:
                seen_ids.add(int(item_id))
                row_cols = re.findall(r'data-col="([a-z_]+)"', row_html)
                assert tuple(row_cols) == bc.COLUMN_NAMES, (url, item_id, row_cols)
            assert seen_ids == seeded, (url, seen_ids)


class TestLoadMoreSentinels:
    """DEFAULT_PAGE_SIZE is 60 (app/config.py) and has_more = (offset +
    per_page) < total (app/routers/items.py) -- so page 1 carries a sentinel
    only when total > 60, and page 2 only when total > 120. 121 items puts
    both sentinels in play."""

    def _assert_sentinel(self, html, expected_colspan):
        m = re.search(r'(<tr id="load-more"[^>]*>)(.*?)</tr>', html, re.S)
        assert m, "no load-more sentinel <tr> found"
        tr_open, inner = m.group(1), m.group(2)

        # G7: hx-get must sit on the <tr>, never on the <td> inside it --
        # htmx 2.x's outerHTML swap inserts into the trigger's parentElement,
        # so hx-get on the <td> nests incoming <tr> rows inside the sentinel
        # row and silently corrupts the table.
        assert "hx-get=" in tr_open, tr_open
        td_m = re.search(r"<td[^>]*>", inner)
        assert td_m, inner
        assert "hx-get=" not in td_m.group(0), td_m.group(0)
        assert f'colspan="{expected_colspan}"' in td_m.group(0), td_m.group(0)

    def test_both_sentinels_present_with_correct_colspan_and_trigger(self, admin_client, db):
        for i in range(121):
            _insert_item(db, title=f"Sentinel Item {i}", isbn=f"9780000{i:06d}", media_type="book")
        db.commit()

        row_count = db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        assert row_count == 121

        expected_colspan = len(bc.COLUMNS)

        # page 1 sentinel -- fragments/item_grid.html
        r1 = admin_client.get("/browse?view=list")
        assert r1.status_code == 200
        self._assert_sentinel(r1.text, expected_colspan)

        # page 2 sentinel -- fragments/item_rows_page.html
        r2 = admin_client.get("/api/search?view=list&page=2")
        assert r2.status_code == 200
        self._assert_sentinel(r2.text, expected_colspan)


class TestValueRendering:
    def test_manual_value_wins_estimate_is_hidden_and_unvalued_renders_dash(
        self, admin_client, db
    ):
        currency = get_currency()
        valued = _insert_item(
            db, title="Valued Item", isbn="9780000009101", media_type="book",
            manual_value=42.50, estimated_value=10.00,
        )
        unvalued = _insert_item(db, title="Unvalued Item", isbn="9780000009102", media_type="book")
        db.commit()

        row_count = db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        assert row_count == 2

        html = admin_client.get("/browse?view=list").text

        valued_cell = _cell(_row_for(html, valued), "value")
        assert currency.symbol in valued_cell, valued_cell
        assert "42.50" in valued_cell, valued_cell
        assert "10.00" not in valued_cell, valued_cell

        unvalued_cell = _cell(_row_for(html, unvalued), "value")
        assert unvalued_cell.strip() == "—", unvalued_cell
