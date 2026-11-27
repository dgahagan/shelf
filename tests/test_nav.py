"""Nav tab registry, visibility resolution, and settings-cache invalidation."""

import json

import pytest

from app.nav import (
    ALWAYS_VISIBLE,
    HIDEABLE_KEYS,
    HIDEABLE_TABS,
    NAV_GROUPS,
    NAV_TABS,
    hideable_tab_states,
    invalidate_cache,
    nav_row,
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

def test_registry_covers_the_sixteen_tabs():
    assert [t["key"] for t in NAV_TABS] == [
        "browse", "scan", "series", "music", "periodicals", "stats", "intake",
        "shelf-fill", "store", "discover", "trash", "labels", "audits",
        "duplicates", "settings", "logs",
    ]
    for tab in NAV_TABS:
        assert tab["label"] and tab["path"].startswith("/")


def test_only_trash_labels_audits_duplicates_settings_and_logs_render_in_the_account_menu():
    """`menu` is what base.html asks instead of naming a key.

    It is the whole reason a tab can move out of the row without either
    template learning its name, so pin the membership rather than the flag.
    """
    assert [t["key"] for t in NAV_TABS if t.get("menu") == "account"] == [
        "trash", "labels", "audits", "duplicates", "settings", "logs",
    ]
    assert all(t.get("menu", "") in ("", "account") for t in NAV_TABS)


def test_visible_tabs_carries_the_menu_destination(db):
    """visible_tabs() is the only thing the templates see."""
    by_key = {t["key"]: t for t in visible_tabs(ADMIN)}
    assert by_key["logs"]["menu"] == "account"
    assert by_key["settings"]["menu"] == "account"
    assert by_key["browse"]["menu"] == ""


def test_every_group_value_is_a_declared_group():
    assert {t["group"] for t in NAV_TABS if "group" in t} <= set(NAV_GROUPS)


def test_no_tab_is_both_grouped_and_in_a_menu():
    assert not [t["key"] for t in NAV_TABS if t.get("group") and t.get("menu")]


def test_group_members_are_contiguous_in_the_registry():
    for group in NAV_GROUPS:
        idx = [i for i, t in enumerate(NAV_TABS) if t.get("group") == group]
        assert idx == list(range(idx[0], idx[0] + len(idx))), group


def test_every_group_has_at_least_two_members():
    for group in NAV_GROUPS:
        assert sum(1 for t in NAV_TABS if t.get("group") == group) >= 2, group


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
    assert keys == ["browse", "series", "music", "periodicals", "stats", "store"]


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


# --- Feature gating ----------------------------------------------------------

def _set_feature(key, enabled):
    from app.database import get_db
    from app.features import set_feature_enabled
    with get_db() as conn:
        set_feature_enabled(conn, key, enabled)


def test_disabling_a_feature_hides_its_tab_for_every_role(db):
    _set_feature("stats", False)
    for user in (ADMIN, EDITOR, VIEWER, None):
        assert "stats" not in _keys(user)


def test_disabling_hardcover_hides_discover_even_with_a_token(db):
    _set(db, "hardcover_token", "hc-token")
    assert "discover" in _keys(ADMIN)
    _set_feature("hardcover", False)
    assert "discover" not in _keys(ADMIN)


def test_hand_hidden_tab_of_an_enabled_feature_is_still_hidden(db):
    _set(db, "nav_hidden_tabs", json.dumps(["stats"]))
    assert "stats" not in _keys(ADMIN)


def test_disabled_features_tab_stays_out_when_not_hand_hidden(db):
    _set_feature("stats", False)
    # nav_hidden_tabs is untouched — the tab is absent purely on the feature flag.
    assert "stats" not in _keys(ADMIN)


def test_hideable_tab_states_reports_feature_off(db):
    state = _state(hideable_tab_states(), "stats")
    assert state["feature_off"] is False
    assert state["feature_label"] == "Statistics"

    _set_feature("stats", False)
    state = _state(hideable_tab_states(), "stats")
    assert state["feature_off"] is True
    assert state["feature_label"] == "Statistics"


def test_hideable_tab_states_feature_label_blank_for_a_featureless_tab(db):
    """`scan` (and `trash`, `logs`) belong to no feature — never turned off,
    never labelled."""
    state = _state(hideable_tab_states(), "trash")
    assert state["feature_off"] is False
    assert state["feature_label"] == ""


# --- Settings page: feature-off annotation -----------------------------------

def test_navigation_row_shows_turned_off_in_features(admin_client, db):
    _set_feature("stats", False)
    row = _row(admin_client.get("/settings").text, "stats")
    assert "Turned off in Features" in row
    assert _is_checked(row)

    import re
    checkbox = re.search(r"<input[^>]*>", row).group(0)
    assert "disabled" not in checkbox


def test_navigation_row_feature_off_beats_the_configure_hint(admin_client, db):
    """discover has both a `requires` hint and, once disabled, a feature-off
    one — the feature-off message wins; the tab is gone from the nav anyway."""
    _set_feature("hardcover", False)
    row = _row(admin_client.get("/settings").text, "discover")
    assert "Turned off in Features" in row
    assert "Hidden until" not in row


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


# --- Rendered groups (app.nav.nav_row through base.html) -----------------------
# Asserted on the data-nav-* hooks, never on the label words (G69).

def _apply_profile(name):
    from app.database import get_db
    from app.features import apply_profile
    with get_db() as conn:
        apply_profile(conn, name)
        conn.commit()


def test_everything_renders_both_group_disclosures(admin_client, db):
    _set(db, "vision_provider", "ollama")
    _set(db, "hardcover_token", "hc-token")
    _apply_profile("everything")
    html = admin_client.get("/browse").text
    assert html.count('data-nav-group="explore"') == 1
    assert html.count('data-nav-group="tools"') == 1
    for key in ("series", "music", "periodicals", "stats", "intake", "shelf-fill"):
        assert html.count(f'data-nav-tab="{key}"') == 1, key


def test_standard_shows_explore_and_shelf_fill_as_a_plain_tab(admin_client, db):
    _apply_profile("standard")
    html = admin_client.get("/browse").text
    assert 'data-nav-group="explore"' in html
    assert 'data-nav-group="tools"' not in html
    assert 'data-nav-tab="shelf-fill"' in html
    assert 'data-nav-menu-heading="tools"' not in html


def test_minimal_renders_no_group(admin_client, db):
    _apply_profile("minimal")
    assert "data-nav-group=" not in admin_client.get("/browse").text


def test_viewer_gets_no_tools_group(viewer_client, db):
    _set(db, "vision_provider", "ollama")
    assert 'data-nav-group="tools"' not in viewer_client.get("/browse").text


def test_group_button_is_active_on_a_member_page(admin_client, db):
    import re
    pattern = r'<button[^>]*data-nav-group-button="explore"[^>]*>'
    on_member = re.search(pattern, admin_client.get("/series").text).group(0)
    assert "bg-shelf-hover text-white" in on_member
    elsewhere = re.search(pattern, admin_client.get("/browse").text).group(0)
    assert "text-shelf-muted" in elsewhere
    assert "bg-shelf-hover text-white" not in elsewhere


def test_hamburger_heads_each_group_with_its_members(admin_client, db):
    html = admin_client.get("/browse").text
    heading = html.index('data-nav-menu-heading="explore"')
    assert html.index('data-nav-menu-tab="series"', heading) > heading
    panel_end = html.index('<div class="hidden lg:flex items-center gap-1">')
    assert heading < panel_end


def _hamburger_sequence(html):
    """The ☰ panel's children in order: tab keys, '#<group>' headings, '|' dividers."""
    import re
    start = html.index('data-testid="nav-menu-panel"')
    end = html.index('<div class="hidden lg:flex items-center gap-1">', start)
    seq = []
    for m in re.finditer(
        r'data-nav-menu-(?:tab="([^"]+)"|heading="([^"]+)"|(divider))', html[start:end]
    ):
        tab, heading, divider = m.groups()
        seq.append(tab or (f"#{heading}" if heading else "|"))
    return seq


def test_hamburger_closes_a_group_before_a_plain_tab(admin_client, db):
    """A plain tab after a group must not read as its member (qa Observation 1):
    a divider opens each group after the first entry and closes it before a tab."""
    _set(db, "vision_provider", "ollama")
    _set(db, "hardcover_token", "hc-token")
    _apply_profile("everything")
    seq = _hamburger_sequence(admin_client.get("/browse").text)
    assert seq == ["browse", "scan", "|", "#explore", "series", "music", "periodicals",
                   "stats", "|", "#tools", "intake", "shelf-fill", "|", "store", "discover"]


def test_hamburger_divider_after_a_group_before_a_singleton_tab(admin_client, db):
    _apply_profile("standard")
    seq = _hamburger_sequence(admin_client.get("/browse").text)
    assert seq == ["browse", "scan", "|", "#explore", "series", "music", "periodicals",
                   "stats", "|", "shelf-fill", "store"]


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


# --- Reachability: the census that 0.37.0 shipped without ---------------------

def test_every_top_level_page_is_reachable_from_the_nav():
    """A page nobody can navigate to is a page that does not ship.

    v0.37.0 shipped four such pages — Home, Music, Periodicals and Shelf Fill
    all rendered correctly at their URL and appeared nowhere in `NAV_TABS`, so
    every route test passed and no user could reach any of them. Route tests
    prove a page *renders*; this proves a page can be *found*.

    Parameterised pages (an item, a publication, one location's Arrange) are
    reached from a listing rather than the nav and are exempt by shape. The
    rest are named individually, each with the reason it needs no tab — an
    entry added here is a deliberate decision, which is exactly what was
    missing.
    """
    import os
    os.environ.setdefault("SHELF_DISABLE_RATE_LIMIT", "1")
    from app.main import app
    from app.nav import NAV_TABS

    EXEMPT = {
        "/": "the brand link in base.html is the Home affordance",
        "/login": "pre-auth",
        "/setup": "pre-auth, first run only",
        "/health": "machine endpoint, not a page",
        "/sw.js": "service worker asset",
        "/docs": "FastAPI's own docs UI",
        "/docs/oauth2-redirect": "FastAPI's own docs UI",
        "/redoc": "FastAPI's own docs UI",
        "/openapi.json": "schema, not a page",
        "/labels/sheet": (
            "reached from the Print sheet form and the Assign result on /labels, "
            "and from the item page's Print-labels link — a print view of a "
            "selection, not a destination (copy-labels plan)"
        ),
        "/stats/wrapup": (
            "reached from the Wrap-up link on /stats, not the nav: a recap of the "
            "stats is a stats surface (wrap-ups design). The link is pinned by "
            "test_wrapup_route.py::test_stats_links_to_the_wrapup_always"
        ),
        "/upcoming": "reached from the Upcoming releases link in the Series page header, not the nav (release-radar plan); pinned by tests/test_upcoming_page.py",
        "/cover-review": (
            "reached from Settings -> Data -> Maintenance, not the nav: a "
            "maintenance chore does not earn permanent nav real estate "
            "(cover-attention-queue design, section 3). The link's existence is "
            "pinned by test_settings.py's cover-review-link tests, so this "
            "exemption is a claim with a test behind it rather than a hole."
        ),
    }

    nav_paths = {t["path"] for t in NAV_TABS}
    unreachable = []
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", set()) or set()
        if not path or "GET" not in methods:
            continue
        if path.startswith("/api") or path.startswith("/static"):
            continue
        if "{" in path:            # reached from a listing, not the nav
            continue
        if path in EXEMPT or path in nav_paths:
            continue
        unreachable.append(path)

    assert not unreachable, (
        "these pages render but nothing links to them — give each a NAV_TABS "
        f"entry or an EXEMPT reason: {sorted(unreachable)}"
    )


def test_the_brand_link_goes_home(admin_client):
    """The one link that reaches `/`. If it drifts, Home is unreachable again."""
    html = admin_client.get("/browse").text
    assert '<a href="/" class="text-lg font-bold' in html


# --- Cross-links into Browse -------------------------------------------------

def test_every_browse_link_uses_a_registered_filter_name():
    """A `/browse?...` link whose parameter is not a real filter is ignored.

    Silently: `browse_filters.values_from` reads the names it knows and drops
    the rest, so the page renders fine and shows *everything*. v0.37.0's Home
    linked its media-type breakdown at `?media_type=` and its lent-out tile at
    `?lent=`, when the registry names them `media_type_filter` and `lent_out` —
    both tiles looked right and neither filtered anything.

    `app/browse_filters.py` is the single declaration; this holds every caller
    to it. `view` is registered `in_url=False` and so is not a legal link
    parameter either.
    """
    import re
    from pathlib import Path
    from app.browse_filters import BY_NAME

    linkable = {name for name, f in BY_NAME.items() if f.in_url}
    roots = [Path("app/templates"), Path("static/js")]
    bad = []
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix not in (".html", ".js"):
                continue
            for m in re.finditer(r"/browse\?([^\"'\s>]+)", path.read_text()):
                for pair in m.group(1).split("&amp;" if "&amp;" in m.group(1) else "&"):
                    key = pair.split("=")[0].strip()
                    if key and key not in linkable:
                        bad.append(f"{path}: ?{key}=")

    assert not bad, (
        "these links pass a parameter Browse does not know, so the filter is "
        f"silently dropped: {sorted(set(bad))}"
    )


# --- nav_row ----------------------------------------------------------------

def _t(key, group="", menu=""):
    return {"key": key, "label": key, "path": "/" + key, "group": group, "menu": menu}


def test_nav_row_folds_two_members_into_one_group_at_the_first_position():
    row = nav_row([_t("a"), _t("s", "explore"), _t("b"), _t("m", "explore"), _t("c")])
    assert [e["kind"] for e in row] == ["tab", "group", "tab", "tab"]
    group = row[1]
    assert group["key"] == "explore" and group["label"] == NAV_GROUPS["explore"]
    assert [t["key"] for t in group["members"]] == ["s", "m"]
    assert group["paths"] == ["/s", "/m"]


def test_nav_row_emits_a_lone_member_as_a_plain_tab_in_place():
    row = nav_row([_t("a"), _t("s", "explore"), _t("b")])
    assert [(e["kind"], e["tab"]["key"]) for e in row] == [
        ("tab", "a"), ("tab", "s"), ("tab", "b"),
    ]


def test_nav_row_emits_nothing_for_an_empty_group():
    assert nav_row([_t("a")]) == [{"kind": "tab", "tab": _t("a")}]


def test_nav_row_skips_menu_tabs():
    row = nav_row([_t("a"), _t("logs", menu="account")])
    assert [e["tab"]["key"] for e in row] == ["a"]


def test_nav_row_of_nothing_is_nothing():
    assert nav_row([]) == []


def _row_shape(user):
    return [(e["kind"], e.get("key") or e["tab"]["key"]) for e in nav_row(visible_tabs(user))]


def test_viewer_has_no_tools_entry_at_all(db):
    row = _row_shape(VIEWER)
    assert ("group", "explore") in row
    assert not [e for e in row if e[1] in ("tools", "intake", "shelf-fill")]


def test_without_vision_tools_is_shelf_fill_as_a_plain_tab(db):
    row = _row_shape(ADMIN)
    assert ("tab", "shelf-fill") in row
    assert not [e for e in row if e == ("group", "tools")]


def test_disabled_shelf_fill_leaves_intake_as_a_plain_tab(db):
    _set(db, "vision_provider", "ollama")
    assert ("group", "tools") in _row_shape(ADMIN)
    _set_feature("shelf_fill", False)
    row = _row_shape(ADMIN)
    assert ("tab", "intake") in row
    assert ("group", "tools") not in row


def test_hiding_three_explore_members_leaves_the_last_as_a_plain_tab(db):
    _set(db, "nav_hidden_tabs", json.dumps(["series", "music", "periodicals"]))
    row = _row_shape(ADMIN)
    assert ("tab", "stats") in row
    assert ("group", "explore") not in row
