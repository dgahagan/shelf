"""Settings → Features: the toggle list, its warnings and its write route."""

import html
import re

import pytest

from app.features import FEATURES, feature_enabled
from tests.conftest import _insert_item
from tests.test_features_gate import off


def _row(page, key):
    """The markup of one feature's card."""
    m = re.search(
        rf'<div data-testid="feature-row-{key}".*?</form>', page, re.S
    )
    assert m, f"no row for {key}"
    return m.group(0)


def _confirm(page, key):
    m = re.search(r'data-confirm="([^"]*)"', _row(page, key))
    return html.unescape(m.group(1)) if m else None


def _borrower(db, name):
    return db.execute("INSERT INTO borrowers (name) VALUES (?)", (name,)).lastrowid


def _loan(db, item_id, borrower_id, checked_in=None):
    db.execute(
        "INSERT INTO checkouts (item_id, borrower_id, checked_in) VALUES (?, ?, ?)",
        (item_id, borrower_id, checked_in),
    )


def _share(db, token):
    db.execute("INSERT INTO share_links (token, scope) VALUES (?, 'collection')", (token,))


def test_lists_every_feature_on_by_default(admin_client):
    page = admin_client.get("/settings").text
    assert 'data-testid="tab-features"' in page
    for key in FEATURES:
        row = _row(page, key)
        assert 'data-feature-state="on"' in row
        assert 'value="0"' in row and "Turn off" in row


def test_turn_off_and_back_on(admin_client):
    resp = admin_client.post("/api/settings/features/stats", data={"enabled": "0"},
                             follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/settings"
    assert feature_enabled("stats") is False
    row = _row(admin_client.get("/settings").text, "stats")
    assert 'data-feature-state="off"' in row and "Turn on" in row
    assert "data-confirm" not in row  # turning on never asks

    admin_client.post("/api/settings/features/stats", data={"enabled": "1"})
    assert feature_enabled("stats") is True


def test_editor_gets_role_refusal(editor_client):
    resp = editor_client.post("/api/settings/features/stats", data={"enabled": "0"},
                              follow_redirects=False)
    assert resp.status_code == 403
    assert feature_enabled("stats") is True


def test_unknown_key_is_404(admin_client):
    resp = admin_client.post("/api/settings/features/nope", data={"enabled": "0"})
    assert resp.status_code == 404


def test_bad_value_changes_nothing(admin_client):
    resp = admin_client.post("/api/settings/features/stats", data={"enabled": "off"})
    assert resp.status_code == 400
    assert feature_enabled("stats") is True


def test_share_warning_counts_links(admin_client, db):
    _share(db, "a")
    _share(db, "b")
    db.commit()
    msg = _confirm(admin_client.get("/settings").text, "share")
    assert "2 active share links stop working" in msg
    assert msg.endswith("Turn it off anyway?")


def test_single_share_link_is_singular(admin_client, db):
    _share(db, "a")
    db.commit()
    assert "1 active share link stops working" in _confirm(admin_client.get("/settings").text, "share")


def test_lending_warning_counts_live_open_loans(admin_client, db):
    a, b = _borrower(db, "Ann"), _borrower(db, "Bob")
    i1 = _insert_item(db, title="One", isbn="9780000000019")
    i2 = _insert_item(db, title="Two", isbn="9780000000026")
    i3 = _insert_item(db, title="Three", isbn="9780000000033",
                      deleted_at="2026-01-01 00:00:00")
    i4 = _insert_item(db, title="Four", isbn="9780000000040")
    _loan(db, i1, a)
    _loan(db, i2, b)
    _loan(db, i3, b)  # on a trashed item: does not count
    _loan(db, i4, a, checked_in="2026-01-02 00:00:00")  # returned: does not count
    db.commit()
    msg = _confirm(admin_client.get("/settings").text, "lending")
    assert "2 open loans to 2 borrowers stay recorded" in msg
    assert "overdue reminders pause" in msg


def test_zero_probes_mean_no_confirm(admin_client):
    page = admin_client.get("/settings").text
    for key in ("stats", "share", "lending"):
        assert _confirm(page, key) is None


def test_data_survives_disable_and_enable(admin_client, db):
    a = _borrower(db, "Ann")
    _loan(db, _insert_item(db), a)
    _share(db, "tok")
    db.commit()

    def counts():
        return [db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("checkouts", "borrowers", "share_links")]

    before = counts()
    for key in ("lending", "share"):
        admin_client.post(f"/api/settings/features/{key}", data={"enabled": "0"})
    assert counts() == before
    for key in ("lending", "share"):
        admin_client.post(f"/api/settings/features/{key}", data={"enabled": "1"})
    assert counts() == before


def _needs_setup(page, key):
    return "data-feature-needs-setup" in _row(page, key)


def test_hardcover_needs_setup_without_a_token(admin_client):
    assert _needs_setup(admin_client.get("/settings").text, "hardcover")


def test_hardcover_configured_by_env_only_token(admin_client, monkeypatch):
    monkeypatch.setenv("HARDCOVER_TOKEN", "env-only")
    assert not _needs_setup(admin_client.get("/settings").text, "hardcover")


def test_features_without_setup_never_say_needs_setup(admin_client):
    page = admin_client.get("/settings").text
    for key, f in FEATURES.items():
        if f.configured is None:
            assert not _needs_setup(page, key), key


def test_disabled_feature_can_be_turned_on_from_settings(admin_client):
    off("series")
    row = _row(admin_client.get("/settings").text, "series")
    assert 'value="1"' in row and "Turn on" in row


# --- Profiles row ---

_STANDARD = {"lending", "series", "stats", "store", "music", "periodicals", "shelf_fill"}


def _profiles_block(page):
    m = re.search(r'<div data-testid="feature-profiles".*?<div class="divide-y', page, re.S)
    assert m, "no Profiles block"
    return m.group(0)


def _current(page):
    return re.search(r'data-profile-current="([^"]*)"', _profiles_block(page)).group(1)


def _profile_form(page, name):
    m = re.search(rf'<form[^>]*data-testid="profile-form-{name}"[^>]*>.*?</form>',
                  _profiles_block(page), re.S)
    assert m, f"no profile form for {name}"
    return m.group(0)


def _profile_confirm(page, name):
    opening = re.match(r"<form[^>]*>", _profile_form(page, name)).group(0)
    m = re.search(r'data-confirm="([^"]*)"', opening)
    return html.unescape(m.group(1)) if m else None


def _feature_rows():
    from app.database import get_db
    with get_db() as conn:
        return dict(conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'feature.%'"
        ).fetchall())


def _expected(profile):
    on = {"minimal": set(), "standard": _STANDARD, "everything": set(FEATURES)}[profile]
    return {f"feature.{k}": "1" if k in on else "0" for k in FEATURES}


def test_fresh_install_reads_everything(admin_client):
    page = admin_client.get("/settings").text
    assert _current(page) == "everything"
    button = re.search(r'<button[^>]*data-testid="profile-apply-everything"[^>]*>',
                       _profile_form(page, "everything")).group(0)
    assert "disabled" in button and 'aria-current="true"' in button
    assert 'data-testid="profile-custom"' not in page


def test_one_toggle_reads_custom(admin_client):
    off("share")
    page = admin_client.get("/settings").text
    assert _current(page) == "custom"
    assert 'data-testid="profile-custom"' in page
    assert 'aria-current="true"' not in _profiles_block(page)


def test_applied_profile_is_marked(admin_client):
    admin_client.post("/api/settings/profile", data={"profile": "standard"})
    assert _current(admin_client.get("/settings").text) == "standard"


def test_zero_probes_mean_no_profile_confirm(admin_client):
    page = admin_client.get("/settings").text
    for name in ("minimal", "standard", "everything"):
        assert _profile_confirm(page, name) is None


def test_share_links_warn_on_profiles_that_turn_share_off(admin_client, db):
    _share(db, "a")
    _share(db, "b")
    db.commit()
    page = admin_client.get("/settings").text
    for name, label in (("minimal", "Minimal"), ("standard", "Standard")):
        msg = _profile_confirm(page, name)
        assert "2 active share links stop working" in msg
        assert msg.endswith(f"Apply {label} anyway?")
    assert _profile_confirm(page, "everything") is None


def test_open_loans_warn_only_where_lending_goes_off(admin_client, db):
    a = _borrower(db, "Ann")
    _loan(db, _insert_item(db, title="One", isbn="9780000000019"), a)
    db.commit()
    page = admin_client.get("/settings").text
    assert "open loan" in _profile_confirm(page, "minimal")
    assert _profile_confirm(page, "standard") is None  # Lending is in Standard


def test_turning_features_on_never_asks(admin_client, db):
    _share(db, "a")
    db.commit()
    admin_client.post("/api/settings/profile", data={"profile": "minimal"})
    page = admin_client.get("/settings").text
    assert _current(page) == "minimal"
    for name in ("minimal", "standard", "everything"):
        assert _profile_confirm(page, name) is None


def test_profile_route_anonymous_redirects_to_login(client, admin_user):
    resp = client.post("/api/settings/profile", data={"profile": "minimal"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    assert _feature_rows() == {}


@pytest.mark.parametrize("fixture", ["editor_client", "viewer_client"])
def test_profile_route_refuses_non_admins(request, fixture):
    c = request.getfixturevalue(fixture)
    resp = c.post("/api/settings/profile", data={"profile": "minimal"},
                  follow_redirects=False)
    assert resp.status_code == 403
    assert _feature_rows() == {}
    assert all(feature_enabled(k) for k in FEATURES)


def test_profile_route_rejects_unknown_profile(admin_client):
    resp = admin_client.post("/api/settings/profile", data={"profile": "nope"},
                             follow_redirects=False)
    assert resp.status_code == 400
    assert _feature_rows() == {}


@pytest.mark.parametrize("profile", ["minimal", "standard", "everything"])
def test_profile_route_applies_each_profile(admin_client, profile):
    resp = admin_client.post("/api/settings/profile", data={"profile": profile},
                             follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings"
    assert _feature_rows() == _expected(profile)


def test_route_order_reaches_the_profile_handler(client):
    """G119: the first full match for the path must be set_profile."""
    from starlette.routing import Match

    from app.main import app

    scope = {"type": "http", "method": "POST", "path": "/api/settings/profile",
             "root_path": ""}
    first = next(r for r in app.router.routes
                 if hasattr(r, "matches") and r.matches(scope)[0] == Match.FULL)
    assert f"{first.endpoint.__module__}.{first.endpoint.__name__}" == \
        "app.routers.settings.set_profile"


def _visible(page, name):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", _profile_form(page, name))).split())


def test_profile_changes_are_visible_text_not_a_tooltip(admin_client):
    """Diff review M1: each profile's description and what applying it
    changes render as text a touch user can read, outside any attribute."""
    block = _profiles_block(admin_client.get("/settings").text)
    visible = html.unescape(re.sub(r"<[^>]+>", " ", block))
    assert "Scan, catalogue and browse. Nothing optional." in visible
    assert "title=" not in block


def test_profile_cards_name_what_applying_turns_off(admin_client):
    """On an all-on install, a smaller profile names what it turns off —
    never setup's "Adds:", which is relative to the next-smaller profile."""
    page = admin_client.get("/settings").text
    assert "Adds:" not in _profiles_block(page)
    assert "Turns off: Sharing, Valuation, Price alerts, Buy links, Photo Intake, Hardcover, " \
        "Audiobookshelf sync, Komga, RomM" in _visible(page, "standard")
    assert "Turns on:" not in _visible(page, "standard")
    assert "Turns off: Lending, Series" in _visible(page, "minimal")
    everything = _visible(page, "everything")
    assert "Turns on:" not in everything and "Turns off:" not in everything


def test_profile_cards_name_what_applying_turns_on(admin_client):
    admin_client.post("/api/settings/profile", data={"profile": "minimal"},
                      follow_redirects=False)
    page = admin_client.get("/settings").text
    assert "Turns on: Lending, Series, Statistics, Store Mode, Music, " \
        "Periodicals, Shelf Fill" in _visible(page, "standard")
    assert "Turns off:" not in _visible(page, "standard")
    minimal = _visible(page, "minimal")
    assert "Turns on:" not in minimal and "Turns off:" not in minimal


def test_profile_card_from_custom_names_both_directions(admin_client):
    off("series")
    page = admin_client.get("/settings").text
    standard = _visible(page, "standard")
    assert "Turns on: Series" in standard
    assert "Turns off: Sharing" in standard


def test_price_alerts_row_says_needs_setup_without_a_key(admin_client, monkeypatch):
    monkeypatch.delenv("ISBNDB_API_KEY", raising=False)
    page = admin_client.get("/settings").text
    assert 'data-testid="feature-row-price_alerts"' in page
    row = _row(page, "price_alerts")
    assert "data-feature-needs-setup" in row
