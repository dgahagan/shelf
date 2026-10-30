"""Token management routes: `/api/tokens`, cookie sessions only."""

import logging

import pytest

from app.auth import create_token as create_session
from app.services import api_tokens
from tests.conftest import _create_user
from tests.test_features_gate import assert_json_403, off

ROUTES = [("GET", "/api/tokens"), ("GET", "/api/tokens/all"),
          ("POST", "/api/tokens"), ("DELETE", "/api/tokens/1")]


def _mint(client, name="homepage"):
    return client.post("/api/tokens", data={"name": name}).json()


def _count(db, user_id):
    return db.execute("SELECT COUNT(*) FROM api_tokens WHERE user_id = ?", (user_id,)).fetchone()[0]


def _login_as(client, user):
    client.cookies.set("access_token", create_session(
        user["id"], user["username"], user["role"], user["display_name"]))


def test_a_viewer_mints_a_token_and_sees_it_once(viewer_client, viewer_user):
    body = _mint(viewer_client)
    assert body["ok"] is True
    assert body["token"].startswith("shelf_")
    assert body["prefix"] == body["token"][:12]
    assert body["name"] == "homepage"
    assert isinstance(body["id"], int) and body["created_at"]

    listed = viewer_client.get("/api/tokens").json()["tokens"]
    assert [t["id"] for t in listed] == [body["id"]]
    assert set(listed[0]) == {"id", "name", "prefix", "last_used_at", "created_at"}
    assert body["token"] not in viewer_client.get("/api/tokens").text


@pytest.mark.parametrize("fixture", ["editor_client", "admin_client"])
def test_editors_and_admins_mint_their_own(request, fixture):
    c = request.getfixturevalue(fixture)
    assert _mint(c)["ok"] is True
    assert len(c.get("/api/tokens").json()["tokens"]) == 1


@pytest.mark.parametrize("name", ["", "   ", "\u200b\u200b", "\n\t\ufeff"])
def test_an_empty_name_is_refused(viewer_client, viewer_user, db, name):
    assert _mint(viewer_client, name) == {"ok": False, "message": "Give the token a name"}
    assert _count(db, viewer_user["id"]) == 0


def test_whitespace_runs_in_a_name_collapse_to_one_space(viewer_client, viewer_user):
    body = _mint(viewer_client, "  home\npage\t\tdash  ")
    assert body["ok"] is True and body["name"] == "home page dash"


def test_a_65_character_name_is_refused(viewer_client, viewer_user, db):
    body = _mint(viewer_client, "x" * 65)
    assert body == {"ok": False, "message": "Keep the name to 64 characters or fewer"}
    assert _count(db, viewer_user["id"]) == 0
    assert _mint(viewer_client, "x" * 64)["ok"] is True


def test_the_26th_token_is_refused(viewer_client, viewer_user, db):
    for i in range(25):
        assert _mint(viewer_client, f"t{i}")["ok"] is True
    assert _mint(viewer_client, "one too many") == {
        "ok": False, "message": "You already have 25 tokens — revoke one first"}
    assert _count(db, viewer_user["id"]) == 25


def test_the_owner_revokes(viewer_client, viewer_user, db):
    token_id = _mint(viewer_client)["id"]
    assert viewer_client.delete(f"/api/tokens/{token_id}").json() == {"ok": True}
    assert _count(db, viewer_user["id"]) == 0


def test_a_viewer_cannot_revoke_someone_elses(client, admin_user, viewer_user, db):
    _login_as(client, admin_user)
    token_id = _mint(client)["id"]
    _login_as(client, viewer_user)
    assert client.delete(f"/api/tokens/{token_id}").json() == {
        "ok": False, "message": "Token not found"}
    assert _count(db, admin_user["id"]) == 1


def test_an_admin_revokes_anyones(client, admin_user, viewer_user, db):
    _login_as(client, viewer_user)
    token_id = _mint(client)["id"]
    _login_as(client, admin_user)
    assert client.delete(f"/api/tokens/{token_id}").json() == {"ok": True}
    assert _count(db, viewer_user["id"]) == 0


def test_revoking_a_missing_token(admin_client):
    assert admin_client.delete("/api/tokens/9999").json() == {
        "ok": False, "message": "Token not found"}


def test_the_admin_list_names_every_users_tokens(client, admin_user, viewer_user):
    _login_as(client, viewer_user)
    _mint(client, "viewer dash")
    _login_as(client, admin_user)
    _mint(client, "admin dash")
    tokens = client.get("/api/tokens/all").json()["tokens"]
    assert {(t["username"], t["name"]) for t in tokens} == {
        ("viewer", "viewer dash"), ("admin", "admin dash")}
    assert all("token" not in t and "token_hash" not in t for t in tokens)


@pytest.mark.parametrize("fixture", ["viewer_client", "editor_client"])
def test_the_admin_list_is_forbidden_below_admin(request, fixture):
    resp = request.getfixturevalue(fixture).get("/api/tokens/all")
    assert resp.status_code == 403
    assert resp.text == "Forbidden"


@pytest.mark.parametrize("method,path", ROUTES)
def test_anonymous_is_sent_to_login(client, viewer_user, method, path):
    client.cookies.clear()
    resp = client.request(method, path, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.parametrize("method,path", ROUTES)
def test_api_off_answers_the_feature_json(admin_client, method, path):
    off("api")
    assert_json_403(admin_client.request(method, path), "api", "API tokens")


def test_a_bearer_cannot_mint_a_token(client, viewer_user, db):
    created = api_tokens.create(db, viewer_user["id"], "dash")
    db.execute("COMMIT")
    client.cookies.clear()
    resp = client.post("/api/tokens", data={"name": "escalate"},
                       headers={"Authorization": f"Bearer {created['token']}"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    assert _count(db, viewer_user["id"]) == 1


def test_a_revoked_token_stops_reading_stats(viewer_client):
    body = _mint(viewer_client)
    bearer = {"Authorization": f"Bearer {body['token']}"}
    assert viewer_client.get("/api/v1/stats", headers=bearer).status_code == 200
    viewer_client.delete(f"/api/tokens/{body['id']}")
    assert viewer_client.get("/api/v1/stats", headers=bearer).status_code == 401


def test_the_log_names_the_token_but_never_shows_it(viewer_client, caplog):
    with caplog.at_level(logging.INFO, logger="app.routers.api_tokens"):
        body = _mint(viewer_client)
        viewer_client.delete(f"/api/tokens/{body['id']}")
    text = caplog.text
    assert "API token 'homepage' created for user 'viewer'" in text
    assert f"API token id={body['id']} revoked by 'viewer'" in text
    assert body["token"] not in text
    assert body["prefix"] not in text


def test_a_post_without_the_csrf_header_is_refused(viewer_client, viewer_user, db):
    viewer_client.headers.pop("X-CSRF-Token")
    resp = viewer_client.post("/api/tokens", data={"name": "x"})
    assert resp.status_code == 403
    assert "CSRF validation failed" in resp.text
    assert _count(db, viewer_user["id"]) == 0


def test_a_second_user_lists_only_their_own(client, viewer_user):
    other = _create_user("other", "password123", "Other", "viewer")
    _login_as(client, viewer_user)
    _mint(client, "mine")
    _login_as(client, other)
    assert client.get("/api/tokens").json()["tokens"] == []
