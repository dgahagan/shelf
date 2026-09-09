"""Product smoke test for the fork-main RomM synced library."""

import sqlite3

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_romm_library_filters_by_provider_platform(live_server, authed_page):
    db_path = live_server["data_dir"] / "shelf.db"
    conn = sqlite3.connect(str(db_path))
    try:
        snes_item = conn.execute(
            "INSERT INTO items (title, media_type, source, owned, platform) "
            "VALUES ('RomM Catalogue SNES', 'video_game', 'romm', 1, 'snes')"
        ).lastrowid
        gba_item = conn.execute(
            "INSERT INTO items (title, media_type, source, owned, platform) "
            "VALUES ('RomM Catalogue GBA', 'video_game', 'romm', 1, 'gba')"
        ).lastrowid
        conn.execute(
            "INSERT INTO romm_records (romm_id, item_id, platform_id) VALUES (?, ?, ?)",
            ("e2e-rom-snes", snes_item, "e2e-platform-snes"),
        )
        conn.execute(
            "INSERT INTO romm_records (romm_id, item_id, platform_id) VALUES (?, ?, ?)",
            ("e2e-rom-gba", gba_item, "e2e-platform-gba"),
        )
        conn.commit()
    finally:
        conn.close()

    authed_page.goto(f"{live_server['url']}/romm/library")
    expect(authed_page.locator("body")).to_contain_text("RomM Library")
    expect(authed_page.locator("body")).to_contain_text("RomM Catalogue SNES")
    expect(authed_page.locator("body")).to_contain_text("RomM Catalogue GBA")

    with authed_page.expect_navigation():
        authed_page.locator("a[href*='platform=e2e-platform-snes']").click()
    expect(authed_page.locator("body")).to_contain_text("RomM Catalogue SNES")
    expect(authed_page.locator("body")).not_to_contain_text("RomM Catalogue GBA")
