"""Regression coverage for scanning an ISBN into the normal edit form."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_item_edit_isbn_camera_uses_rendered_ui_and_shared_scanner():
    template = (ROOT / "app/templates/item_edit.html").read_text(encoding="utf-8")
    source = (ROOT / "static/js/item_edit.js").read_text(encoding="utf-8")

    # The scanner UI belongs to the server-rendered edit template rather than
    # being assembled imperatively in JavaScript. Keep the existing shared
    # field macro so the edit form's save contract remains unchanged.
    assert 'x-data="isbnCamera"' in template
    assert 'field("isbn", "ISBN", item.isbn, placeholder="ISBN-13")' in template
    assert 'id="edit-isbn-camera-reader"' in template
    assert 'id="edit-isbn-zxing-video"' in template
    assert "document.createElement" not in source

    # Match the existing /scan page's vendored fallback chain and shared
    # scanner engine instead of introducing another camera implementation.
    assert '/static/vendor/html5-qrcode-2.3.8.min.js' in template
    assert '/static/vendor/zxing-browser-0.1.5.min.js' in template
    assert '/static/js/scanner-engine.js' in template
    assert "window.createBarcodeScanner" in source
    assert "qrbox: { width: 280, height: 100 }" in source
    assert "aspectRatio: 1.5" in source

    # A scan only fills the ISBN field. Normal save and server validation stay
    # authoritative, and non-Bookland barcodes remain rejected in the camera UI.
    assert "digits.length !== 13" in source
    assert "digits.slice(0, 3) !== '978'" in source
    assert "digits.slice(0, 3) !== '979'" in source
    assert "input.value = digits" in source
    assert ".submit(" not in source
    assert "fetch(" not in source
