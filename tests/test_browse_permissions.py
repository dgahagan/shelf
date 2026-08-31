"""Role-aware Browse bulk-action rendering."""


def test_viewer_has_no_bulk_selection_controls(viewer_client):
    response = viewer_client.get("/browse")
    assert response.status_code == 200
    html = response.text

    assert 'data-can-select="0"' in html
    assert "Delete Selected" not in html
    assert "Move to location..." not in html
    assert 'x-model="bulkTypeVal"' not in html
    assert 'id="bulk-series"' not in html


def test_editor_can_bulk_delete_but_not_bulk_edit(editor_client):
    response = editor_client.get("/browse")
    assert response.status_code == 200
    html = response.text

    assert 'data-can-select="1"' in html
    assert "Delete Selected" in html
    assert "Move to location..." not in html
    assert 'x-model="bulkTypeVal"' not in html
    assert 'id="bulk-series"' not in html
    assert "Tap items to delete them in bulk." in html


def test_admin_gets_bulk_edit_and_delete_controls(admin_client):
    response = admin_client.get("/browse")
    assert response.status_code == 200
    html = response.text

    assert 'data-can-select="1"' in html
    assert "Delete Selected" in html
    assert "Move to location..." in html
    assert 'x-model="bulkTypeVal"' in html
    assert 'id="bulk-series"' in html
    assert "Tap items to bulk edit or delete them." in html
