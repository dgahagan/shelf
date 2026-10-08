"""A cross-site page arrival is re-requested once from the same origin (#149).

The session cookie is SameSite=Strict, so a link from another site arrives
without it. AuthMiddleware answers such a page navigation with an empty 200
carrying `Refresh: 0`, so the browser asks again same-origin, with the cookie.
"""

import pytest

XS = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Dest": "document"}

STREAMS = [
    "/api/hardcover/export/stream",
    "/api/hardcover/import/stream",
    "/api/synopses/backfill/stream",
    "/api/covers/bulk-retry/stream",
    "/api/komga/sync/stream",
    "/api/romm/sync/stream",
    "/api/sync/audiobookshelf/stream",
    "/api/valuate/stream",
]


def _assert_login_redirect(resp):
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    assert "refresh" not in resp.headers


def test_cross_site_page_navigation_is_bounced_with_query(client, admin_user):
    resp = client.get("/browse?q=dune&page=2", headers=XS, follow_redirects=False)
    assert resp.status_code == 200
    assert resp.headers["refresh"] == "0;url=/browse?q=dune&page=2"
    assert resp.headers["cache-control"] == "no-store"
    assert resp.content == b""


def test_cross_site_root_is_bounced(client, admin_user):
    resp = client.get("/", headers=XS, follow_redirects=False)
    assert resp.status_code == 200
    assert resp.headers["refresh"] == "0;url=/"


@pytest.mark.parametrize("site", ["same-origin", "same-site", "none", None])
def test_other_sites_keep_the_login_redirect(client, admin_user, site):
    headers = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    if site is None:
        headers = {}
    else:
        headers["Sec-Fetch-Site"] = site
    _assert_login_redirect(client.get("/browse", headers=headers, follow_redirects=False))


def test_cross_site_fetch_is_not_bounced(client, admin_user):
    headers = {**XS, "Sec-Fetch-Mode": "cors"}
    _assert_login_redirect(client.get("/browse", headers=headers, follow_redirects=False))


@pytest.mark.parametrize("path", STREAMS + ["/api/items"])
def test_api_navigation_is_never_bounced(client, admin_user, path):
    _assert_login_redirect(client.get(path, headers=XS, follow_redirects=False))


@pytest.mark.parametrize("method", ["POST", "HEAD"])
def test_non_get_is_not_bounced(client, admin_user, method):
    resp = client.request(method, "/browse", headers=XS, follow_redirects=False)
    assert "refresh" not in resp.headers
    assert resp.status_code != 200


def test_authenticated_request_renders_the_page(admin_client):
    resp = admin_client.get("/browse", headers=XS, follow_redirects=False)
    assert resp.status_code == 200
    assert "refresh" not in resp.headers


@pytest.mark.parametrize("path", ["//evil.example/x", "/\\evil.example/x", "///evil.example/x"])
def test_target_can_never_leave_the_origin(client, admin_user, path):
    resp = client.get(f"http://testserver{path}", headers=XS, follow_redirects=False)
    assert resp.status_code == 200
    assert resp.headers["refresh"] == "0;url=/evil.example/x"


def test_encoded_crlf_stays_encoded(client, admin_user):
    resp = client.get("/browse%0d%0aX-Injected:1", headers=XS, follow_redirects=False)
    assert "x-injected" not in resp.headers
    assert resp.headers["refresh"] == "0;url=/browse%0d%0aX-Injected:1"


def test_zero_users_still_go_to_setup(client):
    resp = client.get("/browse", headers=XS, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/setup"
    assert "refresh" not in resp.headers


def test_bounce_carries_security_headers(client, admin_user):
    resp = client.get("/browse", headers=XS, follow_redirects=False)
    assert resp.headers["refresh"] == "0;url=/browse"
    assert "content-security-policy" in resp.headers
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_cross_site_login_page_is_bounced(client, admin_user):
    # gemini-R1: a link straight to /login would otherwise show the form to a
    # browser holding a live session; the same-origin retry sends it on to /.
    resp = client.get("/login", headers=XS, follow_redirects=False)
    assert resp.status_code == 200
    assert resp.headers["refresh"] == "0;url=/login"


def test_same_origin_login_page_still_renders(client, admin_user):
    resp = client.get("/login", follow_redirects=False)
    assert resp.status_code == 200
    assert "refresh" not in resp.headers
