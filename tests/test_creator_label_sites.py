"""The creator field's label/placeholder follows media_type (issue #119, T7).

Element-scoped assertions only (G69) — a bare substring check on "Artist" or
"Author(s)" would also match unrelated page chrome.
"""

import json
import re

from tests.conftest import _insert_item


def _authors_label(html):
    match = re.search(r'<label for="authors"[^>]*>([^<]*)</label>', html)
    assert match, "no <label for=\"authors\"> element found in rendered edit page"
    return match.group(1).strip()


class TestEditPageCreatorLabel:
    def test_vinyl_is_labeled_artist(self, editor_client, db):
        item_id = _insert_item(db, title="Vinyl Item", media_type="vinyl")
        db.commit()
        html = editor_client.get(f"/item/{item_id}/edit").text
        assert _authors_label(html) == "Artist"

    def test_video_game_is_labeled_developer(self, editor_client, db):
        item_id = _insert_item(db, title="Game Item", media_type="video_game")
        db.commit()
        html = editor_client.get(f"/item/{item_id}/edit").text
        assert _authors_label(html) == "Developer"

    def test_book_is_labeled_authors(self, editor_client, db):
        item_id = _insert_item(db, title="Book Item", media_type="book")
        db.commit()
        html = editor_client.get(f"/item/{item_id}/edit").text
        assert _authors_label(html) == "Author(s)"


class TestIntakePageCreatorLabels:
    def test_root_carries_valid_creator_labels_json(self, editor_client):
        html = editor_client.get("/intake").text
        match = re.search(r'data-creator-labels=\'([^\']*)\'', html)
        assert match, "intakePage root is missing data-creator-labels"
        labels = json.loads(match.group(1))
        assert labels.get("vinyl") == "Artist"
