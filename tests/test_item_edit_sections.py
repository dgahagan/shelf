from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_item_edit_is_sectioned_without_changing_the_save_contract():
    template = (ROOT / "app/templates/item_edit.html").read_text(encoding="utf-8")
    script = (ROOT / "static/js/item_edit.js").read_text(encoding="utf-8")

    assert 'data-testid="edit-section-nav"' in template
    for section in ("general", "artwork", "series", "identifiers", "location", "media"):
        assert f'id="edit-{section}"' in template
        assert f'data-testid="edit-section-{section}"' in template

    # This is still the existing item edit form: sectioning must not create
    # another persistence path or make navigation itself save anything.
    assert 'action="/api/items/{{ item.id }}"' in template
    assert 'method="post"' in template
    assert 'enctype="multipart/form-data"' in template
    assert 'data-testid="save-btn"' in template

    # Text/number/date fields are rendered by the existing Jinja macro, so
    # assert the macro calls rather than literal rendered name attributes.
    for field in (
        "title", "subtitle", "authors", "publisher", "publish_year", "isbn",
        "page_count", "series_name", "series_position", "narrator",
        "duration_mins", "date_started", "date_finished",
    ):
        assert f'field("{field}"' in template

    # These controls are written directly in the template.
    for field in (
        "media_type", "location_id", "owned", "language", "manual_value",
        "platform", "reading_status", "description", "notes", "cover",
    ):
        assert f'name="{field}"' in template

    # Media-specific groups stay in the DOM so changing Media Type can reveal
    # them immediately without changing what the form submits.
    assert 'data-media-types="book kids_book audiobook ebook comic"' in template
    assert 'data-media-types="video_game audiobook"' in template
    assert "updateEditSectionVisibility" in script
    assert "mediaSelect.addEventListener('change'" in script
