"""Settings → Library: the Buy links card and POST /api/settings/buy-links."""
import re

import pytest

from app.crypto import SENSITIVE_KEYS
from app.database import get_db

KEYS = ("buy_links_bookshop_id", "buy_links_bookshop_host",
        "buy_links_amazon_tag", "buy_links_amazon_host")
URL = "/api/settings/buy-links"


def _block(html):
    """The rendered Buy links card, so assertions cannot match elsewhere."""
    start = html.index('data-testid="buy-links-block"')
    end = html.index("</form>", start)
    return html[start:end]


def _input(block, testid):
    tag = re.search(r'<input[^>]*data-testid="%s"[^>]*>' % testid, block, re.S).group(0)
    return re.search(r'value="([^"]*)"', tag).group(1)


def _selected(block, testid):
    start = block.index('data-testid="%s"' % testid)
    select = block[start:block.index("</select>", start)]
    return re.findall(r'<option value="([^"]*)"\s*selected', select)


def _rendered(block):
    """Every named control's value, exactly as a browser would submit it (G36)."""
    return {
        "buy_links_bookshop_id": _input(block, "buy-links-bookshop-id"),
        "buy_links_bookshop_host": _selected(block, "buy-links-bookshop-host")[0],
        "buy_links_amazon_tag": _input(block, "buy-links-amazon-tag"),
        "buy_links_amazon_host": _selected(block, "buy-links-amazon-host")[0],
    }


def _setting(key):
    with get_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _valid(**over):
    form = {"buy_links_bookshop_id": "513", "buy_links_bookshop_host": "bookshop.org",
            "buy_links_amazon_tag": "shelf-20", "buy_links_amazon_host": "www.amazon.com"}
    form.update(over)
    return form


class TestBlock:
    def test_defaults_render_when_nothing_is_saved(self, admin_client):
        block = _block(admin_client.get("/settings").text)
        assert _rendered(block) == {
            "buy_links_bookshop_id": "", "buy_links_bookshop_host": "bookshop.org",
            "buy_links_amazon_tag": "", "buy_links_amazon_host": "www.amazon.com",
        }

    def test_exactly_one_option_is_selected_in_each_select(self, admin_client):
        block = _block(admin_client.get("/settings").text)
        assert len(_selected(block, "buy-links-bookshop-host")) == 1
        assert len(_selected(block, "buy-links-amazon-host")) == 1

    def test_honesty_line_is_visible_text(self, admin_client):
        block = _block(admin_client.get("/settings").text)
        line = re.search(r'data-testid="buy-links-honesty"[^>]*>([^<]*)<', block).group(1)
        assert line == ("These links pay you, through your own affiliate accounts. Shelf earns "
                        "nothing. A Bookshop.org link without an ISBN is a plain search and pays nobody.")

    def test_saved_values_round_trip(self, admin_client):
        """G36: re-post what the form rendered, change one field, re-render all four."""
        form = _rendered(_block(admin_client.get("/settings").text))
        form["buy_links_amazon_tag"] = "shelf-20"
        resp = admin_client.post(URL, data=form, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/settings"

        form = _rendered(_block(admin_client.get("/settings").text))
        assert form == {
            "buy_links_bookshop_id": "", "buy_links_bookshop_host": "bookshop.org",
            "buy_links_amazon_tag": "shelf-20", "buy_links_amazon_host": "www.amazon.com",
        }
        form["buy_links_amazon_host"] = "www.amazon.co.uk"
        form["buy_links_bookshop_id"] = "513"
        admin_client.post(URL, data=form)
        assert _rendered(_block(admin_client.get("/settings").text)) == {
            "buy_links_bookshop_id": "513", "buy_links_bookshop_host": "bookshop.org",
            "buy_links_amazon_tag": "shelf-20", "buy_links_amazon_host": "www.amazon.co.uk",
        }

    def test_feature_off_note_appears_with_the_flag_off(self, admin_client):
        assert 'data-feature="buy_links"' not in _block(admin_client.get("/settings").text)
        admin_client.post("/api/settings/features/buy_links", data={"enabled": "0"})
        block = _block(admin_client.get("/settings").text)
        assert 'data-testid="feature-off-note" data-feature="buy_links"' in block

    def test_keys_are_not_secrets_and_the_tag_renders_unmasked(self, admin_client):
        assert not set(KEYS) & set(SENSITIVE_KEYS)
        admin_client.post(URL, data=_valid())
        block = _block(admin_client.get("/settings").text)
        assert _input(block, "buy-links-amazon-tag") == "shelf-20"
        assert _setting("buy_links_amazon_tag") == "shelf-20"


class TestSave:
    @pytest.mark.parametrize("over,rule", [
        ({"buy_links_amazon_tag": "shelf 20"}, "letters, digits and hyphens"),
        ({"buy_links_amazon_tag": "shelf<20"}, "letters, digits and hyphens"),
        ({"buy_links_amazon_tag": 'shelf"20'}, "letters, digits and hyphens"),
        ({"buy_links_amazon_tag": "a" * 41}, "up to 40 characters"),
        ({"buy_links_bookshop_id": "abc"}, "digits only"),
        ({"buy_links_bookshop_host": "bookshop.example"}, "Unknown Bookshop.org site"),
        ({"buy_links_amazon_host": "www.amazon.example"}, "Unknown Amazon marketplace"),
        ({"buy_links_bookshop_host": "https://evil.example"}, "Unknown Bookshop.org site"),
        ({"buy_links_amazon_host": "https://evil.example"}, "Unknown Amazon marketplace"),
    ])
    def test_refusals_name_the_rule_and_write_nothing(self, admin_client, over, rule):
        resp = admin_client.post(URL, data=_valid(**over), follow_redirects=False)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert rule in body["message"]
        for key in KEYS:
            assert _setting(key) is None, key

    def test_a_refusal_leaves_earlier_values_in_place(self, admin_client):
        admin_client.post(URL, data=_valid())
        admin_client.post(URL, data=_valid(buy_links_amazon_tag="bad tag", buy_links_bookshop_id="999"))
        assert _setting("buy_links_bookshop_id") == "513"
        assert _setting("buy_links_amazon_tag") == "shelf-20"

    def test_blank_tags_are_accepted_and_stored_empty(self, admin_client):
        admin_client.post(URL, data=_valid())
        resp = admin_client.post(URL, data=_valid(buy_links_bookshop_id="", buy_links_amazon_tag=""),
                                 follow_redirects=False)
        assert resp.status_code == 303
        assert _setting("buy_links_bookshop_id") == ""
        assert _setting("buy_links_amazon_tag") == ""

    def test_input_is_stripped(self, admin_client):
        admin_client.post(URL, data=_valid(buy_links_amazon_tag="  shelf-20 \n"))
        assert _setting("buy_links_amazon_tag") == "shelf-20"

    def test_editor_gets_role_refusal(self, editor_client):
        resp = editor_client.post(URL, data=_valid(), follow_redirects=False)
        assert resp.status_code == 403
        for key in KEYS:
            assert _setting(key) is None
