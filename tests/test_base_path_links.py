"""Settings links built in Python and inside translated strings (#165)."""
import pytest

from app import i18n


def test_igdb_not_configured_link_default(admin_client):
    resp = admin_client.get("/api/games/search", params={"q": "Halo", "platform": ""})
    assert 'href="/settings"' in resp.text


def test_igdb_not_configured_link_prefixed(admin_client, with_base_path):
    resp = admin_client.get("/api/games/search", params={"q": "Halo", "platform": ""})
    assert 'href="/ingress/test/settings"' in resp.text


def test_tmdb_not_configured_link_prefixed(admin_client, with_base_path):
    resp = admin_client.get("/api/dvds/search", params={"q": "test"})
    assert "TMDb API key not configured" in resp.text
    assert 'href="/ingress/test/settings"' in resp.text


def _render_rejected():
    import app.main as main
    tpl = main.templates.env.get_template("fragments/search_status_notice.html")
    return tpl.render(search_status="rejected", search_provider="IGDB")


@pytest.mark.parametrize("locale,word", [("en", "rejected the configured key"),
                                          ("it", "ha rifiutato la chiave")])
def test_translated_anchor_takes_the_prefix(with_base_path, locale, word):
    token = i18n.current_locale.set(locale)
    try:
        html = _render_rejected()
    finally:
        i18n.current_locale.reset(token)
    assert word in html
    assert 'href="/ingress/test/settings"' in html


def test_translated_anchor_default(client):
    token = i18n.current_locale.set("it")
    try:
        html = _render_rejected()
    finally:
        i18n.current_locale.reset(token)
    assert "ha rifiutato la chiave" in html
    assert 'href="/settings"' in html
