"""Focused regressions for the complete first-class Music surface."""

from pathlib import Path

from app.config import MEDIA_TYPES, MUSIC_MEDIA_TYPES
from app.routers import music


def test_music_family_is_first_class():
    assert MUSIC_MEDIA_TYPES == {
        "vinyl",
        "cassette",
        "cd",
        "digital_music",
        "music_other",
    }
    assert MUSIC_MEDIA_TYPES <= set(MEDIA_TYPES)


def test_music_router_exposes_browse_add_detail_and_refresh():
    paths = {(route.path, ",".join(sorted(route.methods or []))) for route in music.router.routes}
    assert ("/music", "GET") in paths
    assert ("/api/music/add", "POST") in paths
    assert ("/music/item/{item_id}", "GET") in paths
    assert ("/api/music/items/{item_id}/refresh", "POST") in paths


def test_musicbrainz_formats_map_conservatively():
    assert music._infer_media_type({"media": [{"format": "12\" Vinyl"}]}) == "vinyl"
    assert music._infer_media_type({"media": [{"format": "Cassette"}]}) == "cassette"
    assert music._infer_media_type({"media": [{"format": "CD"}]}) == "cd"
    assert music._infer_media_type({"media": [{"format": "Digital Media"}]}) == "digital_music"
    assert music._infer_media_type({"media": [{"format": "MiniDisc"}]}) == "music_other"


def test_music_templates_keep_exact_release_workflow_visible():
    root = Path(__file__).parents[1] / "app" / "templates"
    library = (root / "music.html").read_text()
    detail = (root / "music_item.html").read_text()

    assert 'action="/api/music/add"' in library
    assert "Search MusicBrainz" in library
    assert 'href="/music/item/{{ item.id }}"' in library
    assert 'action="/api/music/items/{{ item.id }}/refresh"' in detail
    assert "Track listing" in detail
    assert "musicbrainz.org/release/" in detail
