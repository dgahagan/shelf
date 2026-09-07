from pathlib import Path

from app.routers import item_barcode_edit


UPC_A = "078073003501"
EAN_13 = "4006381333931"


def _item(db, *, title="Disc", media_type="dvd", upc=None):
    return db.execute(
        "INSERT INTO items (title, media_type, owned, upc) VALUES (?, ?, 1, ?)",
        (title, media_type, upc),
    ).lastrowid


def test_editable_upc_canonicalises_upca_to_ean13():
    ok, value = item_barcode_edit._canonical_upc(UPC_A)
    assert ok is True
    assert value == "0" + UPC_A


def test_editable_upc_accepts_valid_non_book_ean_and_rejects_isbn():
    assert item_barcode_edit._canonical_upc(EAN_13) == (True, EAN_13)
    assert item_barcode_edit._canonical_upc("9780306406157") == (False, None)
    assert item_barcode_edit._canonical_upc("4006381333932") == (False, None)


def test_barcode_context_exposes_existing_upc(editor_client, db):
    item_id = _item(db, upc=EAN_13)
    db.commit()
    response = editor_client.get(f"/api/items/{item_id}/barcode-context")
    assert response.status_code == 200
    assert response.json()["upc"] == EAN_13


def test_item_edit_can_save_upc_without_metadata_lookup(editor_client, db):
    item_id = _item(db)
    db.commit()
    response = editor_client.post(
        f"/api/items/{item_id}",
        data={"upc": UPC_A},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    row = db.execute("SELECT upc FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["upc"] == "0" + UPC_A


def test_item_edit_refuses_same_upc_on_same_media_type(editor_client, db):
    _item(db, title="Existing", upc=EAN_13)
    item_id = _item(db, title="Other")
    db.commit()
    response = editor_client.post(
        f"/api/items/{item_id}",
        data={"media_type": "dvd", "upc": EAN_13},
        follow_redirects=False,
    )
    assert response.status_code == 409
    row = db.execute("SELECT upc FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["upc"] is None


def test_upc_camera_is_scan_only_and_reuses_shared_engine():
    template = Path("app/templates/item_edit.html").read_text()
    source = Path("static/js/item_edit.js").read_text()
    assert "data-upc-editor" in template
    assert 'x-data="upcCamera"' in template
    assert 'field("upc", "UPC / EAN", item.upc' in template
    assert 'id="edit-upc-camera-reader"' in template
    assert 'id="edit-upc-zxing-video"' in template
    assert "window.createBarcodeScanner" in source
    assert "edit-upc-camera-reader" in source
    assert "input.value = canonical" in source
    assert "document.createElement" not in source
    assert "fetch(" not in source
    # The camera handler must not submit or call a metadata route. Saving stays
    # the user's explicit action on the existing item-edit form.
    camera_tail = source.split("function upcCamera", 1)[1]
    assert ".submit()" not in camera_tail
    assert "/api/scan" not in camera_tail
