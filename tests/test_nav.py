"""Nav tab registry, visibility resolution, and settings-cache invalidation."""

import json

import pytest

from app.nav import (
    ALWAYS_VISIBLE,
    HIDEABLE_KEYS,
    NAV_TABS,
    invalidate_cache,
    visible_tabs,
)


def _set(db, key, value):
    """Write a setting straight to the test DB and drop the nav cache."""
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = ?",
        (key, value, value),
    )
    db.commit()
    invalidate_cache()


def _keys(user):
    return [t["key"] for t in visible_tabs(user)]


ADMIN = {"id": 1, "username": "admin", "role": "admin"}
EDITOR = {"id": 2, "username": "editor", "role": "editor"}
VIEWER = {"id": 3, "username": "viewer", "role": "viewer"}


# --- Registry shape ---------------------------------------------------------

def test_registry_covers_the_nine_tabs():
    assert [t["key"] for t in NAV_TABS] == [
        "browse", "scan", "intake", "store", "series", "discover",
        "stats", "settings", "logs",
    ]
    for tab in NAV_TABS:
        assert tab["label"] and tab["path"].startswith("/")


def test_browse_and_settings_are_not_hideable():
    assert set(ALWAYS_VISIBLE) == {"browse", "settings"}
    assert not HIDEABLE_KEYS & set(ALWAYS_VISIBLE)


# --- Role gating ------------------------------------------------------------

def test_viewer_sees_no_scan_intake_settings_or_logs(db):
    keys = _keys(VIEWER)
    assert "browse" in keys and "store" in keys and "stats" in keys
    for gated in ("scan", "intake", "settings", "logs"):
        assert gated not in keys


def test_editor_sees_scan_but_no_settings_or_logs(db):
    keys = _keys(EDITOR)
    assert "scan" in keys
    assert "settings" not in keys and "logs" not in keys


def test_admin_sees_settings_and_logs(db):
    keys = _keys(ADMIN)
    assert "settings" in keys and "logs" in keys and "scan" in keys


def test_anonymous_sees_only_ungated_tabs(db):
    keys = _keys(None)
    assert keys == ["browse", "store", "series", "stats"]


# --- Integration requirements ----------------------------------------------

def test_intake_hidden_without_a_vision_provider(db):
    assert "intake" not in _keys(ADMIN)


@pytest.mark.parametrize("provider,key", [
    ("anthropic", "anthropic_api_key"),
    ("openai", "openai_api_key"),
])
def test_intake_needs_the_providers_key(db, provider, key):
    _set(db, "vision_provider", provider)
    assert "intake" not in _keys(ADMIN)
    _set(db, key, "sk-test")
    assert "intake" in _keys(ADMIN)


def test_ollama_needs_only_the_provider(db):
    _set(db, "vision_provider", "ollama")
    assert "intake" in _keys(ADMIN)


def test_role_gating_beats_a_configured_integration(db):
    _set(db, "vision_provider", "ollama")
    assert "intake" not in _keys(VIEWER)


def test_discover_hidden_without_a_hardcover_token(db):
    assert "discover" not in _keys(ADMIN)
    _set(db, "hardcover_token", "hc-token")
    assert "discover" in _keys(ADMIN)


def test_discover_hidden_when_the_token_is_blank(db):
    _set(db, "hardcover_token", "   ")
    assert "discover" not in _keys(ADMIN)


def test_env_provided_token_counts_as_configured(db, monkeypatch):
    monkeypatch.setenv("HARDCOVER_TOKEN", "env-token")
    invalidate_cache()
    assert "discover" in _keys(ADMIN)


# --- Manual hiding ----------------------------------------------------------

def test_hidden_tabs_are_dropped(db):
    _set(db, "nav_hidden_tabs", json.dumps(["stats", "store"]))
    keys = _keys(ADMIN)
    assert "stats" not in keys and "store" not in keys
    assert "browse" in keys and "settings" in keys


def test_browse_and_settings_survive_a_forged_hidden_list(db):
    _set(db, "nav_hidden_tabs", json.dumps(["browse", "settings"]))
    keys = _keys(ADMIN)
    assert "browse" in keys and "settings" in keys


@pytest.mark.parametrize("stored", ["not json", "{}", '"stats"', "[1, 2]", "null", ""])
def test_malformed_hidden_lists_hide_nothing(db, stored):
    _set(db, "nav_hidden_tabs", stored)
    keys = _keys(ADMIN)
    assert "stats" in keys and "browse" in keys


def test_unknown_keys_in_the_hidden_list_are_ignored(db):
    _set(db, "nav_hidden_tabs", json.dumps(["stats", "nonexistent"]))
    assert "stats" not in _keys(ADMIN)


# --- Caching ----------------------------------------------------------------

def test_settings_are_cached_between_calls(db):
    visible_tabs(ADMIN)  # warm
    db.execute("INSERT INTO settings (key, value) VALUES ('hardcover_token', 'hc')")
    db.commit()
    assert "discover" not in _keys(ADMIN)  # stale by design
    invalidate_cache()
    assert "discover" in _keys(ADMIN)


def test_saving_a_token_makes_discover_appear_without_a_restart(admin_client, db):
    assert "discover" not in _keys(ADMIN)
    r = admin_client.post("/api/settings", data={"hardcover_token": "hc-token"},
                          follow_redirects=False)
    assert r.status_code == 303
    assert "discover" in _keys(ADMIN)


def test_saving_vision_settings_makes_intake_appear_without_a_restart(admin_client, db):
    assert "intake" not in _keys(ADMIN)
    r = admin_client.post("/api/settings/vision", data={"vision_provider": "ollama"},
                          follow_redirects=False)
    assert r.status_code == 303
    assert "intake" in _keys(ADMIN)


def test_each_test_starts_with_a_cold_cache(db):
    """The autouse fixture resets the cache, so no prior test's config leaks."""
    import app.nav as nav_mod
    assert nav_mod._cached_settings is None or nav_mod._cached_settings.get("hardcover_token") in ("", None)


# --- Context injection ------------------------------------------------------

class _FakeRequest:
    """Just enough of a Request for the TemplateResponse wrapper."""

    def __init__(self, user):
        self.state = type("S", (), {"user": user})()


def _captured_context(monkeypatch, user, **call):
    """Run the main.py TemplateResponse wrapper and return the context it built."""
    import app.main as main_mod
    seen = {}

    def _fake(request_or_self, *args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return None

    monkeypatch.setattr(main_mod, "_original_template_response", _fake)
    request = _FakeRequest(user)
    if "context" in call:
        main_mod._template_response_with_user(request, "page.html", context=call["context"])
        return call["context"]
    main_mod._template_response_with_user(request, "page.html", call["positional"])
    return call["positional"]


def test_wrapper_injects_nav_tabs_with_keyword_context(monkeypatch, db):
    ctx = _captured_context(monkeypatch, ADMIN, context={})
    assert ctx["user"] == ADMIN
    assert [t["key"] for t in ctx["nav_tabs"]] == _keys(ADMIN)


def test_wrapper_injects_nav_tabs_with_positional_context(monkeypatch, db):
    ctx = _captured_context(monkeypatch, VIEWER, positional={"items": []})
    assert ctx["user"] == VIEWER
    assert "settings" not in [t["key"] for t in ctx["nav_tabs"]]


def test_wrapper_does_not_override_an_explicit_nav_tabs(monkeypatch, db):
    ctx = _captured_context(monkeypatch, ADMIN, context={"nav_tabs": []})
    assert ctx["nav_tabs"] == []


# --- Rendered nav -----------------------------------------------------------

def test_rendered_nav_follows_the_registry(admin_client, db):
    """The nav bar itself renders from `nav_tabs`, not from hardcoded anchors."""
    html = admin_client.get("/browse").text
    assert 'data-nav-tab="browse"' in html
    assert 'data-nav-tab="settings"' in html
    assert 'data-nav-tab="discover"' not in html  # no token configured
    _set(db, "hardcover_token", "hc-token")
    assert 'data-nav-tab="discover"' in admin_client.get("/browse").text


def test_rendered_nav_respects_role(viewer_client, db):
    html = viewer_client.get("/browse").text
    assert 'data-nav-tab="browse"' in html
    for gated in ("scan", "settings", "logs"):
        assert f'data-nav-tab="{gated}"' not in html


def test_active_tab_is_highlighted(admin_client, db):
    html = admin_client.get("/stats").text
    import re
    anchor = re.search(r'<a[^>]*data-nav-tab="stats"[^>]*>', html).group(0)
    assert "bg-shelf-hover text-white" in anchor
    other = re.search(r'<a[^>]*data-nav-tab="store"[^>]*>', html).group(0)
    assert "text-shelf-muted" in other


# --- POST /api/settings/nav --------------------------------------------------

def _post_nav(client, checked_keys):
    """POST the nav form with only the given keys present (as checked boxes)."""
    data = {k: "on" for k in checked_keys}
    return client.post("/api/settings/nav", data=data, follow_redirects=False)


def test_hiding_a_tab_then_unhiding_round_trips(admin_client, db):
    all_but_stats = HIDEABLE_KEYS - {"stats"}
    r = _post_nav(admin_client, all_but_stats)
    assert r.status_code == 303
    assert "stats" not in _keys(ADMIN)

    r = _post_nav(admin_client, HIDEABLE_KEYS)
    assert r.status_code == 303
    assert "stats" in _keys(ADMIN)


def test_browse_and_settings_cannot_be_hidden_via_forged_form(admin_client, db):
    # Submit nothing checked at all, plus forged unchecks for browse/settings —
    # neither key is hideable, so nothing can remove them from the nav.
    r = admin_client.post(
        "/api/settings/nav",
        data={"browse": "", "settings": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    keys = _keys(ADMIN)
    assert "browse" in keys and "settings" in keys


def test_unknown_keys_in_the_form_are_not_stored(admin_client, db):
    r = admin_client.post(
        "/api/settings/nav",
        data={"nonexistent": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    row = db.execute(
        "SELECT value FROM settings WHERE key = 'nav_hidden_tabs'"
    ).fetchone()
    stored = json.loads(row["value"])
    assert "nonexistent" not in stored
    # every hideable key was unchecked (only 'nonexistent' was posted), so
    # every hideable key ends up hidden — but nothing else.
    assert set(stored) == HIDEABLE_KEYS


def test_editor_cannot_post_nav_settings(editor_client, db):
    r = _post_nav(editor_client, HIDEABLE_KEYS)
    assert r.status_code == 403


def test_viewer_cannot_post_nav_settings(viewer_client, db):
    r = _post_nav(viewer_client, HIDEABLE_KEYS)
    assert r.status_code == 403


@pytest.mark.parametrize("stored", ["not json", "{}", '"stats"', "[1, 2]"])
def test_malformed_hidden_tabs_setting_does_not_500_the_settings_page(admin_client, db, stored):
    _set(db, "nav_hidden_tabs", stored)
    r = admin_client.get("/settings")
    assert r.status_code == 200


# --- Settings page: Navigation card -----------------------------------------

def test_navigation_card_renders_with_expected_checkbox_state(admin_client, db):
    _set(db, "nav_hidden_tabs", json.dumps(["stats"]))
    html = admin_client.get("/settings").text
    assert "Navigation" in html
    assert 'action="/api/settings/nav"' in html

    import re
    stats_input = re.search(r'<input[^>]*name="stats"[^>]*>', html).group(0)
    assert "checked" not in stats_input

    scan_input = re.search(r'<input[^>]*name="scan"[^>]*>', html).group(0)
    assert "checked" in scan_input
