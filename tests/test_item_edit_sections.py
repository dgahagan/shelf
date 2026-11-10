from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_item_edit_is_sectioned_without_changing_the_save_contract():
    template = (ROOT / "app/templates/item_edit.html").read_text(encoding="utf-8")
    script = (ROOT / "static/js/item_edit.js").read_text(encoding="utf-8")

    assert 'data-testid="edit-section-nav"' in template
    for section in ("general", "artwork", "series", "identifiers", "location", "media", "tags"):
        assert f'id="edit-{section}"' in template
        assert f'data-testid="edit-section-{section}"' in template

    # The tags island sits outside the save form (it carries its own
    # hx-post/hx-delete controls; nesting forms is invalid HTML).
    assert template.index('id="edit-tags"') > template.rindex('</form>')
    assert 'href="#edit-tags"' in template

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
        "duration_mins", "asin", "date_started", "date_finished",
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
    assert 'data-media-types="book audiobook ebook comic manga"' in template
    assert 'data-media-types="video_game audiobook"' in template
    assert "updateEditSectionVisibility" in script
    assert "mediaSelect.addEventListener('change'" in script
    assert "kids_book" not in template

    # Artwork exposes the existing safe manual-cover URL route directly from
    # Edit without nesting another form inside the item-save form. The HTMX
    # action applies only the cover and deliberately stays on the edit page so
    # unsaved metadata remains in the DOM.
    assert 'id="edit-cover-url"' in template
    assert 'data-testid="edit-cover-url-submit"' in template
    assert 'hx-post="/api/items/{{ item.id }}/cover-url"' in template
    assert 'hx-include="#edit-cover-url"' in template
    assert 'hx-vals=\'{"return_to":"edit"}\'' in template
