"""Nav tab registry, visibility resolution, and settings-cache invalidation."""

import json

import pytest

from app.nav import (
    ALWAYS_VISIBLE,
    HIDEABLE_KEYS,
    HIDEABLE_TABS,
    NAV_TABS,
    hideable_tab_states,
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


# --- Settings page: auto-hidden tab annotations (issue #22) ------------------
#
# The checkbox and the hint answer two different questions, so every test here
# asserts *both*: "checked" alone passes on the buggy build that shipped a
# checked box for a tab missing from the nav, and the hint alone can't prove
# the preference survived.

def _row(html, key):
    """The Navigation card row for one tab key. Rows contain no nested divs,
    so a non-greedy match to the first closing tag is the whole row."""
    import re
    m = re.search(rf'<div data-nav-setting-row="{key}".*?</div>', html, re.S)
    assert m, f"no Navigation-card row rendered for {key!r}"
    return m.group(0)


def _is_checked(row):
    import re
    return "checked" in re.search(r"<input[^>]*>", row).group(0)


def _has_hint(row):
    return "Hidden until" in row


def test_discover_row_is_checked_and_hinted_without_a_token(admin_client, db):
    """The actual fix: not manually hidden (so checked) *and* unavailable (so
    hinted). Either assertion alone passes on the pre-fix build."""
    row = _row(admin_client.get("/settings").text, "discover")
    assert _is_checked(row)
    assert _has_hint(row)
    assert "a Hardcover token" in row
    assert 'data-testid="configure-discover"' in row


def test_discover_row_loses_the_hint_once_a_token_is_saved(admin_client, db):
    _set(db, "hardcover_token", "hc-token")
    row = _row(admin_client.get("/settings").text, "discover")
    assert _is_checked(row)
    assert not _has_hint(row)
    assert 'data-testid="configure-discover"' not in row


def test_intake_row_is_hinted_until_a_vision_provider_is_set(admin_client, db):
    row = _row(admin_client.get("/settings").text, "intake")
    assert _is_checked(row)
    assert _has_hint(row)
    assert "a vision provider" in row

    _set(db, "vision_provider", "ollama")
    row = _row(admin_client.get("/settings").text, "intake")
    assert _is_checked(row)
    assert not _has_hint(row)


def test_a_tab_without_requirements_never_renders_a_hint(admin_client, db):
    row = _row(admin_client.get("/settings").text, "stats")
    assert _is_checked(row)
    assert not _has_hint(row)


def test_an_env_provided_token_leaves_the_discover_row_unhinted(admin_client, db, monkeypatch):
    """The settings card must agree with the nav bar on env-only credentials.

    `get_all_settings()` — what the route reads for every other field — omits
    keys with no row in the table, so feeding it to `hideable_tab_states()`
    would render "Hidden until a Hardcover token is set" beside a Discover tab
    that is visible in the nav. Pins the no-argument call in the route.
    """
    monkeypatch.setenv("HARDCOVER_TOKEN", "env-token")
    invalidate_cache()
    row = _row(admin_client.get("/settings").text, "discover")
    assert _is_checked(row)
    assert not _has_hint(row)
    assert "discover" in _keys(ADMIN)  # and the nav bar agrees


def test_a_manually_hidden_unavailable_tab_is_unchecked_and_hinted(admin_client, db):
    """The fourth hidden/available combination. A template written as
    `checked` when `not hidden or not available` passes every other test here
    but renders this row checked."""
    _set(db, "nav_hidden_tabs", json.dumps(["discover"]))
    row = _row(admin_client.get("/settings").text, "discover")
    assert not _is_checked(row)
    assert _has_hint(row)


def test_saving_the_form_while_a_tab_is_auto_hidden_keeps_the_preference(admin_client, db):
    """The trap the disabled-checkbox alternative would have shipped.

    A disabled checkbox posts nothing, and `update_nav_settings` derives
    hidden = HIDEABLE_KEYS - checked — so saving this form would have written
    Discover into `nav_hidden_tabs` permanently, and configuring Hardcover
    later would not have brought it back.
    """
    r = _post_nav(admin_client, HIDEABLE_KEYS)  # every box checked, no token
    assert r.status_code == 303
    assert "discover" not in _keys(ADMIN)  # still auto-hidden, correctly

    row = db.execute("SELECT value FROM settings WHERE key = 'nav_hidden_tabs'").fetchone()
    assert "discover" not in json.loads(row["value"])

    r = admin_client.post("/api/settings", data={"hardcover_token": "hc-token"},
                          follow_redirects=False)
    assert r.status_code == 303
    assert "discover" in _keys(ADMIN)


# --- hideable_tab_states() ---------------------------------------------------

def _state(states, key):
    return next(s for s in states if s["key"] == key)


def test_discover_available_reflects_the_token(db):
    states = hideable_tab_states()
    assert _state(states, "discover")["available"] is False
    _set(db, "hardcover_token", "hc-token")
    assert _state(hideable_tab_states(), "discover")["available"] is True


def test_intake_available_without_a_provider_is_false(db):
    assert _state(hideable_tab_states(), "intake")["available"] is False


def test_intake_available_anthropic_without_a_key_is_false(db):
    _set(db, "vision_provider", "anthropic")
    assert _state(hideable_tab_states(), "intake")["available"] is False


def test_intake_available_ollama_on_defaults_is_true(db):
    _set(db, "vision_provider", "ollama")
    assert _state(hideable_tab_states(), "intake")["available"] is True


@pytest.mark.parametrize("key", ["stats", "store"])
def test_tabs_without_requires_are_always_available(db, key):
    state = _state(hideable_tab_states(), key)
    assert state["available"] is True
    assert state["requirement_label"] == ""


def test_hidden_is_independent_of_available(db):
    _set(db, "nav_hidden_tabs", json.dumps(["discover"]))
    state = _state(hideable_tab_states(), "discover")
    assert state["hidden"] is True
    assert state["available"] is False  # no token — unrelated to the hide


def test_requiring_tabs_carry_the_label_the_settings_hint_renders(db):
    """The hint text is built from this string, so pin it here rather than
    only at the template layer."""
    states = hideable_tab_states()
    assert _state(states, "discover")["requirement_label"] == "a Hardcover token"
    assert _state(states, "intake")["requirement_label"] == "a vision provider"


def test_returns_every_hideable_key_in_registry_order(db):
    states = hideable_tab_states()
    assert [s["key"] for s in states] == [t["key"] for t in HIDEABLE_TABS]


def test_env_provided_token_counts_as_configured_with_no_argument(db, monkeypatch):
    """Mirrors test_env_provided_token_counts_as_configured above, but pins
    hideable_tab_states() to the no-arg path: passing a settings dict from
    get_all_settings() would silently break env-only credentials, since that
    dict doesn't run through get_setting()'s env-override logic."""
    monkeypatch.setenv("HARDCOVER_TOKEN", "env-token")
    invalidate_cache()
    states = hideable_tab_states()
    assert _state(states, "discover")["available"] is True
