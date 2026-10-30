"""Bearer tokens on `/api/v1`: who gets in, as what, and what everything else still answers.

The cookieless cases use `client` plus a seeded user (zero users would send
everything to /setup, G117). Redirect pins pass `follow_redirects=False` and
assert the location by equality.
"""

from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi import Depends

from app.auth import get_secret_key, require_role
from app.config import JWT_ALGORITHM, JWT_EXPIRY_SECONDS
from app.services import api_tokens
from tests.test_features_gate import assert_json_403, off

STATS = "/api/v1/stats"


def _token_for(db, user):
    created = api_tokens.create(db, user["id"], "dashboard")
    db.execute("COMMIT")
    return created["token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _assert_v1_401(resp, message):
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"
    assert resp.headers["cache-control"] == "no-store"
    assert "location" not in resp.headers
    assert resp.json() == {"error": "unauthorized", "ok": False, "message": message}


@pytest.fixture
def probe():
    """A v1 route behind an editor guard, present only for the test.

    Removed at teardown so the registry lints and the response-model walk
    never see it.
    """
    from app.main import app

    async def _probe(_=Depends(require_role("editor"))):
        return {"ok": True}

    before = list(app.router.routes)
    app.router.add_api_route("/api/v1/_probe", _probe, methods=["GET"])
    yield "/api/v1/_probe"
    app.router.routes[:] = before


# --- the token reads v1, and nothing else ---------------------------------------

def test_a_valid_token_reads_stats_without_a_cookie(client, viewer_user, db):
    token = _token_for(db, viewer_user)
    client.cookies.clear()
    resp = client.get(STATS, headers=_bearer(token))
    assert resp.status_code == 200
    assert isinstance(resp.json()["items"]["total"], int)


def test_the_scheme_is_case_insensitive(client, viewer_user, db):
    token = _token_for(db, viewer_user)
    client.cookies.clear()
    resp = client.get(STATS, headers={"Authorization": f"bearer   {token}"})
    assert resp.status_code == 200


@pytest.mark.parametrize("path", ["/api/settings/backup", "/api/users", "/api/items", "/browse"])
def test_the_token_is_no_credential_outside_v1(client, admin_user, db, path):
    token = _token_for(db, admin_user)
    client.cookies.clear()
    resp = client.get(path, headers=_bearer(token), follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_a_bearer_on_token_management_is_no_credential(client, admin_user, db):
    token = _token_for(db, admin_user)
    client.cookies.clear()
    resp = client.get("/api/tokens", headers=_bearer(token), follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_an_admins_token_resolves_to_a_viewer(admin_user, db):
    token = _token_for(db, admin_user)
    assert api_tokens.resolve(token)["role"] == "viewer"


def test_an_admins_token_cannot_pass_an_editor_guard(client, admin_user, db, probe):
    token = _token_for(db, admin_user)
    client.cookies.clear()
    assert client.get(probe, headers=_bearer(token)).status_code == 403


def test_the_probe_admits_an_admin_cookie(admin_client, probe):
    """The probe's 403 above is the cap, not a broken probe."""
    assert admin_client.get(probe).status_code == 200


# --- refusals --------------------------------------------------------------------

@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_a_bearer_write_is_refused_before_csrf(client, viewer_user, db, method):
    token = _token_for(db, viewer_user)
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    resp = client.request(method, STATS, headers=_bearer(token))
    _assert_v1_401(resp, "Tokens are read-only: only GET is accepted with a bearer token")


@pytest.mark.parametrize("header", [
    "Bearer shelf_not-a-real-token",
    "Basic dXNlcjpwYXNz",
    "Bearer",
    "Bearer    ",
    "Bearer shelf_" + "x" * 200,
    "Bearer not-even-prefixed",
])
def test_a_bad_credential_is_401_json(client, viewer_user, header):
    client.cookies.clear()
    resp = client.get(STATS, headers={"Authorization": header})
    _assert_v1_401(resp, "Invalid or revoked API token")


def test_no_cookie_and_no_token_is_401_json(client, viewer_user):
    client.cookies.clear()
    resp = client.get(STATS, follow_redirects=False)
    _assert_v1_401(resp, "Sign in or send an API token")


def test_other_api_paths_still_redirect_anonymous_requests(client, viewer_user):
    client.cookies.clear()
    resp = client.get("/api/items", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_head_with_a_valid_token_is_200(client, viewer_user, db):
    token = _token_for(db, viewer_user)
    client.cookies.clear()
    resp = client.head(STATS, headers=_bearer(token))
    assert resp.status_code == 200


def test_a_revoked_token_is_401(client, viewer_user, db):
    created = api_tokens.create(db, viewer_user["id"], "gone")
    db.execute("COMMIT")
    api_tokens.revoke(db, created["id"])
    db.execute("COMMIT")
    client.cookies.clear()
    _assert_v1_401(client.get(STATS, headers=_bearer(created["token"])),
                   "Invalid or revoked API token")


# --- bearer and cookie together ----------------------------------------------------

def test_the_bearer_decides_over_an_admin_cookie(admin_client, viewer_user, db, probe):
    token = _token_for(db, viewer_user)
    assert admin_client.get(probe, headers=_bearer(token)).status_code == 403


def test_a_bad_bearer_with_a_good_cookie_is_401(admin_client):
    resp = admin_client.get(STATS, headers=_bearer("shelf_wrong"))
    _assert_v1_401(resp, "Invalid or revoked API token")


def _cookie_past_half_life(user):
    iat = datetime.now(timezone.utc) - timedelta(seconds=JWT_EXPIRY_SECONDS * 0.6)
    payload = {
        "sub": str(user["id"]), "username": user["username"], "role": user["role"],
        "display_name": user["display_name"], "tv": 1,
        "iat": iat, "exp": iat + timedelta(seconds=JWT_EXPIRY_SECONDS),
    }
    return pyjwt.encode(payload, get_secret_key(), algorithm=JWT_ALGORITHM)


def test_a_bearer_request_never_refreshes_the_cookie(client, admin_user, db):
    """A cookie minted from the viewer-capped dict would demote the session (G10)."""
    token = _token_for(db, admin_user)
    client.cookies.set("access_token", _cookie_past_half_life(admin_user))
    resp = client.get(STATS, headers=_bearer(token))
    assert resp.status_code == 200
    assert "set-cookie" not in resp.headers


def test_a_cookie_session_on_v1_works_and_still_refreshes(client, admin_user):
    client.cookies.set("access_token", _cookie_past_half_life(admin_user))
    resp = client.get(STATS)
    assert resp.status_code == 200
    assert "access_token=" in resp.headers.get("set-cookie", "")


def test_a_fresh_cookie_session_on_v1_works(viewer_client):
    assert viewer_client.get(STATS).status_code == 200


# --- the flag answers after auth ------------------------------------------------------

def test_api_off_gives_a_valid_bearer_the_feature_403(client, viewer_user, db):
    token = _token_for(db, viewer_user)
    off("api")
    client.cookies.clear()
    assert_json_403(client.get(STATS, headers=_bearer(token)), "api", "API tokens")


def test_a_password_change_leaves_the_token_working(client, viewer_user, db):
    token = _token_for(db, viewer_user)
    db.execute("UPDATE users SET token_version = token_version + 1 WHERE id = ?",
               (viewer_user["id"],))
    db.execute("COMMIT")
    client.cookies.clear()
    assert client.get(STATS, headers=_bearer(token)).status_code == 200

