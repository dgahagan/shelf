"""Session cookie path under SHELF_BASE_PATH, and a logout that clears both paths (#165).

Read raw Set-Cookie headers: the httpx jar hides cookie attributes (G123).
"""
from datetime import datetime, timedelta, timezone

import jwt

BASE = "/ingress/test"


def _set_cookies(resp):
    return resp.headers.get_list("set-cookie")


def _login(client):
    return client.post("/login", data={"username": "admin", "password": "password123"},
                       follow_redirects=False)


def _cookie(header):
    name = header.split("=", 1)[0]
    attrs = {}
    for part in header.split(";")[1:]:
        k, _, v = part.strip().partition("=")
        attrs[k.lower()] = v
    return name, attrs


def test_default_login_sets_both_at_root(client, admin_user):
    cookies = [_cookie(h) for h in _set_cookies(_login(client))]
    assert sorted(n for n, _ in cookies) == ["access_token", "csrf_token"]
    assert [a["path"] for _, a in cookies] == ["/", "/"]


def test_default_logout_clears_at_root_only(admin_client):
    cookies = [_cookie(h) for h in _set_cookies(admin_client.post("/logout", follow_redirects=False))]
    assert sorted((n, a["path"]) for n, a in cookies) == [("access_token", "/"), ("csrf_token", "/")]


def test_prefixed_login_scopes_both(client, admin_user, with_base_path):
    cookies = [_cookie(h) for h in _set_cookies(_login(client))]
    assert sorted((n, a["path"]) for n, a in cookies) == [("access_token", BASE), ("csrf_token", BASE)]


def test_prefixed_logout_clears_scoped_and_legacy(admin_client, with_base_path):
    cookies = [_cookie(h) for h in _set_cookies(admin_client.post("/logout", follow_redirects=False))]
    assert sorted((n, a["path"]) for n, a in cookies) == [
        ("access_token", "/"), ("access_token", BASE), ("csrf_token", "/"), ("csrf_token", BASE),
    ]
    assert all(a.get("max-age") == "0" for _, a in cookies)


def _old_token(user):
    from app.auth import get_secret_key
    from app.config import JWT_ALGORITHM, JWT_EXPIRY_SECONDS
    iat = datetime.now(timezone.utc) - timedelta(seconds=JWT_EXPIRY_SECONDS * 0.75)
    return jwt.encode({
        "sub": str(user["id"]), "username": user["username"], "role": user["role"],
        "display_name": user["display_name"], "tv": 1,
        "iat": iat, "exp": iat + timedelta(seconds=JWT_EXPIRY_SECONDS),
    }, get_secret_key(), algorithm=JWT_ALGORITHM)


def test_sliding_refresh_sets_scoped_cookies(client, admin_user, with_base_path):
    client.cookies.set("access_token", _old_token(admin_user))
    resp = client.get("/browse")
    assert resp.status_code == 200
    cookies = [_cookie(h) for h in _set_cookies(resp)]
    assert sorted((n, a["path"]) for n, a in cookies) == [("access_token", BASE), ("csrf_token", BASE)]


def test_refresh_with_legacy_and_scoped_pair_keeps_the_scoped_csrf(client, admin_user, with_base_path):
    """A browser sends the longer-path cookie first; the server reads that one.

    The page script also reads the first csrf_token, so after the refresh the
    header it sends and the cookie the server compares it with still agree.
    """
    old = _old_token(admin_user)
    client.cookies.clear()
    cookie = f"access_token={old}; csrf_token=scoped-csrf; access_token={old}; csrf_token=legacy-csrf"
    resp = client.get("/browse", headers={"Cookie": cookie})
    assert resp.status_code == 200
    refreshed = dict(h.split(";", 1)[0].split("=", 1) for h in _set_cookies(resp))
    assert refreshed["csrf_token"] == "scoped-csrf"


def _fresh_token(user):
    from app.auth import get_secret_key
    from app.config import JWT_ALGORITHM, JWT_EXPIRY_SECONDS
    iat = datetime.now(timezone.utc)
    return jwt.encode({
        "sub": str(user["id"]), "username": user["username"], "role": user["role"],
        "display_name": user["display_name"], "tv": 1,
        "iat": iat, "exp": iat + timedelta(seconds=JWT_EXPIRY_SECONDS),
    }, get_secret_key(), algorithm=JWT_ALGORITHM)


def test_foreign_root_session_cookie_does_not_shadow_the_scoped_one(client, admin_user, with_base_path):
    """Another app on the host left access_token at Path=/ (diff review F1)."""
    client.cookies.clear()
    cookie = f"access_token={_fresh_token(admin_user)}; csrf_token=c; access_token=another-apps-session"
    resp = client.get("/browse", headers={"Cookie": cookie}, follow_redirects=False)
    assert resp.status_code == 200


def test_foreign_root_csrf_cookie_does_not_break_posts(client, admin_user, with_base_path):
    """Another app's csrf_token at Path=/ comes second; the scoped one is compared."""
    client.cookies.clear()
    cookie = f"access_token={_fresh_token(admin_user)}; csrf_token=scoped; csrf_token=foreign"
    resp = client.post("/api/settings/trash", headers={"Cookie": cookie, "X-CSRF-Token": "scoped"},
                       follow_redirects=False)
    assert "CSRF" not in resp.text
    assert resp.status_code != 403


def test_default_reads_cookies_as_before(client, admin_user):
    """At the default nothing is scoped, so the reader is request.cookies unchanged."""
    from starlette.requests import Request
    from app.auth import read_cookie
    req = Request({"type": "http", "headers": [(b"cookie", b"csrf_token=a; csrf_token=b")]})
    assert read_cookie(req, "csrf_token") == req.cookies["csrf_token"]
