"""Currency registry, cache invalidation, and money formatting."""

from app.currency import CURRENCIES, format_money, get_currency, invalidate_cache


def _set(db, key, value):
    """Write a setting straight to the test DB and drop the currency cache."""
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = ?",
        (key, value, value),
    )
    db.commit()
    invalidate_cache()


def test_usd_is_the_default_when_no_setting_is_stored(db):
    assert get_currency().code == "USD"
    assert format_money(1234.56) == "$1,234.56"


def test_eur_renders_tight_prefix(db):
    _set(db, "currency", "EUR")
    assert format_money(1234.56) == "€1,234.56"


def test_sek_renders_suffix_with_a_space(db):
    _set(db, "currency", "SEK")
    assert format_money(1234.56) == "1,234.56 kr"


def test_jpy_is_zero_decimal_and_rounds(db):
    _set(db, "currency", "JPY")
    assert format_money(1234.56) == "¥1,235"


def test_explicit_decimals_overrides_the_currency_default(db):
    _set(db, "currency", "USD")
    assert format_money(1234.56, decimals=0) == "$1,235"


def test_thousands_grouping_is_always_applied(db):
    _set(db, "currency", "USD")
    assert format_money(1234567.891) == "$1,234,567.89"


def test_unknown_stored_currency_code_falls_back_to_usd(db):
    _set(db, "currency", "XYZ")
    assert get_currency().code == "USD"
    assert format_money(1234.56) == "$1,234.56"


def test_cache_invalidation_picks_up_a_new_setting(db):
    assert get_currency().code == "USD"
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = ?",
        ("currency", "GBP", "GBP"),
    )
    db.commit()
    # Cache is still warm with USD until we invalidate.
    assert get_currency().code == "USD"
    invalidate_cache()
    assert get_currency().code == "GBP"


def test_registry_has_exactly_the_twenty_expected_codes():
    assert set(CURRENCIES) == {
        "USD", "EUR", "GBP", "CAD", "AUD", "NZD", "CHF", "JPY", "CNY",
        "KRW", "INR", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "BRL",
        "MXN", "ZAR",
    }


def test_suffix_currencies_are_exactly_the_expected_four():
    suffix_codes = {code for code, c in CURRENCIES.items() if c.suffix}
    assert suffix_codes == {"SEK", "NOK", "DKK", "PLN", "CZK", "HUF"}


def test_zero_decimal_currencies_are_exactly_the_expected_three():
    zero_dec_codes = {code for code, c in CURRENCIES.items() if c.decimals == 0}
    assert zero_dec_codes == {"JPY", "KRW", "HUF"}


# --- POST /api/settings/display ---------------------------------------------

def test_valid_code_persists_and_a_later_render_carries_the_new_symbol(admin_client, db):
    r = admin_client.post(
        "/api/settings/display", data={"currency": "GBP"}, follow_redirects=False
    )
    assert r.status_code == 303
    row = db.execute("SELECT value FROM settings WHERE key = 'currency'").fetchone()
    assert row["value"] == "GBP"

    # The route must have dropped the cache: no manual invalidate_cache()
    # here, so a stale cached Currency would still report USD.
    assert get_currency().code == "GBP"
    assert format_money(1234.56) == "£1,234.56"

    # ...and the reloaded page pre-selects it in the dropdown.
    html = admin_client.get("/settings").text
    assert '<option value="GBP" selected>GBP — British Pound (£)</option>' in html


def test_unknown_code_writes_nothing(admin_client, db):
    r = admin_client.post(
        "/api/settings/display", data={"currency": "XYZ"}, follow_redirects=False
    )
    assert r.status_code == 303
    row = db.execute("SELECT value FROM settings WHERE key = 'currency'").fetchone()
    assert row is None


def test_editor_cannot_post_display_settings(editor_client):
    r = editor_client.post(
        "/api/settings/display", data={"currency": "GBP"}, follow_redirects=False
    )
    assert r.status_code == 403


def test_viewer_cannot_post_display_settings(viewer_client):
    r = viewer_client.post(
        "/api/settings/display", data={"currency": "GBP"}, follow_redirects=False
    )
    assert r.status_code == 403
