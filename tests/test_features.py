"""Feature registry: flag resolution, the flag cache and registry consistency."""

import pytest

import app.features as features
from app.features import (
    CORE_NAV_TABS,
    FEATURES,
    PROFILES,
    Feature,
    apply_profile,
    current_profile,
    feature_enabled,
    profile_choices,
    profile_keys,
    set_feature_enabled,
)
from tests.test_features_gate import off


def _raw(db, key, value):
    """Write a flag row directly, bypassing the write path, and drop the cache."""
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = ?",
        (key, value, value),
    )
    db.commit()
    features.invalidate_cache()


def test_absent_row_is_enabled():
    assert all(feature_enabled(k) for k in FEATURES)


@pytest.mark.parametrize("value", ["1", "", "yes", "false", "00", " 0"])
def test_any_value_but_exact_zero_is_enabled(db, value):
    _raw(db, "feature.series", value)
    assert feature_enabled("series") is True


def test_exact_zero_disables(db):
    _raw(db, "feature.series", "0")
    assert feature_enabled("series") is False
    assert feature_enabled("stats") is True


def test_unknown_key_raises():
    with pytest.raises(KeyError):
        feature_enabled("sereis")


def test_set_unknown_key_raises(db):
    with pytest.raises(KeyError):
        set_feature_enabled(db, "nope", False)


def test_env_var_does_not_override(monkeypatch):
    for name in ("FEATURE_SERIES", "SHELF_FEATURE_SERIES", "feature.series"):
        monkeypatch.setenv(name, "0")
    features.invalidate_cache()
    assert feature_enabled("series") is True


def test_cache_holds_between_reads(db):
    assert feature_enabled("series") is True
    # A raw write without invalidation is not seen: the cache answers.
    db.execute("INSERT INTO settings (key, value) VALUES ('feature.series', '0')")
    db.commit()
    assert feature_enabled("series") is True
    features.invalidate_cache()
    assert feature_enabled("series") is False


def test_set_feature_enabled_round_trip(db):
    set_feature_enabled(db, "series", False)
    db.commit()
    assert feature_enabled("series") is False
    set_feature_enabled(db, "series", True)
    db.commit()
    assert feature_enabled("series") is True
    row = db.execute("SELECT value FROM settings WHERE key = 'feature.series'").fetchone()
    assert row["value"] == "1"


def test_set_feature_enabled_drops_both_caches(db):
    import app.nav as nav
    feature_enabled("series")
    nav._nav_settings()
    assert features._cached_flags is not None
    assert nav._cached_settings is not None
    set_feature_enabled(db, "series", False)
    assert features._cached_flags is None
    assert nav._cached_settings is None


def test_refill_before_commit_is_dropped_at_commit():
    """R1: another connection refilling between the write and its commit
    caches the pre-commit value; the after-commit invalidation drops it."""
    from app.database import get_db
    with get_db() as writer:
        set_feature_enabled(writer, "series", False)
        # Another request reads now: it cannot see the uncommitted row.
        assert feature_enabled("series") is True
        assert features._cached_flags is not None
    # The commit has landed and invalidated the stale refill.
    assert features._cached_flags is None
    assert feature_enabled("series") is False


def test_rollback_keeps_the_old_value():
    from app.database import get_db
    with pytest.raises(RuntimeError):
        with get_db() as writer:
            set_feature_enabled(writer, "series", False)
            raise RuntimeError
    assert feature_enabled("series") is True


def test_invalidation_during_refill_is_not_cached(db, monkeypatch):
    """R1: a refill that straddles an invalidation returns its snapshot for
    that call but does not store it."""
    import app.database as database
    real = database.get_setting
    fired = []

    def racing_get_setting(conn, key):
        if not fired:
            fired.append(key)
            features.invalidate_cache()
        return real(conn, key)

    monkeypatch.setattr(database, "get_setting", racing_get_setting)
    assert feature_enabled("series") is True
    assert fired
    assert features._cached_flags is None
    # With no race, the next read caches.
    monkeypatch.setattr(database, "get_setting", real)
    feature_enabled("series")
    assert features._cached_flags is not None


def test_unreadable_db_reads_enabled_and_is_not_cached(monkeypatch):
    import app.database as database

    def broken():
        raise OSError("disk gone")

    monkeypatch.setattr(database, "get_db", broken)
    assert feature_enabled("lending") is True
    assert features._cached_flags is None


def test_registry_nav_tabs_are_real_tabs():
    from app.nav import NAV_TABS
    keys = {t["key"] for t in NAV_TABS}
    for key, f in FEATURES.items():
        for tab in f.nav_tabs:
            assert tab in keys, f"FEATURES[{key!r}].nav_tabs names unknown tab {tab!r}"
    assert CORE_NAV_TABS <= keys


def test_registry_entry_paths_are_local():
    for key, f in FEATURES.items():
        assert f.entry_path.startswith("/") and not f.entry_path.startswith("//"), key


def test_registry_has_the_sixteen_designed_features():
    assert list(FEATURES) == [
        "lending", "series", "stats", "store", "share", "valuation", "price_alerts", "music",
        "periodicals", "shelf_fill", "intake", "hardcover", "abs_sync", "komga", "romm",
        "collector",
    ]


# --- Profiles ----------------------------------------------------------------

_STANDARD_KEYS = frozenset({
    "lending", "series", "stats", "store", "music", "periodicals", "shelf_fill",
})


def test_every_feature_profile_is_standard_or_everything():
    for key, f in FEATURES.items():
        assert f.profile in ("standard", "everything"), f"FEATURES[{key!r}].profile is {f.profile!r}"


def test_profile_keys_minimal_is_empty():
    assert profile_keys("minimal") == frozenset()


def test_profile_keys_standard_is_a_strict_subset_of_everything():
    assert profile_keys("standard") < profile_keys("everything")


def test_profile_keys_everything_is_every_feature():
    assert profile_keys("everything") == set(FEATURES)


def test_profile_keys_standard_is_the_designed_seven():
    assert profile_keys("standard") == _STANDARD_KEYS


def test_profile_keys_unknown_name_raises():
    with pytest.raises(KeyError):
        profile_keys("nope")


def test_feature_profile_has_no_default():
    with pytest.raises(TypeError):
        Feature(label="x", description="y")


def _profile_rows(db):
    rows = db.execute("SELECT key, value FROM settings WHERE key LIKE 'feature.%'").fetchall()
    return {row["key"]: row["value"] for row in rows}


@pytest.mark.parametrize("name", ["minimal", "standard", "everything"])
def test_apply_profile_writes_all_sixteen_rows(db, name):
    apply_profile(db, name)
    db.commit()
    rows = _profile_rows(db)
    keys = profile_keys(name)
    assert set(rows) == {f"feature.{k}" for k in FEATURES}
    for key in FEATURES:
        expected = "1" if key in keys else "0"
        assert rows[f"feature.{key}"] == expected, key


def test_apply_profile_unknown_name_raises_and_writes_nothing(db):
    with pytest.raises(KeyError):
        apply_profile(db, "nope")
    db.commit()
    assert _profile_rows(db) == {}


@pytest.mark.parametrize("name", ["minimal", "standard", "everything"])
def test_current_profile_after_apply(db, name):
    apply_profile(db, name)
    db.commit()
    assert current_profile() == name


def test_current_profile_is_none_after_a_manual_toggle(db):
    apply_profile(db, "standard")
    db.commit()
    set_feature_enabled(db, "series", False)
    db.commit()
    assert current_profile() is None


def test_current_profile_is_everything_on_a_fresh_db():
    assert current_profile() == "everything"


def test_apply_profile_refill_before_commit_is_dropped_at_commit():
    """G124 pin, modelled on test_refill_before_commit_is_dropped_at_commit:
    another connection refilling between the write and its commit caches the
    pre-commit state; the after-commit invalidation drops it."""
    from app.database import get_db
    with get_db() as writer:
        apply_profile(writer, "minimal")
        # Another request reads now: it cannot see the uncommitted rows.
        assert feature_enabled("series") is True
        assert features._cached_flags is not None
    assert features._cached_flags is None
    assert feature_enabled("series") is False


def test_profile_choices_order_and_minimal_adds_nothing():
    choices = profile_choices()
    assert [c["name"] for c in choices] == ["minimal", "standard", "everything"]
    assert choices[0]["adds"] == []


def test_profile_choices_adds_cover_every_feature_label_exactly_once():
    choices = profile_choices()
    all_adds = [label for c in choices for label in c["adds"]]
    assert sorted(all_adds) == sorted(f.label for f in FEATURES.values())
    assert len(all_adds) == len(set(all_adds))


# --- feature_on template global, disabled_scan_modes, client names --------


def test_feature_on_global_is_feature_enabled():
    from app.main import templates
    assert templates.env.globals["feature_on"] is feature_enabled


def test_feature_on_unknown_key_raises():
    from app.main import templates
    with pytest.raises(KeyError):
        templates.env.from_string("{{ feature_on('nope') }}").render()


def test_disabled_scan_modes_with_lending_off():
    off("lending")
    assert features.disabled_scan_modes() == "lend return"


def test_disabled_scan_modes_with_everything_on():
    assert features.disabled_scan_modes() == ""


def test_registry_client_entries_are_nonempty_and_whitespace_free():
    checked = 0
    for key, f in FEATURES.items():
        for name in f.client:
            checked += 1
            assert name, f"FEATURES[{key!r}].client has an empty entry"
            assert not any(c.isspace() for c in name), (
                f"FEATURES[{key!r}].client entry {name!r} has whitespace"
            )
    assert checked >= 3


# --- The registry lint ------------------------------------------------------
#
# Same shape as tests/test_nav.py's route census: walk every route the app
# serves and make each one classified. A new router, route or nav tab nobody
# classified fails here rather than shipping ungated by accident.


def _flat_calls(dependant):
    """Every dependency callable, in FastAPI's resolution order: router list,
    then decorator list, then signature (depth-first)."""
    calls = []
    for dep in dependant.dependencies:
        calls.append(dep.call)
        calls.extend(_flat_calls(dep))
    return calls


def _route_census():
    """-> [(module, method, path, [feature_key...], [call...])] per method."""
    from app.main import app
    rows = []
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        dependant = getattr(route, "dependant", None)
        if endpoint is None or dependant is None:
            continue  # static mounts, websocket-less plumbing
        calls = _flat_calls(dependant)
        keys = [c.feature_key for c in calls if hasattr(c, "feature_key")]
        for method in sorted(route.methods or ()):
            if method == "HEAD":
                continue
            rows.append((endpoint.__module__, method, route.path, keys, calls))
    return rows


def _owner_by_module():
    owners = {}
    for key, f in FEATURES.items():
        for module in f.modules:
            assert module not in owners, f"{module} is claimed by {owners[module]!r} and {key!r}"
            owners[module] = key
    return owners


def _where(key):
    return (f"add it to FEATURES[{key!r}].ungated with a reason, or gate it with "
            f"Depends(require_feature({key!r})) after its require_role")


def test_every_route_in_a_feature_module_is_gated_or_exempt():
    owners = _owner_by_module()
    problems = []
    for module, method, path, keys, _ in _route_census():
        key = owners.get(module)  # dotted names, never basenames (G88)
        if key is None:
            continue
        f = FEATURES[key]
        exempt = (method, path) in f.ungated or (method, path) in f.inline
        if exempt and key in keys:
            problems.append(f"{method} {path} is gated by {key!r} but listed as exempt")
        elif not exempt and key not in keys:
            problems.append(f"{method} {path} ({module}) is ungated: {_where(key)}")
    assert not problems, "\n".join(problems)


def test_every_listed_core_route_is_gated():
    census = {(m, p): keys for _, m, p, keys, _ in _route_census()}
    problems = []
    for key, f in FEATURES.items():
        for route in f.routes:
            if route not in census:
                problems.append(f"FEATURES[{key!r}].routes names {route}, which no route serves")
            elif key not in census[route]:
                problems.append(f"{route} is in FEATURES[{key!r}].routes but not gated by it")
    assert not problems, "\n".join(problems)


def test_no_other_route_carries_a_feature_gate():
    owners = _owner_by_module()
    listed = {(route, key) for key, f in FEATURES.items() for route in f.routes}
    problems = []
    for module, method, path, keys, _ in _route_census():
        for key in keys:
            if owners.get(module) == key or ((method, path), key) in listed:
                continue
            problems.append(
                f"{method} {path} ({module}) is gated by {key!r} without being classified: "
                f"add {module!r} to FEATURES[{key!r}].modules or the route to its routes"
            )
    assert not problems, "\n".join(problems)


def test_every_exemption_names_a_real_route():
    census = {(m, p) for _, m, p, _, _ in _route_census()}
    stale = [
        f"FEATURES[{key!r}].{kind} names {route}, which no route serves"
        for key, f in FEATURES.items()
        for kind, table in (("ungated", f.ungated), ("inline", f.inline))
        for route in table
        if route not in census
    ]
    assert not stale, "\n".join(stale)


def test_every_exemption_says_why():
    for key, f in FEATURES.items():
        for route, reason in {**f.ungated, **f.inline}.items():
            assert reason.strip(), f"FEATURES[{key!r}] exempts {route} without a reason"


def test_every_nav_tab_is_classified_exactly_once():
    from app.nav import NAV_TABS
    owners = {}
    for key, f in FEATURES.items():
        for tab in f.nav_tabs:
            owners.setdefault(tab, []).append(key)
    problems = []
    for tab in (t["key"] for t in NAV_TABS):
        claims = owners.get(tab, []) + (["CORE_NAV_TABS"] if tab in CORE_NAV_TABS else [])
        if len(claims) != 1:
            problems.append(
                f"nav tab {tab!r} is claimed by {claims or 'nobody'}: put it in exactly one "
                f"feature's nav_tabs or in CORE_NAV_TABS"
            )
    assert not problems, "\n".join(problems)


def test_role_is_answered_before_feature():
    """The role refusal comes first, so the disabled page never tells a user
    what a route they may not use is for."""
    problems = []
    for module, method, path, keys, calls in _route_census():
        if not keys:
            continue
        roles = [i for i, c in enumerate(calls)
                 if getattr(c, "__qualname__", "").startswith("require_role.")]
        gates = [i for i, c in enumerate(calls) if hasattr(c, "feature_key")]
        if roles and min(gates) < max(roles):
            problems.append(f"{method} {path} ({module}) resolves its feature gate before its role check")
    assert not problems, "\n".join(problems)


# --- price_alerts ------------------------------------------------------------

def test_price_alerts_is_everything_only():
    assert FEATURES["price_alerts"].profile == "everything"
    assert "price_alerts" in profile_keys("everything")
    assert "price_alerts" not in profile_keys("standard")
    assert profile_keys("standard") == _STANDARD_KEYS


def test_collector_is_everything_only():
    assert FEATURES["collector"].profile == "everything"
    assert "collector" in profile_keys("everything")
    assert "collector" not in profile_keys("standard")
    assert profile_keys("standard") == _STANDARD_KEYS


def _price_alerts_row():
    return next(r for r in features.feature_rows() if r["key"] == "price_alerts")


def test_price_alerts_needs_an_isbndb_key(monkeypatch):
    monkeypatch.delenv("ISBNDB_API_KEY", raising=False)
    assert _price_alerts_row()["configured"] is False


def test_price_alerts_configured_by_env_only_key(monkeypatch):
    monkeypatch.setenv("ISBNDB_API_KEY", "env-only-isbndb-key")
    assert _price_alerts_row()["configured"] is True
