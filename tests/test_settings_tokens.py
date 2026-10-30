"""Settings -> Users: every user's API tokens, for admins."""

from pathlib import Path

TABS = ("library", "integrations", "data", "users", "features")


def test_admin_settings_has_the_tokens_card(admin_client):
    html = admin_client.get("/settings").text
    assert 'data-testid="admin-tokens-card"' in html
    assert 'x-data="adminTokensPanel"' in html
    assert 'data-testid="admin-token-revoke"' in html


def test_editor_is_still_redirected_from_settings(editor_client):
    r = editor_client.get("/settings", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/browse"


def test_users_template_stays_under_the_cap():
    n = len(Path("app/templates/fragments/settings/users.html").read_text().splitlines())
    assert n <= 400


def test_loader_guard_and_registration_name_the_panel():
    guard = Path("static/js/component-load-guard.js").read_text()
    assert "adminTokensPanel: 'components-settings.js'" in guard
    assert "Alpine.data('adminTokensPanel'" in Path("static/js/components-settings.js").read_text()


def test_no_sixth_tab_control(admin_client):
    html = admin_client.get("/settings").text
    for key in TABS:
        assert html.count(f'data-testid="tab-{key}"') == 1
    assert html.count('data-testid="tab-') == 5
