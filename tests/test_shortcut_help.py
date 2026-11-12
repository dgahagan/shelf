"""The shortcut-help controls are bound by id from app.js, not by inline onclick.

Inline handlers are refused by the script-src 'self' CSP, and the trigger's
translated title is not a stable selector.
"""
import pytest


@pytest.mark.parametrize("path", ["/browse", "/"])
def test_no_inline_onclick_anywhere(admin_client, path):
    r = admin_client.get(path)
    assert r.status_code == 200
    assert ' onclick="' not in r.text


def test_shortcut_controls_carry_ids(admin_client):
    html = admin_client.get("/browse").text
    assert 'id="shortcut-help-trigger"' in html
    assert 'id="shortcut-modal-close"' in html
    assert 'id="shortcut-modal"' in html


def test_trigger_keeps_english_title(admin_client):
    html = admin_client.get("/browse").text
    assert 'title="Keyboard shortcuts (?)"' in html
