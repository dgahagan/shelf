"""Browse's creator column and author sort option follow the Type filter (#119).

With one type picked, the list header and the sort option use that type's
word from `CREATOR_LABELS` ("Artist", "Developer", "Director"); otherwise both
keep Browse's "Author" — singular, not the add form's "Author(s)". The header
is re-rendered by every page-1 search; the sort option lives in the page and is
re-rendered out of band. The column picker's own JSON always says "Author", so
every pin reads the `<th>` or `<option>` element, never a bare substring (G69).
"""

import re

import pytest

from app.config import MUSIC_MEDIA_TYPES, browse_creator_label
from tests.conftest import _insert_item


def _author_th(html: str) -> str:
    m = re.search(r'<th\b[^>]*data-col="author"[^>]*>(.*?)</th>', html, re.S)
    assert m, "no author column header rendered"
    return m.group(1).strip()


def _author_option(html: str) -> tuple[str, str]:
    """(opening tag, text) of the `sort-option-author` option."""
    m = re.search(r'(<option\b[^>]*id="sort-option-author"[^>]*>)(.*?)</option>', html, re.S)
    assert m, "no sort-option-author rendered"
    return m.group(1), m.group(2).strip()


class TestBrowseCreatorLabel:
    def test_every_music_type_is_artist(self):
        for media_type in MUSIC_MEDIA_TYPES:
            assert browse_creator_label(media_type) == "Artist", media_type

    def test_game_and_dvd_take_their_override(self):
        assert browse_creator_label("video_game") == "Developer"
        assert browse_creator_label("dvd") == "Director"

    @pytest.mark.parametrize("media_type", ["book", None, "", "not_a_type"])
    def test_everything_else_reads_author_singular(self, media_type):
        assert browse_creator_label(media_type) == "Author"


@pytest.fixture
def seeded(db):
    _insert_item(db, title="Label Vinyl", isbn="9780000000401", media_type="vinyl")
    _insert_item(db, title="Label Book", isbn="9780000000402", media_type="book")
    _insert_item(db, title="Label Dvd", isbn="9780000000403", media_type="dvd")
    db.commit()  # G48


@pytest.mark.parametrize(
    "type_filter, word",
    [("vinyl", "Artist"), ("dvd", "Director"), ("book", "Author"), ("", "Author")],
)
def test_search_header_and_oob_option_follow_the_type(admin_client, seeded, type_filter, word):
    html = admin_client.get(
        f"/api/search?media_type_filter={type_filter}&view=list"
    ).text
    assert _author_th(html) == word
    tag, text = _author_option(html)
    assert text == word
    assert 'hx-swap-oob="true"' in tag


@pytest.mark.parametrize("sort, selected", [("author", True), ("title_asc", False), ("newest", False)])
def test_oob_option_is_selected_only_under_the_author_sort(admin_client, seeded, sort, selected):
    html = admin_client.get(f"/api/search?media_type_filter=vinyl&sort={sort}").text
    tag, _ = _author_option(html)
    assert (re.search(r"\bselected\b", tag) is not None) is selected


def test_page_two_renders_no_oob_option(admin_client, seeded):
    # The option swaps only with page 1's counts; appended pages carry none.
    html = admin_client.get("/api/search?media_type_filter=vinyl&page=2").text
    assert 'id="sort-option-author"' not in html


@pytest.mark.parametrize("type_filter, word", [("vinyl", "Artist"), ("", "Author")])
def test_browse_page_renders_the_static_option_for_the_type(admin_client, seeded, type_filter, word):
    html = admin_client.get(f"/browse?media_type_filter={type_filter}").text
    # The page holds exactly one such option: the static one in the sort select.
    assert html.count('id="sort-option-author"') == 1
    _, text = _author_option(html)
    assert text == word
