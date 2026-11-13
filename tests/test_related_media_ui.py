from html.parser import HTMLParser
from urllib.parse import urlencode

import pytest

from app.services import media_groups


class _FormatBlock(HTMLParser):
    """Read only the format block, not labels/links in the related-media panel."""

    def __init__(self, html):
        super().__init__()
        self.count = 0
        self.depth = 0
        self.attrs = {}
        self.hrefs = []
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and attrs.get("id") == "also-available-as":
            self.count += 1
            self.attrs = attrs
            self.depth = 1
        elif self.depth:
            if tag == "div":
                self.depth += 1
            elif tag == "a":
                self.hrefs.append(attrs["href"])

    def handle_endtag(self, tag):
        if tag == "div" and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.text.append(data)


def _format_block(html, *, oob=False):
    block = _FormatBlock(html)
    assert block.count == 1
    assert block.attrs.get("hx-swap-oob") == ("outerHTML" if oob else None)
    return block


def _item(db, title, media_type="book", authors=None):
    return db.execute(
        "INSERT INTO items (title, media_type, authors, owned) VALUES (?, ?, ?, 1)",
        (title, media_type, authors),
    ).lastrowid


def test_item_detail_loads_related_media_panel(admin_client, db):
    item_id = _item(db, "The Book")
    db.commit()

    response = admin_client.get(f"/item/{item_id}")

    assert response.status_code == 200
    assert f'hx-get="/api/related-media/items/{item_id}/panel"' in response.text


def test_panel_shows_full_transitive_group_and_directness(admin_client, db):
    a = _item(db, "Novel")
    b = _item(db, "Audiobook", "audiobook")
    c = _item(db, "Film", "dvd")
    media_groups.link_items(db, a, b, link_type="format")
    media_groups.link_items(db, b, c, link_type="adaptation")
    db.commit()

    response = admin_client.get(f"/api/related-media/items/{a}/panel")

    assert response.status_code == 200
    assert "Audiobook" in response.text
    assert "Film" in response.text
    assert "Format" in response.text
    assert "via related group" in response.text
    assert f"/links/{b}" in response.text
    assert f"/links/{c}" not in response.text
    assert "hx-swap-oob" not in response.text
    assert 'id="also-available-as"' not in response.text


def test_viewer_panel_is_read_only(viewer_client, db):
    a = _item(db, "Novel")
    b = _item(db, "Film", "dvd")
    media_groups.link_items(db, a, b, link_type="adaptation")
    db.commit()

    response = viewer_client.get(f"/api/related-media/items/{a}/panel")

    assert response.status_code == 200
    assert "Film" in response.text
    assert "Link another catalogue item" not in response.text
    assert "Unlink" not in response.text


def test_search_excludes_entire_existing_component(admin_client, db):
    a = _item(db, "Story")
    b = _item(db, "Story audio", "audiobook")
    c = _item(db, "Story film", "dvd")
    outsider = _item(db, "Story game", "video_game")
    media_groups.link_items(db, a, b, link_type="format")
    media_groups.link_items(db, b, c, link_type="adaptation")
    db.commit()

    response = admin_client.get(
        f"/api/related-media/items/{a}/search", params={"q": "Story"}
    )

    assert response.status_code == 200
    assert "Story game" in response.text
    assert f'value="{outsider}"' in response.text
    assert f'value="{b}"' not in response.text
    assert f'value="{c}"' not in response.text
    assert "hx-swap-oob" not in response.text


def test_editor_can_link_an_adaptation(admin_client, db):
    a = _item(db, "Novel")
    b = _item(db, "Film", "dvd")
    db.commit()

    response = admin_client.post(
        f"/api/related-media/items/{a}/links",
        data={"other_item_id": str(b), "link_type": "adaptation"},
    )

    assert response.status_code == 200
    edge = db.execute(
        "SELECT link_type FROM item_links WHERE item_a_id = ? AND item_b_id = ?",
        tuple(sorted((a, b))),
    ).fetchone()
    assert edge["link_type"] == "adaptation"
    assert "Film" in response.text


def test_unlinking_direct_edge_can_split_group(admin_client, db):
    # Distinct titles: a bare "B" or "C" also matches the panel's own copy.
    a = _item(db, "Title Alpha")
    b = _item(db, "Title Bravo")
    c = _item(db, "Title Charlie")
    media_groups.link_items(db, a, b)
    media_groups.link_items(db, b, c)
    db.commit()

    response = admin_client.delete(f"/api/related-media/items/{a}/links/{b}")

    assert response.status_code == 200
    assert "Title Bravo" not in response.text
    assert "Title Charlie" not in response.text
    assert media_groups.related_ids(db, a) == []
    assert media_groups.related_ids(db, b) == [c]


def test_viewer_cannot_mutate_related_media(viewer_client, db):
    a = _item(db, "Novel")
    b = _item(db, "Film", "dvd")
    media_groups.link_items(db, a, b)
    db.commit()

    link_response = viewer_client.post(
        f"/api/related-media/items/{a}/links",
        data={"other_item_id": str(b), "link_type": "adaptation"},
    )
    unlink_response = viewer_client.delete(
        f"/api/related-media/items/{a}/links/{b}"
    )

    assert link_response.status_code == 403
    assert unlink_response.status_code == 403
    assert "hx-swap-oob" not in link_response.text + unlink_response.text
    assert media_groups.direct_links(db, a)[0]["link_type"] == "related"


def test_link_and_unlink_refuse_missing_targets(admin_client, db):
    a = _item(db, "Novel")
    db.commit()

    link_response = admin_client.post(
        f"/api/related-media/items/{a}/links",
        data={"other_item_id": "999999", "link_type": "related"},
    )
    unlink_response = admin_client.delete(
        f"/api/related-media/items/{a}/links/999999"
    )

    assert link_response.status_code == 404
    assert unlink_response.status_code == 404
    assert "hx-swap-oob" not in link_response.text + unlink_response.text
    assert media_groups.related_ids(db, a) == []


def test_soft_deleted_items_are_not_related_media_targets(admin_client, db):
    live = _item(db, "Live")
    deleted = _item(db, "Deleted")
    db.execute(
        "UPDATE items SET deleted_at = '2026-09-19T00:00:00' WHERE id = ?",
        (deleted,),
    )
    db.commit()

    panel = admin_client.get(f"/api/related-media/items/{deleted}/panel")
    search = admin_client.get(
        f"/api/related-media/items/{live}/search", params={"q": "Deleted"}
    )
    link = admin_client.post(
        f"/api/related-media/items/{live}/links",
        data={"other_item_id": str(deleted), "link_type": "related"},
    )

    assert panel.status_code == 404
    assert f'value="{deleted}"' not in search.text
    assert link.status_code == 404
    assert "hx-swap-oob" not in link.text
    assert media_groups.related_ids(db, live) == []


def test_first_format_link_refreshes_an_initially_empty_target(editor_client, db):
    a = _item(db, "Novel")
    b = _item(db, "Novel audio", "audiobook")
    db.commit()

    empty = _format_block(editor_client.get(f"/item/{a}").text)
    assert empty.hrefs == []
    assert "".join(empty.text).strip() == ""

    response = editor_client.post(
        f"/api/related-media/items/{a}/links",
        data={"other_item_id": str(b), "link_type": "format"},
    )

    assert response.status_code == 200
    assert 'id="related-media-panel"' in response.text
    block = _format_block(response.text, oob=True)
    assert block.hrefs == [f"/item/{b}"]
    assert "Also available as:" in "".join(block.text)
    assert "Audiobook" in "".join(block.text)


@pytest.mark.parametrize("keep_another_format", [False, True])
def test_unlink_refreshes_formats_and_keeps_the_empty_target(
    editor_client, db, keep_another_format
):
    a = _item(db, "Novel")
    b = _item(db, "Novel audio", "audiobook")
    c = _item(db, "Novel ebook", "ebook")
    film = _item(db, "The Film", "dvd")
    media_groups.link_items(db, a, b, link_type="format")
    media_groups.link_items(db, a, film, link_type="adaptation")
    if keep_another_format:
        media_groups.link_items(db, a, c, link_type="format")
    db.commit()

    response = editor_client.delete(f"/api/related-media/items/{a}/links/{b}")

    assert response.status_code == 200
    assert "The Film" in response.text  # The panel is still populated.
    block = _format_block(response.text, oob=True)
    assert block.hrefs == ([f"/item/{c}"] if keep_another_format else [])
    assert ("Also available as:" in "".join(block.text)) == keep_another_format


def test_refresh_only_lists_live_direct_format_neighbours(editor_client, db):
    # Create the neighbour first to cover the reverse orientation of an edge.
    audio = _item(db, "Novel audio", "audiobook")
    a = _item(db, "Novel")
    ebook = _item(db, "Novel ebook", "ebook")
    related = _item(db, "Related book")
    film = _item(db, "The Film", "dvd")
    trashed = _item(db, "Trashed format", "ebook")
    media_groups.link_items(db, a, audio, link_type="format")
    media_groups.link_items(db, audio, ebook, link_type="format")
    media_groups.link_items(db, a, film, link_type="adaptation")
    media_groups.link_items(db, a, trashed, link_type="format")
    db.execute("UPDATE items SET deleted_at = '2026-09-19T00:00:00' WHERE id = ?", (trashed,))
    db.commit()

    response = editor_client.post(
        f"/api/related-media/items/{a}/links",
        data={"other_item_id": str(related), "link_type": "related"},
    )

    assert response.status_code == 200
    assert "Novel ebook" in response.text  # Transitive formats remain in the panel.
    assert _format_block(response.text, oob=True).hrefs == [f"/item/{audio}"]
    assert _format_block(editor_client.get(f"/item/{a}").text).hrefs == [f"/item/{audio}"]


@pytest.mark.parametrize("link_type", ["related", "adaptation"])
def test_non_format_mutations_do_not_claim_another_format(editor_client, db, link_type):
    a = _item(db, "Novel")
    b = _item(db, "Other work", "dvd")
    db.commit()

    response = editor_client.post(
        f"/api/related-media/items/{a}/links",
        data={"other_item_id": str(b), "link_type": link_type},
    )

    assert response.status_code == 200
    assert "Other work" in response.text
    block = _format_block(response.text, oob=True)
    assert block.hrefs == []
    assert "".join(block.text).strip() == ""


@pytest.mark.parametrize("existing_type", ["format", "related"])
def test_duplicate_link_refresh_uses_the_stored_type(editor_client, db, existing_type):
    a = _item(db, "Novel")
    b = _item(db, "Novel audio", "audiobook")
    media_groups.link_items(db, a, b, link_type=existing_type)
    db.commit()

    response = editor_client.post(
        f"/api/related-media/items/{a}/links",
        data={"other_item_id": str(b), "link_type": "related" if existing_type == "format" else "format"},
    )

    assert response.status_code == 200
    assert media_groups.direct_links(db, a)[0]["link_type"] == existing_type
    assert _format_block(response.text, oob=True).hrefs == (
        [f"/item/{b}"] if existing_type == "format" else []
    )


@pytest.mark.parametrize("refusal", ["self", "invalid_type", "missing_source", "trashed_source"])
def test_refused_mutations_do_not_emit_a_format_refresh(editor_client, db, refusal):
    a = _item(db, "Novel")
    b = _item(db, "Novel audio", "audiobook")
    if refusal == "trashed_source":
        db.execute("UPDATE items SET deleted_at = '2026-09-19T00:00:00' WHERE id = ?", (a,))
    db.commit()
    source = 999999 if refusal == "missing_source" else a

    response = editor_client.post(
        f"/api/related-media/items/{source}/links",
        data={"other_item_id": str(a if refusal == "self" else b),
              "link_type": "unknown" if refusal == "invalid_type" else "format"},
    )

    assert response.status_code == (400 if refusal in ("self", "invalid_type") else 404)
    assert "hx-swap-oob" not in response.text
    assert db.execute("SELECT COUNT(*) FROM item_links").fetchone()[0] == 0


def test_missing_edge_does_not_refresh_or_remove_another_format(editor_client, db):
    a = _item(db, "Novel")
    b = _item(db, "Novel audio", "audiobook")
    c = _item(db, "Unlinked ebook", "ebook")
    media_groups.link_items(db, a, b, link_type="format")
    db.commit()

    response = editor_client.delete(f"/api/related-media/items/{a}/links/{c}")

    assert response.status_code == 404
    assert "hx-swap-oob" not in response.text
    assert [row["item_id"] for row in media_groups.direct_links(db, a)] == [b]


@pytest.mark.parametrize("origin, key", [
    ("series", "series"), ("stats", "stats"), ("", ""),
    ("https://evil.example", ""), ('"><script>alert(1)</script>', ""),
])
def test_back_context_survives_lazy_panel_search_link_and_unlink(
    editor_client, db, origin, key
):
    a = _item(db, "Novel")
    b = _item(db, "Novel audio", "audiobook")
    c = _item(db, "Novel ebook", "ebook")
    media_groups.link_items(db, a, b, link_type="format")
    db.commit()
    query = "?" + urlencode({"from": origin})
    suffix = f"?from={key}" if key else ""
    base = f"/api/related-media/items/{a}"

    detail = editor_client.get(f"/item/{a}{query}")
    assert f'hx-get="{base}/panel{suffix}"' in detail.text
    assert _format_block(detail.text).hrefs == [f"/item/{b}{suffix}"]

    panel = editor_client.get(f"{base}/panel{query}")
    assert f'hx-get="{base}/search{suffix}"' in panel.text
    assert f'hx-delete="{base}/links/{b}{suffix}"' in panel.text
    assert "hx-swap-oob" not in panel.text

    search = editor_client.get(f"{base}/search", params={"q": "Novel ebook", "from": origin})
    assert f'value="{c}"' in search.text
    assert f'hx-post="{base}/links{suffix}"' in search.text

    linked = editor_client.post(
        f"{base}/links{query}", data={"other_item_id": str(c), "link_type": "format"},
    )
    assert linked.status_code == 200
    assert f'hx-get="{base}/search{suffix}"' in linked.text
    assert f'hx-delete="{base}/links/{c}{suffix}"' in linked.text
    assert set(_format_block(linked.text, oob=True).hrefs) == {
        f"/item/{b}{suffix}", f"/item/{c}{suffix}",
    }

    unlinked = editor_client.delete(f"{base}/links/{c}{query}")
    assert unlinked.status_code == 200
    assert _format_block(unlinked.text, oob=True).hrefs == [f"/item/{b}{suffix}"]
    if not key:
        assert "?from=" not in panel.text + search.text + linked.text + unlinked.text


def test_format_refresh_keeps_translated_heading_and_media_label(editor_client, db, monkeypatch):
    monkeypatch.setenv("SHELF_I18N", "on")
    db.execute("UPDATE users SET language = 'it' WHERE username = 'editor'")
    a = _item(db, "Romanzo")
    b = _item(db, "Romanzo audio", "audiobook")
    db.commit()

    response = editor_client.post(
        f"/api/related-media/items/{a}/links",
        data={"other_item_id": str(b), "link_type": "format"},
    )

    assert response.status_code == 200
    refreshed = " ".join("".join(_format_block(response.text, oob=True).text).split())
    initial = " ".join("".join(_format_block(editor_client.get(f"/item/{a}").text).text).split())
    assert refreshed == initial == "Disponibile anche come: Audiolibro"
