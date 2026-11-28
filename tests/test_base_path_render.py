"""The runtime twin of the base-path lint (#165).

Renders real pages under SHELF_BASE_PATH=/ingress/test and checks every URL
attribute in the body carries the prefix. Each page also carries a positive
assertion taken from the page's own body, never from the nav chrome (which
every page renders), so the pin cannot pass on an empty page (G142, G108).
"""
import re

import pytest

from tests.conftest import _insert_item

BASE = "/ingress/test"
_URL_ATTR = re.compile(r'\s(?:href|src|action|hx-get|hx-post|hx-put|hx-delete|hx-patch|data-cover)="(/[^"]*)"')
_NAV = re.compile(r"<a\b[^>]*data-nav-tab[^>]*>", re.S)


@pytest.fixture
def seeded(db):
    item = _insert_item(db, title="Prefix Probe", isbn="9780306406157", cover_path="covers/1.jpg")
    db.execute("INSERT INTO share_links (token, scope, label) VALUES ('probetoken', 'wishlist', 'x')")
    db.commit()  # G48: the db fixture holds a transaction the request would not see
    return item


def _pages(item):
    return [
        ("/login", None),
        ("/", None),
        ("/browse", f'{BASE}/item/{item}'),
        (f"/item/{item}", f'{BASE}/item/{item}/edit'),
        (f"/item/{item}/edit", f'{BASE}/api/items/{item}'),
        ("/scan", f'{BASE}/api/scan'),
        ("/settings", f'{BASE}/api/share'),
        ("/store", f'{BASE}/static/js/store.js'),
    ]


def _escapes(html):
    return [u for u in _URL_ATTR.findall(html) if not (u.startswith(BASE + "/") or u == BASE or u.startswith("//"))]


def test_prefixed_pages_emit_only_prefixed_urls(admin_client, seeded, with_base_path):
    for path, positive in _pages(seeded):
        resp = admin_client.get(path)
        assert resp.status_code == 200, path
        assert _escapes(resp.text) == [], path
        if positive:
            body = _NAV.sub("", resp.text)
            assert positive in body, (path, positive)


def test_share_page_prefixed(client, seeded, with_base_path):
    resp = client.get("/share/probetoken")
    assert resp.status_code == 200
    assert _escapes(resp.text) == []
    assert f'{BASE}/static/css/app.css' in resp.text


def test_prefix_is_the_only_difference(admin_client, seeded, monkeypatch):
    """The default render equals the prefixed one with the prefix removed."""
    import app.main as main
    pages = [p for p, _ in _pages(seeded)]
    default = {p: admin_client.get(p).text for p in pages}
    monkeypatch.setattr("app.config.BASE_PATH", BASE)
    monkeypatch.setitem(main.templates.env.globals, "base_path", BASE)
    for p in pages:
        prefixed = admin_client.get(p).text
        assert BASE in prefixed, p
        assert prefixed.replace(BASE, "") == default[p], p
