"""Tests for related-media groups built from the existing item_links graph."""

import pytest

from app.services import media_groups
from app.services.item_write import insert_item


def test_link_is_undirected_and_idempotent(db):
    a = insert_item(db, title="Dune", media_type="book")
    b = insert_item(db, title="Dune", media_type="dvd")

    assert media_groups.link_items(db, b, a, link_type="adaptation") is True
    assert media_groups.link_items(db, a, b, link_type="adaptation") is False

    row = db.execute("SELECT item_a_id, item_b_id, link_type FROM item_links").fetchone()
    assert tuple(row) == (min(a, b), max(a, b), "adaptation")


def test_self_links_and_missing_items_are_not_created(db):
    item_id = insert_item(db, title="One")
    assert media_groups.link_items(db, item_id, item_id) is False
    assert media_groups.link_items(db, item_id, 999999) is False
    assert db.execute("SELECT COUNT(*) AS c FROM item_links").fetchone()["c"] == 0


def test_unknown_link_type_is_rejected(db):
    a = insert_item(db, title="A")
    b = insert_item(db, title="B")
    with pytest.raises(ValueError, match="Unknown related-media"):
        media_groups.link_items(db, a, b, link_type="guess")


def test_group_is_full_transitive_connected_component(db):
    a = insert_item(db, title="A")
    b = insert_item(db, title="B")
    c = insert_item(db, title="C")
    d = insert_item(db, title="D")

    media_groups.link_items(db, a, b, link_type="format")
    media_groups.link_items(db, b, c, link_type="related")

    assert media_groups.related_ids(db, a) == [b, c]
    assert media_groups.related_ids(db, c) == [a, b]
    assert media_groups.related_ids(db, b, include_self=True) == [a, b, c]
    assert media_groups.related_ids(db, d) == []


def test_direct_links_preserve_relationship_type(db):
    book = insert_item(db, title="Dune", media_type="book")
    ebook = insert_item(db, title="Dune eBook", media_type="ebook")
    film = insert_item(db, title="Dune Film", media_type="dvd")

    media_groups.link_items(db, book, ebook, link_type="format")
    media_groups.link_items(db, book, film, link_type="adaptation")

    links = media_groups.direct_links(db, book)
    assert [(row["title"], row["link_type"]) for row in links] == [
        ("Dune eBook", "format"),
        ("Dune Film", "adaptation"),
    ]


def test_unlink_can_split_a_component(db):
    a = insert_item(db, title="A")
    b = insert_item(db, title="B")
    c = insert_item(db, title="C")
    media_groups.link_items(db, a, b)
    media_groups.link_items(db, b, c)

    assert media_groups.unlink_items(db, b, c) is True
    assert media_groups.related_ids(db, a) == [b]
    assert media_groups.related_ids(db, c) == []


def test_search_excludes_every_item_already_in_the_group(db):
    anchor = insert_item(db, title="The Hobbit", authors="J. R. R. Tolkien")
    ebook = insert_item(db, title="The Hobbit eBook", authors="J. R. R. Tolkien", media_type="ebook")
    candidate = insert_item(db, title="The Hobbit Film", media_type="dvd")
    unrelated = insert_item(db, title="Dune", media_type="book")
    media_groups.link_items(db, anchor, ebook, link_type="format")

    results = media_groups.search_candidates(db, anchor, "Hobbit")
    ids = [row["id"] for row in results]
    assert candidate in ids
    assert anchor not in ids
    assert ebook not in ids
    assert unrelated not in ids
