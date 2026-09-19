"""Regression coverage for the refreshed top-right account menu."""


def _panel(html: str) -> str:
    return html.split('data-testid="account-menu-panel"', 1)[1].split("<!-- Account Modal -->", 1)[0]


def test_admin_account_menu_groups_profile_administration_and_sign_out(admin_client):
    html = admin_client.get("/browse").text
    panel = _panel(html)

    assert 'aria-label="Account menu"' in html
    assert 'aria-haspopup="menu"' in html
    assert 'role="menu"' in panel
    assert 'data-testid="account-menu-profile"' in panel
    assert 'data-testid="account-menu-settings"' in panel
    assert 'data-testid="account-menu-logout"' in panel
    assert "Profile and password" in panel
    assert "Administration" in panel
    assert "Sign out" in panel


def test_viewer_account_menu_keeps_profile_but_omits_administration(viewer_client):
    panel = _panel(viewer_client.get("/browse").text)

    assert 'data-testid="account-menu-profile"' in panel
    assert 'data-testid="account-menu-logout"' in panel
    assert 'data-testid="account-menu-settings"' not in panel
    assert "Administration" not in panel


def test_account_modal_keeps_existing_profile_and_password_controls(admin_client):
    html = admin_client.get("/browse").text

    assert "Manage your profile and sign-in details" in html
    assert 'aria-label="Close account dialog"' in html
    assert "Display Name" in html
    assert "Change Password" in html
