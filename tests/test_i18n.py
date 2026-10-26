"""UI translation: locale resolution, per-user choice, and the catalogs."""

import subprocess
import sys
from pathlib import Path

import pytest

from app import i18n

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _i18n_on(monkeypatch):
    monkeypatch.setenv("SHELF_I18N", "on")


@pytest.mark.parametrize("user_language, header, expected", [
    (None, None, "en"),
    (None, "", "en"),
    (None, "it-IT,it;q=0.9,en;q=0.8", "it"),
    (None, "en-US,en;q=0.9,it;q=0.8", "en"),
    (None, "fr-FR,fr;q=0.9,it;q=0.5", "it"),      # unsupported tags are skipped
    (None, "it;q=0.4,en;q=0.9", "en"),            # q-value beats position
    (None, "it;q=0", "en"),                       # q=0 means "not this one"
    (None, "it;level=1;q=0", "en"),               # q is read wherever it sits
    (None, "de, *", "en"),
    ("it", "en-US,en;q=0.9", "it"),               # the user's choice beats the browser
    ("xx", "it", "it"),                           # an unknown stored value falls through
])
def test_resolve(user_language, header, expected):
    assert i18n.resolve(user_language, header) == expected


def test_gettext_follows_the_locale():
    token = i18n.current_locale.set("it")
    try:
        assert i18n.gettext("Sign out") == "Esci"
        assert i18n.js_catalog()["Done"] == "Fatto"
    finally:
        i18n.current_locale.reset(token)
    assert i18n.gettext("Sign out") == "Sign out"
    assert i18n.js_catalog() == {}


def test_login_page_follows_the_browser(client, admin_user):
    it = client.get("/login", headers={"Accept-Language": "it-IT,it;q=0.9"})
    assert '<html lang="it"' in it.text
    assert "Accedi alla tua biblioteca" in it.text

    en = client.get("/login")
    assert '<html lang="en"' in en.text
    assert "Sign in to your library" in en.text


def test_route_message_is_translated(client, admin_user):
    """The locale set in AuthMiddleware reaches the endpoint (a contextvar
    across BaseHTTPMiddleware's call_next), not only the template."""
    r = client.post("/login", data={"username": "nobody", "password": "x"},
                    headers={"Accept-Language": "it"})
    assert r.status_code == 401
    assert "Nome utente o password non validi" in r.text


def test_user_choice_beats_the_browser(admin_client, db):
    r = admin_client.post("/api/account/language", data={"language": "it"})
    assert r.json() == {"ok": True}
    assert db.execute("SELECT language FROM users WHERE username = 'admin'").fetchone()[0] == "it"

    page = admin_client.get("/browse", headers={"Accept-Language": "en-US"})
    assert '<html lang="it"' in page.text
    assert "Sfoglia" in page.text
    assert '"Done": "Fatto"' in page.text

    # Back to automatic: the browser decides again.
    admin_client.post("/api/account/language", data={"language": ""})
    page = admin_client.get("/browse", headers={"Accept-Language": "en-US"})
    assert '<html lang="en"' in page.text


def test_viewer_can_pick_their_own_language(viewer_client):
    assert viewer_client.post("/api/account/language", data={"language": "it"}).json()["ok"]


def test_unknown_language_is_refused(admin_client, db):
    r = admin_client.post("/api/account/language", data={"language": "xx"})
    assert r.json()["ok"] is False
    assert db.execute("SELECT language FROM users WHERE username = 'admin'").fetchone()[0] is None


def test_catalogs_are_complete_and_compiled():
    """Same check as `make check-i18n`, so a stale .mo fails the suite too."""
    r = subprocess.run([sys.executable, "scripts/i18n.py", "--check"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("value", [None, "", "off", "0", "nope"])
def test_off_stays_english_and_hides_the_picker(admin_client, db, monkeypatch, value):
    """Until every area is translated, the UI is English unless SHELF_I18N is on."""
    if value is None:
        monkeypatch.delenv("SHELF_I18N")
    else:
        monkeypatch.setenv("SHELF_I18N", value)
    db.execute("UPDATE users SET language = 'it' WHERE username = 'admin'")
    db.commit()
    page = admin_client.get("/browse", headers={"Accept-Language": "it"})
    assert '<html lang="en"' in page.text
    assert "Sfoglia" not in page.text
    assert 'data-testid="account-language"' not in page.text
