"""Tests for ABS cross-linking (_auto_link_items author guard, detail badge)."""
from app.services.audiobookshelf import _authors_compatible, _auto_link_items
from tests.conftest import _insert_item


def _links(db):
    return db.execute("SELECT item_a_id, item_b_id FROM item_links").fetchall()


class TestAuthorsCompatible:
    def test_matching_first_author(self):
        assert _authors_compatible("Frank Herbert", "Frank Herbert") is True
        assert _authors_compatible("Frank Herbert, Kevin Anderson", "Frank Herbert") is True

    def test_case_insensitive(self):
        assert _authors_compatible("frank herbert", "FRANK HERBERT") is True

    def test_different_authors(self):
        assert _authors_compatible("Isaac Asimov", "Peter Ackroyd") is False

    def test_missing_side_allows(self):
        assert _authors_compatible(None, "Frank Herbert") is True
        assert _authors_compatible("Frank Herbert", "") is True


class TestAutoLink:
    def test_links_same_title_same_author(self, db):
        a = _insert_item(db, title="Dune", isbn=None, media_type="book", authors="Frank Herbert")
        b = _insert_item(db, title="Dune", isbn=None, media_type="audiobook",
                         authors="Frank Herbert", abs_id="li_abc")
        db.execute("COMMIT")
        _auto_link_items()
        assert (min(a, b), max(a, b)) in [tuple(r) for r in _links(db)]

    def test_rejects_same_title_different_author(self, db):
        _insert_item(db, title="Foundation", isbn=None, media_type="book", authors="Peter Ackroyd")
        _insert_item(db, title="Foundation", isbn=None, media_type="audiobook",
                     authors="Isaac Asimov", abs_id="li_abc")
        db.execute("COMMIT")
        _auto_link_items()
        assert _links(db) == []

    def test_isbn_match_ignores_authors(self, db):
        a = _insert_item(db, title="Dune", isbn="9780441172719", media_type="book", authors=None)
        b = _insert_item(db, title="Dune (Unabridged)", isbn="9780441172719",
                         media_type="audiobook", authors="Frank Herbert", abs_id="li_abc")
        db.execute("COMMIT")
        _auto_link_items()
        assert (min(a, b), max(a, b)) in [tuple(r) for r in _links(db)]

    def test_title_match_when_authors_missing(self, db):
        a = _insert_item(db, title="Dune", isbn=None, media_type="book", authors=None)
        b = _insert_item(db, title="Dune", isbn=None, media_type="audiobook",
                         authors="Frank Herbert", abs_id="li_abc")
        db.execute("COMMIT")
        _auto_link_items()
        assert (min(a, b), max(a, b)) in [tuple(r) for r in _links(db)]


class TestAlsoInAbsBadge:
    def _seed_linked(self, db):
        book = _insert_item(db, title="Dune", isbn="9780900000601", media_type="book",
                            authors="Frank Herbert")
        audio = _insert_item(db, title="Dune", isbn="9780900000618", media_type="audiobook",
                             authors="Frank Herbert", abs_id="li_abc")
        db.execute(
            "INSERT INTO item_links (item_a_id, item_b_id) VALUES (?, ?)",
            (min(book, audio), max(book, audio)),
        )
        db.execute("INSERT INTO settings (key, value) VALUES ('abs_url', 'https://abs.example')")
        db.execute("COMMIT")
        return book, audio

    def test_physical_item_deep_links_to_abs(self, admin_client, db):
        book, _ = self._seed_linked(db)
        html = admin_client.get(f"/item/{book}").text
        assert "Also in Audiobookshelf (Audiobook)" in html
        assert "https://abs.example/item/li_abc" in html

    def test_abs_item_shows_listen_not_also(self, admin_client, db):
        _, audio = self._seed_linked(db)
        html = admin_client.get(f"/item/{audio}").text
        assert "Listen on Audiobookshelf" in html
        assert "Also in Audiobookshelf (Audiobook)" not in html

    def test_no_badge_without_abs_url_setting(self, admin_client, db):
        book = _insert_item(db, title="Dune", isbn="9780900000625", media_type="book")
        audio = _insert_item(db, title="Dune A", isbn="9780900000632",
                             media_type="audiobook", abs_id="li_abc")
        db.execute("INSERT INTO item_links (item_a_id, item_b_id) VALUES (?, ?)",
                   (min(book, audio), max(book, audio)))
        db.execute("COMMIT")
        html = admin_client.get(f"/item/{book}").text
        assert "Also in Audiobookshelf (Audiobook)" not in html
