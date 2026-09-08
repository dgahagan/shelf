"""Regression coverage for the sectioned Settings workspace and user menu."""

from pathlib import Path


def test_settings_page_has_four_section_controls(admin_client):
    html = admin_client.get("/settings").text
    assert 'data-testid="settings-section-nav"' in html
    assert 'data-testid="settings-section-content"' in html
    for key in ("library", "integrations", "data", "users"):
        assert html.count(f'data-testid="tab-{key}"') == 1


def test_settings_page_keeps_existing_setting_surfaces(admin_client):
    """The layout is a shell change: representative existing forms survive."""
    html = admin_client.get("/settings").text
    assert 'action="/api/settings/display"' in html
    assert 'action="/api/settings/nav"' in html
    assert 'action="/api/settings"' in html
    assert "Audiobookshelf" in html
    assert "Portable archive" in html
    assert "Users" in html


def test_admin_settings_entry_lives_in_account_menu(admin_client):
    html = admin_client.get("/browse").text
    assert 'data-testid="account-menu-button"' in html
    assert 'data-testid="account-menu-panel"' in html
    assert 'data-testid="account-menu-settings"' in html
    assert 'data-nav-tab="settings"' in html


def test_non_admin_account_menus_do_not_offer_admin_settings(editor_client, viewer_client):
    for client in (editor_client, viewer_client):
        html = client.get("/browse").text
        assert 'data-testid="account-menu-button"' in html
        assert 'data-testid="account-menu-settings"' not in html
        # This mirrors the existing server contract: /settings itself has
        # always required the admin role, so moving the link does not tighten
        # editor/viewer access as a side effect of the layout change.
        response = client.get("/settings", follow_redirects=False)
        assert response.status_code in (302, 303, 401, 403)


def test_settings_is_not_rendered_in_primary_nav(admin_client):
    html = admin_client.get("/browse").text
    desktop_start = html.index('<div class="hidden lg:flex items-center gap-1">')
    desktop_end = html.index("</div>", desktop_start)
    desktop_nav = html[desktop_start:desktop_end]
    assert 'data-nav-tab="settings"' not in desktop_nav

    mobile_start = html.index('data-testid="nav-menu-panel"')
    mobile_end = html.index("</div>", mobile_start)
    mobile_nav = html[mobile_start:mobile_end]
    assert 'data-nav-menu-tab="settings"' not in mobile_nav


def test_account_menu_controller_is_registered():
    js = Path(__file__).resolve().parent.parent.joinpath("static/js/components.js").read_text()
    assert "Alpine.data('accountMenu'" in js
    assert "openAccount()" in js
    assert "closeAll()" in js
