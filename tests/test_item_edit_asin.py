"""ASIN on the item edit form: round-trip, normalisation, refusal, clearing."""
import re
from html.parser import HTMLParser


class _Form(HTMLParser):
    """Collect what a browser would submit from the edit form."""

    def __init__(self):
        super().__init__()
        self.data = {}
        self._in_form = False
        self._select = None
        self._textarea = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and "/api/items/" in (a.get("action") or ""):
            self._in_form = True
        if not self._in_form:
            return
        name = a.get("name")
        if tag == "input" and name:
            kind = a.get("type", "text")
            if kind in ("file", "submit", "button"):
                return
            if kind in ("checkbox", "radio") and "checked" not in a:
                return
            self.data[name] = a.get("value") or ""
        elif tag == "select" and name:
            self._select = name
            self.data.setdefault(name, "")
        elif tag == "option" and self._select and "selected" in a:
            self.data[self._select] = a.get("value") or ""
        elif tag == "textarea" and name:
            self._textarea = name
            self.data[name] = ""

    def handle_data(self, data):
        if self._textarea:
            self.data[self._textarea] += data

    def handle_endtag(self, tag):
        if tag == "form":
            self._in_form = False
        elif tag == "select":
            self._select = None
        elif tag == "textarea":
            self._textarea = None


def _rendered_form(client, item_id):
    html = client.get(f"/item/{item_id}/edit").text
    parser = _Form()
    parser.feed(html)
    return html, parser.data


def _item(db, media_type="audiobook", asin=None):
    item_id = db.execute(
        "INSERT INTO items (title, media_type, owned, asin) VALUES (?, ?, 1, ?)",
        ("Some Title", media_type, asin),
    ).lastrowid
    db.commit()
    return item_id


def _asin(db, item_id):
    return db.execute("SELECT asin FROM items WHERE id = ?", (item_id,)).fetchone()["asin"]


def test_asin_round_trips_through_the_rendered_form(editor_client, db):
    item_id = _item(db, asin="B0D1234567")
    html, data = _rendered_form(editor_client, item_id)
    assert data["asin"] == "B0D1234567"
    resp = editor_client.post(f"/api/items/{item_id}", data=data, follow_redirects=False)
    assert resp.status_code == 303
    assert "error=" not in resp.headers["location"]
    assert _asin(db, item_id) == "B0D1234567"


def test_lowercase_asin_is_saved_upper_case(editor_client, db):
    item_id = _item(db)
    resp = editor_client.post(f"/api/items/{item_id}", data={"asin": "b0d1234567"},
                              follow_redirects=False)
    assert resp.status_code == 303
    assert "error=" not in resp.headers["location"]
    assert _asin(db, item_id) == "B0D1234567"


def test_malformed_asin_is_refused_and_banner_renders(editor_client, db):
    item_id = _item(db, asin="B0D1234567")
    resp = editor_client.post(f"/api/items/{item_id}", data={"asin": "B0D-12"},
                              follow_redirects=False)
    assert resp.status_code == 303
    assert "error=invalid_asin" in resp.headers["location"]
    page = editor_client.get(resp.headers["location"])
    assert "An ASIN is ten letters or digits, like B0D1234567." in page.text
    assert _asin(db, item_id) == "B0D1234567"


def test_empty_asin_clears_to_null(editor_client, db):
    item_id = _item(db, asin="B0D1234567")
    resp = editor_client.post(f"/api/items/{item_id}", data={"asin": ""},
                              follow_redirects=False)
    assert resp.status_code == 303
    assert "error=" not in resp.headers["location"]
    assert _asin(db, item_id) is None


def test_book_without_asin_saves_hidden_empty_field_as_null(editor_client, db):
    item_id = _item(db, media_type="book")
    html, data = _rendered_form(editor_client, item_id)
    assert re.search(r'name="asin"', html), "the ASIN control stays in the DOM"
    assert data["asin"] == ""
    resp = editor_client.post(f"/api/items/{item_id}", data=data, follow_redirects=False)
    assert resp.status_code == 303
    assert "error=" not in resp.headers["location"]
    assert _asin(db, item_id) is None
