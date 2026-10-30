"""T5 — a turned-off feature's Settings card says so and drops only its
use controls; its setup controls stay. One `GET /settings` renders every
card (the tabs are `x-show`, not lazy), so each test makes one request.

Seeds commit before the request (G48); flags are flipped with `off(...)`
from tests/test_features_gate.py, which drops the flag cache. Every pin
names an element or a data-testid, never a bare label (G69), and every
absence pin has a presence twin with the flag on (G108).
"""

from tests.conftest import _insert_borrower
from tests.test_features_gate import off


def _note(key):
    return f'data-testid="feature-off-note" data-feature="{key}"'


def _seed_borrower_and_share(db):
    borrower_id = _insert_borrower(db, name="Sweep Borrower")
    db.execute("INSERT INTO share_links (token, scope, label) VALUES ('sweep-tok', 'collection', 'Sweep Link')")
    db.commit()
    return borrower_id


class TestBorrowersCard:
    """Lending off: add/remove forms go, the borrower's name stays as
    plain text, and the card gets the off note (Decisions already made,
    2026-09-28 — supersedes the design's original "stays whole" reading)."""

    def test_lending_off_removes_add_and_remove_forms(self, admin_client, db):
        _seed_borrower_and_share(db)
        off("lending")

        html = admin_client.get("/settings").text

        assert _note("lending") in html
        assert 'data-testid="borrower-add"' not in html
        assert 'data-testid="borrower-remove"' not in html
        assert "Sweep Borrower" in html

    def test_lending_on_keeps_add_and_remove_forms(self, admin_client, db):
        _seed_borrower_and_share(db)

        html = admin_client.get("/settings").text

        assert _note("lending") not in html
        assert 'data-testid="borrower-add"' in html
        assert 'data-testid="borrower-remove"' in html
        assert "Sweep Borrower" in html


class TestLendingCard:
    """Lending off: the Lending card stays whole — its two routes are core
    settings routes, not gated — and gains the off note."""

    def test_lending_off_keeps_the_settings_form(self, admin_client, db):
        off("lending")

        html = admin_client.get("/settings").text

        assert _note("lending") in html
        assert 'name="lending_overdue_days"' in html

    def test_lending_on_has_no_note(self, admin_client, db):
        html = admin_client.get("/settings").text

        assert _note("lending") not in html
        assert 'name="lending_overdue_days"' in html


class TestAbsSyncCard:
    """ABS off: Sync Now and the cleanup button go; the URL/token form,
    Test and the schedule form stay (all ungated, per features.py)."""

    def test_abs_sync_off_removes_sync_and_cleanup(self, admin_client, db):
        off("abs_sync")

        html = admin_client.get("/settings").text

        assert _note("abs_sync") in html
        assert 'data-testid="abs-sync-now"' not in html
        assert 'data-testid="abs-cleanup"' not in html
        assert 'name="abs_url"' in html
        assert '@click="testAbs"' in html
        assert 'action="/api/sync/audiobookshelf/schedule"' in html

    def test_abs_sync_on_keeps_sync_and_cleanup(self, admin_client, db):
        html = admin_client.get("/settings").text

        assert _note("abs_sync") not in html
        assert 'data-testid="abs-sync-now"' in html
        assert 'data-testid="abs-cleanup"' in html


class TestHardcoverCard:
    """Hardcover off: Import and Export go; the token form, Test and the
    Sync Schedule form (ungated core settings) stay."""

    def test_hardcover_off_removes_import_and_export(self, admin_client, db):
        off("hardcover")

        html = admin_client.get("/settings").text

        assert _note("hardcover") in html
        assert 'data-testid="hc-import"' not in html
        assert 'data-testid="hc-export"' not in html
        assert 'name="hardcover_token"' in html
        assert 'action="/api/hardcover/schedule"' in html

    def test_hardcover_on_keeps_import_and_export(self, admin_client, db):
        html = admin_client.get("/settings").text

        assert _note("hardcover") not in html
        assert 'data-testid="hc-import"' in html
        assert 'data-testid="hc-export"' in html


class TestValuationCard:
    """Valuation off: the run control and the report link go; the key
    form and Test Key (ungated) stay."""

    def test_valuation_off_removes_run_and_report(self, admin_client, db):
        off("valuation")

        html = admin_client.get("/settings").text

        assert _note("valuation") in html
        assert 'data-testid="valuation-run"' not in html
        assert 'data-testid="valuation-report"' not in html
        assert 'name="isbndb_api_key"' in html
        assert '@click="testKey"' in html

    def test_valuation_on_keeps_run_and_report(self, admin_client, db):
        html = admin_client.get("/settings").text

        assert _note("valuation") not in html
        assert 'data-testid="valuation-run"' in html
        assert 'data-testid="valuation-report"' in html


class TestPhotoIntakeCard:
    """Photo Intake has no gated control of its own — just the note."""

    def test_intake_off_has_note_and_keeps_provider_form(self, admin_client, db):
        off("intake")

        html = admin_client.get("/settings").text

        assert _note("intake") in html
        assert 'name="vision_provider"' in html

    def test_intake_on_has_no_note(self, admin_client, db):
        html = admin_client.get("/settings").text

        assert _note("intake") not in html
        assert 'name="vision_provider"' in html


class TestRommCard:
    """RomM off: #romm-sync goes; Save, Test and Manage Platforms stay."""

    def test_romm_off_removes_sync(self, admin_client, db):
        off("romm")

        html = admin_client.get("/settings").text

        assert _note("romm") in html
        assert 'id="romm-sync"' not in html
        assert 'id="romm-save"' in html
        assert 'id="romm-test"' in html
        assert 'id="romm-load-platforms"' in html

    def test_romm_on_keeps_sync(self, admin_client, db):
        html = admin_client.get("/settings").text

        assert _note("romm") not in html
        assert 'id="romm-sync"' in html


class TestKomgaCard:
    """Komga off: #komga-sync goes; Save, Test and Manage Libraries stay."""

    def test_komga_off_removes_sync(self, admin_client, db):
        off("komga")

        html = admin_client.get("/settings").text

        assert _note("komga") in html
        assert 'id="komga-sync"' not in html
        assert 'id="komga-save"' in html
        assert 'id="komga-test"' in html
        assert 'id="komga-load-libraries"' in html

    def test_komga_on_keeps_sync(self, admin_client, db):
        html = admin_client.get("/settings").text

        assert _note("komga") not in html
        assert 'id="komga-sync"' in html


class TestDiscogsCard:
    """Discogs has no gated control of its own — just the note."""

    def test_music_off_has_note_and_keeps_token_form(self, admin_client, db):
        off("music")

        html = admin_client.get("/settings").text

        assert _note("music") in html
        assert 'name="discogs_token"' in html

    def test_music_on_has_no_note(self, admin_client, db):
        html = admin_client.get("/settings").text

        assert _note("music") not in html
        assert 'name="discogs_token"' in html


class TestSharingCard:
    """Sharing off: the create form and each row's Copy/Open/Revoke go;
    the heading and the rows themselves (scope, label, token) stay as
    plain text. Scoped to the share-revoke testid, not the token text
    (G69's third face) — the token stays visible either way."""

    def test_share_off_removes_create_and_revoke(self, admin_client, db):
        _seed_borrower_and_share(db)
        off("share")

        html = admin_client.get("/settings").text

        assert _note("share") in html
        assert 'data-testid="share-create"' not in html
        assert 'data-testid="share-revoke"' not in html
        assert 'data-testid="share-link-row"' in html
        assert "/share/sweep-tok" in html

    def test_share_on_keeps_create_and_revoke(self, admin_client, db):
        _seed_borrower_and_share(db)

        html = admin_client.get("/settings").text

        assert _note("share") not in html
        assert 'data-testid="share-create"' in html
        assert 'data-testid="share-revoke"' in html
        assert "/share/sweep-tok" in html


class TestAdminTokensCard:
    """api off: the Users tab's tokens card keeps its heading and shows the
    off note, but loses the panel component. Scoped to the component name
    and testid, not the heading (G69)."""

    def test_api_off_shows_note_and_drops_panel(self, admin_client):
        off("api")

        html = admin_client.get("/settings").text

        assert 'data-testid="admin-tokens-card"' in html
        assert _note("api") in html
        assert 'x-data="adminTokensPanel"' not in html
        assert 'data-testid="admin-tokens-empty"' not in html

    def test_api_on_keeps_panel_and_no_note(self, admin_client):
        html = admin_client.get("/settings").text

        assert _note("api") not in html
        assert 'x-data="adminTokensPanel"' in html
        assert 'data-testid="admin-tokens-empty"' in html
