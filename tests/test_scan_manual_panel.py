"""The Scan page's manual entry panel (issue #120).

The panel is a third input path on `/scan`, beside the barcode form and title
search. These tests pin what the server renders: the panel's presence and open
state, the two fields the card fixes and the panel lets the user choose, the
swap wiring, and the `?from={id}` prefill's graceful degradation.

The live behaviour that needs a browser — the persisted-mode normalization,
the platform select's `:disabled`, and one page carrying two cards plus the
panel — is in `tests/e2e/test_scan.py` and
`tests/e2e/test_component_load_guard.py`.
"""

import pytest

from app.config import MEDIA_TYPES
from tests.conftest import _insert_item, _insert_location


class TestPanelRenders:
    def test_editor_gets_the_panel(self, editor_client):
        html = editor_client.get("/scan").text
        assert 'data-manual-host="panel"' in html
        assert "data-manual-open" in html

    def test_a_viewer_never_sees_the_panel(self, viewer_client):
        """/scan is editor-gated, so a viewer is redirected away rather than
        shown a page with the panel suppressed."""
        resp = viewer_client.get("/scan", follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        assert 'data-manual-host="panel"' not in resp.text

    def test_add_manual_opens_it_and_plain_scan_does_not(self, admin_client):
        opened = admin_client.get("/scan?add=manual").text
        assert 'data-manual-open="true"' in opened

        plain = admin_client.get("/scan").text
        assert 'data-manual-open="false"' in plain

    def test_an_unrelated_add_value_does_not_open_it(self, admin_client):
        html = admin_client.get("/scan?add=something").text
        assert 'data-manual-open="false"' in html


class TestPanelFields:
    def _panel(self, client, url="/scan?add=manual"):
        """The panel's own markup, sliced off at its host marker so an
        assertion cannot be satisfied by the page's other controls."""
        html = client.get(url).text
        start = html.index('data-manual-host="panel"')
        return html[start:]

    def test_media_type_select_offers_every_type(self, admin_client):
        panel = self._panel(admin_client)
        for key in MEDIA_TYPES:
            assert 'value="%s"' % key in panel, key

    def test_media_type_select_never_offers_auto(self, admin_client):
        """`auto` means "detect from the barcode" and there is none. The route
        refuses it (pinned in test_title_search.py) — the panel simply has no
        such option."""
        panel = self._panel(admin_client)
        assert 'value="auto"' not in panel

    def test_identifier_is_visible_on_the_panel(self, admin_client):
        panel = self._panel(admin_client)
        assert 'name="isbn"' in panel
        assert '<input type="hidden" name="isbn"' not in panel
        assert "ISBN or barcode (optional)" in panel

    def test_identifier_is_still_hidden_on_the_not_found_card(self, admin_client, db):
        """The card is the fragment's default host and is unchanged."""
        from unittest.mock import AsyncMock, patch

        from app.services import provider_result

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(return_value=(None, "", {}, provider_result.no_match("openlibrary"))),
        ), patch(
            "app.routers.items_common._fetch_preview_cover", new=AsyncMock(return_value=None)
        ):
            resp = admin_client.post(
                "/api/scan",
                data={"isbn": "9780000999931", "media_type": "book", "mode": "add"},
            )
        assert '<input type="hidden" name="isbn"' in resp.text
        assert '<input type="hidden" name="media_type"' in resp.text

    def test_panel_form_carries_its_own_swap_wiring(self, admin_client):
        """G54: the destination is decided on the control itself, and the mode
        comes in by hx-include rather than a cross-scope Alpine read."""
        panel = self._panel(admin_client)
        assert 'hx-target="#scan-results"' in panel
        assert 'hx-swap="afterbegin"' in panel
        assert 'hx-include="#scan-mode"' in panel

    def test_the_page_supplies_the_included_mode_input(self, admin_client):
        assert 'id="scan-mode"' in admin_client.get("/scan").text

    def test_panel_has_no_skip_button(self, admin_client):
        """"Skip" sets showForm = false, which on the panel would hide the
        form with the page toggle as the only way back."""
        assert "Skip" not in self._panel(admin_client)


class TestPrefillFromAnExistingItem:
    def _seed(self, db):
        loc = _insert_location(db, name="Loft")
        item_id = _insert_item(
            db,
            title="Source Item",
            authors="Ada Lovelace",
            publisher="Analytical Press",
            publish_year=1843,
            media_type="video_game",
            platform="snes",
            series_name="Engines",
            location_id=loc,
        )
        db.commit()
        return item_id, loc

    def test_prefills_all_six_copyable_fields_and_the_type(self, admin_client, db):
        item_id, loc = self._seed(db)
        html = admin_client.get("/scan?add=manual&from=%d" % item_id).text
        panel = html[html.index('data-manual-host="panel"'):]

        assert 'value="Ada Lovelace"' in panel
        assert 'value="Analytical Press"' in panel
        assert 'value="1843"' in panel
        assert 'value="Engines"' in panel
        assert 'value="video_game" selected' in panel
        assert 'value="snes" selected' in panel
        assert 'value="%d" selected' % loc in panel

    def test_prefilled_game_shows_the_platform_select(self, admin_client, db):
        """A prefilled game must not paint the platform select hidden and then
        reveal it — the server already knows the type."""
        item_id, _ = self._seed(db)
        html = admin_client.get("/scan?add=manual&from=%d" % item_id).text
        panel = html[html.index('data-manual-host="panel"'):]
        select_block = panel[:panel.index('name="platform"')]
        assert 'x-show="mediaType === \'video_game\'"' in select_block
        assert "style=\"display:none\"" not in select_block.rsplit("<div", 1)[-1]

    def test_platform_select_is_disabled_off_the_game_type(self, admin_client):
        """R2: x-show only sets display:none, and a hidden <select> is still a
        successful control that posts its value. :disabled is what removes it
        from submission."""
        html = admin_client.get("/scan?add=manual").text
        panel = html[html.index('data-manual-host="panel"'):]
        assert ':disabled="mediaType !== \'video_game\'"' in panel

    @pytest.mark.parametrize("bad", ["999999", "notanumber", "0", "-3", ""])
    def test_a_from_that_names_nothing_renders_a_normal_empty_panel(
        self, admin_client, bad
    ):
        """Missing, malformed, zero, negative and nonexistent all mean the same
        thing: no prefill, a normal 200. A 422 here is the regression — a typed
        int annotation would validate before the handler could degrade."""
        resp = admin_client.get("/scan?add=manual&from=%s" % bad)
        assert resp.status_code == 200
        assert 'data-manual-host="panel"' in resp.text
        assert "Ada Lovelace" not in resp.text

    def test_a_null_column_prefills_empty_and_never_the_text_None(
        self, admin_client, db
    ):
        """The common case: an item with no series, publisher or year.

        `copyable_fields` hands the template Python `None` for a NULL column,
        and Jinja stringifies that to the four characters `None`. The picker's
        JS path has guarded this since #19 (`applyTemplate` skips null), so
        only the server-rendered `?from=` prefill could regress — which it did:
        `value="None"` reached the Series and Publisher text inputs, and made
        the number input's value unparseable. Source: test-drive Observation 1.
        """
        item_id = _insert_item(db, title="Bare Item", media_type="book")
        db.commit()

        html = admin_client.get("/scan?add=manual&from=%d" % item_id).text
        panel = html[html.index('data-manual-host="panel"'):]

        assert 'value="None"' not in panel
        for name in ("authors", "publisher", "publish_year", "series_name"):
            field = panel[panel.index('name="%s"' % name):]
            assert 'value="None"' not in field[:field.index(">")]

    def test_prefill_agrees_with_the_copy_template_endpoint(self, admin_client, db):
        """Both read one declared field set, so they cannot drift."""
        item_id, _ = self._seed(db)
        payload = admin_client.get("/api/items/%d/copy-template" % item_id).json()

        html = admin_client.get("/scan?add=manual&from=%d" % item_id).text
        panel = html[html.index('data-manual-host="panel"'):]
        for value in (payload["authors"], payload["publisher"], payload["series_name"]):
            assert 'value="%s"' % value in panel
        assert 'value="%s" selected' % payload["media_type"] in panel
        assert 'value="%s" selected' % payload["platform"] in panel


class TestCopyTemplateEndpointIsUnchanged:
    """The helper extraction must not move this endpoint's contract."""

    def test_returns_exactly_the_declared_field_set(self, admin_client, db):
        from app.services.item_template import COPYABLE_FIELDS

        item_id = _insert_item(db, title="X", authors="Y", media_type="book")
        db.commit()
        payload = admin_client.get("/api/items/%d/copy-template" % item_id).json()
        assert list(payload.keys()) == list(COPYABLE_FIELDS)

    def test_still_404s_on_a_missing_id(self, admin_client):
        resp = admin_client.get("/api/items/999999/copy-template")
        assert resp.status_code == 404
        assert resp.json() == {"error": "Not found"}
