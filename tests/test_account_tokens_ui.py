"""The API tokens tab in the account modal: rendered for every role with
`api` on, absent with it off, and the dropdown panel unchanged."""

import pytest

from tests.test_account_menu_refresh import _panel
from tests.test_features_gate import off

MARKER = "<!-- Account Modal -->"
CLIENTS = ["viewer_client", "editor_client", "admin_client"]


def _modal(html: str) -> str:
    return html.split(MARKER, 1)[1]


@pytest.mark.parametrize("fixture", CLIENTS)
def test_every_role_gets_the_tokens_tab(request, fixture):
    html = request.getfixturevalue(fixture).get("/browse").text
    modal = _modal(html)
    assert 'data-testid="account-tab-tokens"' in modal
    assert 'x-data="apiTokensPanel"' in modal
    assert 'data-testid="api-tokens-pane"' in modal
    assert "This is the only time the token is shown." in modal
    assert "No tokens yet." in modal


@pytest.mark.parametrize("fixture", CLIENTS)
def test_the_tab_is_absent_with_api_off(request, fixture):
    client = request.getfixturevalue(fixture)
    off("api")
    html = client.get("/browse").text
    assert MARKER in html  # the modal itself still renders
    assert 'data-testid="account-tab-tokens"' not in html
    assert 'data-testid="api-tokens-pane"' not in html
    assert "apiTokensPanel" not in html


@pytest.mark.parametrize("fixture", CLIENTS)
def test_the_dropdown_panel_is_unchanged(request, fixture):
    client = request.getfixturevalue(fixture)
    on_panel = _panel(client.get("/browse").text)
    off("api")
    assert _panel(client.get("/browse").text) == on_panel
    assert "token" not in on_panel.lower()


def test_the_tokens_tab_sits_after_the_existing_tabs(admin_client):
    modal = _modal(admin_client.get("/browse").text)
    assert modal.index(">Profile</button>") < modal.index(">Password</button>") \
        < modal.index('data-testid="account-tab-tokens"')


def test_the_component_is_registered_in_the_load_guard():
    from pathlib import Path
    guard = Path("static/js/component-load-guard.js").read_text()
    assert "apiTokensPanel: 'components-settings.js'" in guard
    assert "Alpine.data('apiTokensPanel'" in Path("static/js/components-settings.js").read_text()
