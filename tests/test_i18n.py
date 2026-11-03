"""UI translation: locale resolution, per-user choice, and the catalogs."""

import subprocess
import sys
from pathlib import Path

import pytest

from app import features, i18n
from app.services import api_tokens
from tests.conftest import _insert_item

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("user_language, header, expected", [
    (None, None, "en"),
    (None, "", "en"),
    (None, "it-IT,it;q=0.9,en;q=0.8", "it"),
    (None, "en-US,en;q=0.9,it;q=0.8", "en"),
    (None, "fr-FR,fr;q=0.9,it;q=0.5", "it"),      # unsupported tags are skipped
    (None, "it;q=0.4,en;q=0.9", "en"),            # q-value beats position
    (None, "it;q=0", "en"),                       # q=0 means "not this one"
    (None, "it;level=1;q=0", "en"),               # q is read wherever it sits
    (None, "de, *", "de"),
    (None, "nl, *", "en"),                        # an unsupported tag plus a wildcard falls back
    ("it", "en-US,en;q=0.9", "it"),               # the user's choice beats the browser
    ("xx", "it", "it"),                           # an unknown stored value falls through
    ("zh", None, "zh_Hans"),                      # a stored bare code still resolves
    (None, "zh-CN", "zh_Hans"),                   # region aliased to a script
    (None, "zh-Hans-CN", "zh_Hans"),              # script and region together
    (None, "zh-TW,zh;q=0.9", "zh_Hans"),          # only variant shipped: Traditional falls back to it
    (None, "zh", "zh_Hans"),                      # bare language finds its only variant
    (None, "IT_ch", "it"),                        # casing and underscores, unsupported region
])
def test_resolve(user_language, header, expected):
    assert i18n.resolve(user_language, header) == expected


@pytest.mark.parametrize("tag, expected", [
    ("pt-BR", "pt_BR"), ("pt_br", "pt_BR"), ("PT-br", "pt_BR"),
    ("pt-PT", "pt_PT"), ("pt", None), ("pt-AO", None),
    ("zh-Hant", "zh_Hant"), ("zh-TW", "zh_Hant"), ("zh-HK", "zh_Hant"), ("zh-Hant-TW", "zh_Hant"),
    ("zh-CN", "zh_Hans"), ("zh-SG", "zh_Hans"), ("zh", None),
    ("es-419", "es_419"), ("", None), (None, None),
])
def test_match_prefers_the_most_specific_variant(monkeypatch, tag, expected):
    # Two variants of each, so a bare language is ambiguous and must not match.
    monkeypatch.setattr(i18n, "SUPPORTED", ("en", "pt_BR", "pt_PT", "zh_Hans", "zh_Hant", "es_419", "es_ES"))
    assert i18n.match(tag) == expected


def test_html_lang_is_bcp47(client, admin_user):
    assert '<html lang="zh-Hans"' in client.get("/login", headers={"Accept-Language": "zh-CN"}).text


def test_store_root_lang_is_bcp47(admin_client):
    page = admin_client.get("/store", headers={"Accept-Language": "zh-CN"})
    assert '<html lang="zh-Hans"' in page.text


def test_public_share_page_root_lang_follows_its_text(admin_client, client):
    """The share page skips Auth but follows the browser: its root lang must
    name the language its text is in, not a hard-coded English."""
    resp = admin_client.post("/api/share", data={"scope": "wishlist", "label": "x"},
                             follow_redirects=False)
    assert resp.status_code == 303
    from app.database import get_db
    with get_db() as db:
        token = db.execute("SELECT token FROM share_links ORDER BY id DESC LIMIT 1").fetchone()[0]
    client.cookies.clear()
    page = client.get(f"/share/{token}", headers={"Accept-Language": "it"})
    assert page.status_code == 200
    assert '<html lang="it"' in page.text
    assert "Ancora niente qui." in page.text


def test_gettext_follows_the_locale():
    token = i18n.current_locale.set("it")
    try:
        assert i18n.gettext("Sign out") == "Esci"
        assert i18n.js_catalog()["Done"] == "Fatto"
    finally:
        i18n.current_locale.reset(token)
    assert i18n.gettext("Sign out") == "Sign out"
    assert i18n.js_catalog() == {}


def test_login_page_follows_the_browser(client, admin_user):
    it = client.get("/login", headers={"Accept-Language": "it-IT,it;q=0.9"})
    assert '<html lang="it"' in it.text
    assert "Accedi alla tua biblioteca" in it.text

    en = client.get("/login")
    assert '<html lang="en"' in en.text
    assert "Sign in to your library" in en.text


def test_route_message_is_translated(client, admin_user):
    """The locale set in AuthMiddleware reaches the endpoint (a contextvar
    across BaseHTTPMiddleware's call_next), not only the template."""
    r = client.post("/login", data={"username": "nobody", "password": "x"},
                    headers={"Accept-Language": "it"})
    assert r.status_code == 401
    assert "Nome utente o password non validi" in r.text


def test_user_choice_beats_the_browser(admin_client, db):
    r = admin_client.post("/api/account/language", data={"language": "it"})
    assert r.json() == {"ok": True}
    assert db.execute("SELECT language FROM users WHERE username = 'admin'").fetchone()[0] == "it"

    page = admin_client.get("/browse", headers={"Accept-Language": "en-US"})
    assert '<html lang="it"' in page.text
    assert "Sfoglia" in page.text
    assert '"Done": "Fatto"' in page.text

    # Back to automatic: the browser decides again.
    admin_client.post("/api/account/language", data={"language": ""})
    page = admin_client.get("/browse", headers={"Accept-Language": "en-US"})
    assert '<html lang="en"' in page.text


def test_viewer_can_pick_their_own_language(viewer_client):
    assert viewer_client.post("/api/account/language", data={"language": "it"}).json()["ok"]


def test_unknown_language_is_refused(admin_client, db):
    r = admin_client.post("/api/account/language", data={"language": "xx"})
    assert r.json()["ok"] is False
    assert db.execute("SELECT language FROM users WHERE username = 'admin'").fetchone()[0] is None


def test_api_tokens_tab_and_its_errors_are_translated(admin_client):
    admin_client.post("/api/account/language", data={"language": "it"})

    page = admin_client.get("/browse").text
    assert "Token API" in page
    assert '"Revoke token {name}?": "Revocare il token {name}?"' in page
    long_name = "x" * (api_tokens.MAX_NAME + 1)
    assert admin_client.post("/api/tokens", data={"name": long_name}).json()["message"] == (
        f"Il nome può avere al massimo {api_tokens.MAX_NAME} caratteri")


def test_browse_is_translated(admin_client, db):
    _insert_item(db, title="Prestato", isbn="9780000000033", media_type="dvd", source="csv_import")
    db.commit()
    admin_client.post("/api/account/language", data={"language": "it"})

    page = admin_client.get("/browse?view=list").text
    for text in ("Collezione", "Tutti i tipi", "Più recenti", "Da iniziare", "Importazione CSV"):
        assert text in page, text
    assert '"prefix": "Tipo"' in page  # the filter chips browse.js builds
    assert '"{count} selected": "Selezionati: {count}"' in page

    swapped = admin_client.get("/api/search?media_type_filter=dvd").text
    assert all(t in swapped for t in ("Da vedere", "Tutti i tipi", "Regista"))  # the OOB dropdowns


def test_bulk_messages_are_translated(admin_client, db):
    item_id = _insert_item(db, title="Etichettato", isbn="9780000000040")
    db.commit()
    admin_client.post("/api/account/language", data={"language": "it"})

    tagged = admin_client.post("/api/items/bulk-tags", json={"item_ids": [item_id], "add": ["kids"]})
    assert tagged.json()["message"] == "Tag aggiunto (kids). Elementi: 1"
    refused = admin_client.post("/api/items/bulk-update", json={"item_ids": [], "updates": {}})
    assert refused.json()["message"] == "Nessun elemento o modifica specificati"


@pytest.mark.parametrize("locale", ["en", "it"])
def test_values_with_a_percent_sign_reach_browse_unformatted(admin_client, db, locale):
    """Newstyle _() runs `value % {}` and marks the result safe, so a query-string
    or stored value must never go through it: `%d` would 500 the page."""
    _insert_item(db, title="Strano", isbn="9780000000057", language="100%d", source="50%d off")
    db.commit()
    admin_client.post("/api/account/language", data={"language": locale})

    for url in ("/browse?source_filter=100%25d", "/api/search?source_filter=100%25d", "/browse?view=list"):
        assert admin_client.get(url).status_code == 200, url
    assert "100%d" in admin_client.get("/browse").text


def test_item_page_is_translated(admin_client, db):
    item_id = _insert_item(db, title="Dettaglio", isbn="9780000000064", source="50%d off")
    db.commit()
    admin_client.post("/api/account/language", data={"language": "it"})

    page = admin_client.get(f"/item/{item_id}").text
    for text in ("Torna alla collezione", "Tipo:</span> Libro", "Aggiunto il", "tramite 50%D Off", "Prestiti"):
        assert text in page, text


def test_catalogs_are_complete_and_compiled():
    """Same check as `make check-i18n`, so a stale .mo fails the suite too."""
    r = subprocess.run([sys.executable, "scripts/i18n.py", "--check"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("value", ["0", "off", "false", "no", "OFF", " Off "])
def test_off_stays_english_and_hides_the_picker(admin_client, db, monkeypatch, value):
    """An explicit off-word keeps the UI English and hides the picker, even
    for a user who picked Italian."""
    monkeypatch.setenv("SHELF_I18N", value)
    db.execute("UPDATE users SET language = 'it' WHERE username = 'admin'")
    db.commit()
    page = admin_client.get("/browse", headers={"Accept-Language": "it"})
    assert '<html lang="en"' in page.text
    assert "Sfoglia" not in page.text
    assert 'data-testid="account-language"' not in page.text


@pytest.mark.parametrize("value", [None, "", "on", "nope"])
def test_on_by_default_follows_the_language_and_shows_the_picker(admin_client, db, monkeypatch, value):
    """Unset, empty (the compose pass-through) and any other value mean on."""
    if value is None:
        monkeypatch.delenv("SHELF_I18N", raising=False)
    else:
        monkeypatch.setenv("SHELF_I18N", value)
    db.execute("UPDATE users SET language = 'it' WHERE username = 'admin'")
    db.commit()
    page = admin_client.get("/browse", headers={"Accept-Language": "it"})
    assert '<html lang="it"' in page.text
    assert "Sfoglia" in page.text
    assert 'data-testid="account-language"' in page.text


def test_item_edit_and_manual_add_are_translated(admin_client, db):
    item_id = _insert_item(db, title="Modificami", isbn="9780000000071")
    db.commit()
    admin_client.post("/api/account/language", data={"language": "it"})

    page = admin_client.get(f"/item/{item_id}/edit").text
    for text in ("Modifica Modificami", "Sottotitolo", "Tipo di media", "Autore/i", "UPC-A o EAN-13"):
        assert text in page, text
    scan = admin_client.get("/scan").text
    assert '"dvd": "Regista"' in scan  # the creator labels the manual-add form reads


def test_scan_page_and_results_are_translated(admin_client):
    admin_client.post("/api/account/language", data={"language": "it"})

    page = admin_client.get("/scan").text
    assert "Scansiona con la fotocamera" in page and "Automatico (dal codice a barre)" in page
    assert '"Recently Added": "Aggiunti di recente"' in page  # the results heading getter
    assert '"auto": ["Da iniziare", "In corso", "Finito"]' in page  # default-status words
    # Both manual-entry toggle labels are server-rendered, not an x-text literal.
    assert "Aggiungi a mano" in page and "Nascondi inserimento manuale" in page
    assert "'Hide manual entry'" not in page

    card = admin_client.post("/api/scan", data={"isbn": "9780000000088", "mode": "lookup"}).text
    assert "Non è nella tua collezione" in card and "non trovato" in card


def test_intake_shelf_fill_and_store_are_translated(admin_client):
    admin_client.post("/api/account/language", data={"language": "it"})

    assert "Nessun servizio di visione configurato" in admin_client.get("/intake").text
    shelf_fill = admin_client.get("/shelf-fill").text
    assert 'pagina <span class="text-shelf-text">Disponi</span>.' in shelf_fill
    store = admin_client.get("/store").text
    assert '<html lang="it"' in store and "Modalità negozio" in store
    assert '"OWNED": "POSSEDUTO"' in store  # store.js reads the catalog itself


def test_series_page_is_translated(admin_client, db):
    for n, isbn in ((1, "9780000000095"), (3, "9780000000101")):
        _insert_item(db, title=f"Saga {n}", isbn=isbn, series_name="Saga", series_position=n)
    db.commit()
    admin_client.post("/api/account/language", data={"language": "it"})

    page = admin_client.get("/series").text
    for text in ("La tua biblioteca raggruppata per serie", "2 posseduti", "forse mancano", '"1 book": "1 libro"'):
        assert text in page, text


def test_music_and_periodicals_are_translated(admin_client):
    admin_client.post("/api/account/language", data={"language": "it"})

    assert "La tua musica" in admin_client.get("/music").text
    assert "Ancora nessun periodico in catalogo" in admin_client.get("/periodicals").text


def test_home_stats_and_labels_are_translated(admin_client):
    admin_client.post("/api/account/language", data={"language": "it"})

    assert "La tua collezione è vuota." in admin_client.get("/").text
    assert "Statistiche della collezione" in admin_client.get("/stats").text
    assert "Riepilogo: ottobre 2026" in admin_client.get("/stats/wrapup?period=2026-10").text
    assert "Etichette delle copie" in admin_client.get("/labels").text


def test_trash_and_audits_are_translated(admin_client):
    admin_client.post("/api/account/language", data={"language": "it"})

    assert "Il cestino è vuoto." in admin_client.get("/trash").text
    assert "Ancora nessun inventario" in admin_client.get("/audits").text


def test_duplicates_logs_and_feature_pages_are_translated(admin_client, db):
    admin_client.post("/api/account/language", data={"language": "it"})

    assert "Pagina" in admin_client.get("/logs").text or "Ancora nessuna voce nel log" in admin_client.get("/logs").text
    assert "Non sono duplicati" in admin_client.get("/duplicates").text or "Niente qui." in admin_client.get("/duplicates").text
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('feature.series', '0')")
    db.commit()
    features.invalidate_cache()
    off = admin_client.get("/series")
    assert "Serie" in off.text and "è disattivato su questo Shelf" in off.text


def test_settings_library_tab_is_translated(admin_client):
    admin_client.post("/api/account/language", data={"language": "it"})

    page = admin_client.get("/settings").text
    for text in ("Posizioni", "Dollaro statunitense", "Persone per i prestiti", "Ultima esecuzione"):
        assert text in page, text


def test_settings_features_tags_and_users_are_translated(admin_client):
    admin_client.post("/api/account/language", data={"language": "it"})

    page = admin_client.get("/settings").text
    for text in ("Essenziale", "Strumenti da collezionista", "Aggiungi utente", "Ancora nessun tag"):
        assert text in page, text


def test_settings_data_tab_is_translated(admin_client):
    admin_client.post("/api/account/language", data={"language": "it"})

    page = admin_client.get("/settings").text
    for text in ("Backup e ripristino", "Archivio portabile", "illeggibili senza <code>encryption.key</code>", '"Plus {list}.": "Inoltre: {list}."'):
        assert text in page, text
    assert admin_client.post("/api/import/csv", data={"mode": "bogus"}).json().get("error", "").startswith(("Modalità", "Nessun"))


# --- German -----------------------------------------------------------------


@pytest.mark.parametrize("env, header, expected", [
    ("de", None, "de"),               # no browser hint: the configured default
    ("de", "", "de"),
    ("de", "it-IT,it;q=0.9", "it"),   # a supported browser language still wins
    ("de", "fr-FR", "de"),            # an unsupported one falls to the default
    ("xx", None, "en"),               # an unknown default is ignored
    ("de-DE", None, "de"),            # a regional tag is matched like a header
])
def test_default_language_comes_from_the_environment(monkeypatch, env, header, expected):
    monkeypatch.setenv("SHELF_DEFAULT_LANGUAGE", env)
    assert i18n.resolve(None, header) == expected


def test_german_is_offered_in_the_picker():
    assert "de" in i18n.SUPPORTED
    assert i18n.LANGUAGE_NAMES["de"] == "Deutsch"


def test_home_and_scan_are_translated_into_german(admin_client, db):
    _insert_item(db, title="Zelda", media_type="video_game", upc="045496590420")
    db.commit()
    admin_client.post("/api/account/language", data={"language": "de"})

    home = admin_client.get("/").text
    assert '<html lang="de"' in home
    for text in ("Sammlung ansehen", "Zuletzt hinzugefügt", "Videospiel", "Fehlende Cover"):
        assert text in home, text

    scan = admin_client.get("/scan").text
    for text in ("Mit Kamera scannen", "Nach Titel suchen", "Videospiel"):
        assert text in scan, text
    assert '"Recently Added": "Zuletzt hinzugef\\u00fcgt"' in scan  # the results heading getter


def test_scan_messages_are_translated_into_german(admin_client, db):
    _insert_item(db, title="Buch", isbn="9780000000132")
    db.commit()
    admin_client.post("/api/account/language", data={"language": "de"})
    r = admin_client.post("/api/scan", data={"isbn": "9780000000132", "mode": "lend"})
    assert "Keine Person ausgewählt" in r.text


def test_settings_stats_and_trash_are_translated_into_german(admin_client):
    admin_client.post("/api/account/language", data={"language": "de"})

    settings = admin_client.get("/settings").text
    for text in ("Orte", "US-Dollar", "Personen"):
        assert text in settings, text
    assert "Statistik der Sammlung" in admin_client.get("/stats").text
    assert "Der Papierkorb ist leer." in admin_client.get("/trash").text


def test_recent_scan_words_are_translated(admin_client, db):
    db.execute("INSERT INTO scan_log (isbn, media_type, result) VALUES ('9780000000088', 'book', 'not_found')")
    db.commit()
    admin_client.post("/api/account/language", data={"language": "de"})
    page = admin_client.get("/api/recent-scans").text
    assert "nicht gefunden" in page and "not_found" not in page


def test_feature_buttons_and_confirms_are_translated(admin_client):
    """The Features tab's profile and toggle buttons, and the confirm
    question each carries, follow the user's language."""
    admin_client.post("/api/account/language", data={"language": "it"})
    page = admin_client.get("/settings").text
    for text in ("Applica Essenziale", "Disattiva"):
        assert text in page, text
    assert "Turn off" not in page and "Apply Essenziale" not in page
    token = i18n.current_locale.set("it")
    try:
        rows = [{"key": k, "label": f.label, "enabled": True, "phrases": ["x."]}
                for k, f in features.FEATURES.items()]
        confirms = [r["confirm"] for r in features.profile_rows(rows) if r["confirm"]]
        assert confirms and all(c.endswith("comunque?") for c in confirms), confirms
    finally:
        i18n.current_locale.reset(token)


def test_settings_sync_integrations_are_translated(admin_client):
    admin_client.post("/api/account/language", data={"language": "it"})

    page = admin_client.get("/settings").text
    for text in ("Sincronizzazione Audiobookshelf", "Importa la biblioteca da Hardcover", '"Import from Hardcover": "Importa da Hardcover"'):
        assert text in page, text


def test_settings_provider_integrations_are_translated(admin_client):
    admin_client.post("/api/account/language", data={"language": "it"})

    page = admin_client.get("/settings").text
    for text in ("Valutazione della collezione", "Ottenere una chiave API di Google Books", '"Valuate Collection": "Valuta la collezione"'):
        assert text in page, text


def test_settings_vision_and_igdb_are_translated(admin_client):
    admin_client.post("/api/account/language", data={"language": "it"})

    page = admin_client.get("/settings").text
    for text in ("Acquisizione da foto (visione)", "Ottenere le credenziali IGDB"):
        assert text in page, text


def test_settings_komga_romm_and_discogs_are_translated(admin_client):
    admin_client.post("/api/account/language", data={"language": "it"})

    page = admin_client.get("/settings").text
    for text in ("Prova la connessione", "Token di accesso personale"):
        assert text in page, text
