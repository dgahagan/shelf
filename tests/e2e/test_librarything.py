"""LibraryThing settings and edition provenance, without external calls."""
import sqlite3

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item

pytestmark = pytest.mark.e2e


def test_librarything_token_write_only_card(live_server, authed_page):
    errors = []
    authed_page.on("pageerror", lambda error: errors.append(str(error)))
    authed_page.goto(f"{live_server['url']}/settings")
    authed_page.get_by_role("button", name="Integrations").click()
    token = authed_page.locator('input[name="librarything_token"]')
    expect(token).to_be_visible()
    token.fill("fake-e2e-librarything-token")
    token.locator("xpath=..").get_by_role("button", name="Save", exact=True).click()
    authed_page.get_by_role("button", name="Integrations").click()
    expect(token).to_have_value("")
    expect(authed_page.locator('input[name="clear_librarything_token"]')).to_be_visible()
    assert "fake-e2e-librarything-token" not in authed_page.content()
    token.scroll_into_view_if_needed()
    authed_page.screenshot(path="/tmp/shelf-librarything-settings.png")
    authed_page.locator('input[name="clear_librarything_token"]').check()
    token.locator("xpath=..").get_by_role("button", name="Save", exact=True).click()
    authed_page.get_by_role("button", name="Integrations").click()
    expect(authed_page.locator('input[name="clear_librarything_token"]')).to_have_count(0)
    assert errors == []


def test_english_cover_provenance_rendered(live_server, authed_page):
    item_id = insert_item(live_server['data_dir'], title="Atomi szokások", isbn="9786155420818", authors="James Clear", media_type="book")
    import struct
    import zlib
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixels = (b"\x00" + b"\x60\x55\xcc" * 200) * 300
    image = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 200, 300, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")
    (live_server['data_dir'] / "covers" / "example.jpg").write_bytes(image)
    with sqlite3.connect(live_server['data_dir'] / 'shelf.db') as db:
        db.execute("UPDATE items SET cover_path = ?, cover_edition_isbn = ?, cover_edition_language = ? WHERE id = ?", ("covers/example.jpg", "9780735211292", "en", item_id))
    authed_page.goto(f"{live_server['url']}/item/{item_id}")
    expect(authed_page.get_by_text("English cover from another edition", exact=True)).to_be_visible()
    authed_page.screenshot(path="/tmp/shelf-librarything-provenance.png")
