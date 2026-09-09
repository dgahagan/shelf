from app.services import romm_catalog, romm_records


def _candidate(romm_id, platform_id, platform, title, *, publisher=None, year=None):
    return {
        "romm_id": romm_id,
        "romm_platform_id": platform_id,
        "title": title,
        "platform": platform,
        "platform_name": platform.upper(),
        "publisher": publisher,
        "publish_year": year,
        "description": None,
    }


def test_platform_summaries_keep_provider_platforms_separate(db):
    romm_records.persist_candidate(
        db, _candidate("rom-1", "p-snes", "snes", "Same Game")
    )
    romm_records.persist_candidate(
        db, _candidate("rom-2", "p-gba", "gba", "Same Game")
    )

    rows = romm_catalog.platform_summaries(db)

    assert {row["platform_id"] for row in rows} == {"p-snes", "p-gba"}
    assert sum(row["game_count"] for row in rows) == 2


def test_catalog_filter_and_search_do_not_merge_same_titles(db):
    romm_records.persist_candidate(
        db,
        _candidate(
            "rom-1", "p-snes", "snes", "Shared Title", publisher="Nintendo"
        ),
    )
    romm_records.persist_candidate(
        db,
        _candidate(
            "rom-2", "p-gba", "gba", "Shared Title", publisher="Nintendo"
        ),
    )
    romm_records.persist_candidate(
        db,
        _candidate("rom-3", "p-snes", "snes", "Other Game", publisher="Sega"),
    )

    rows, total = romm_catalog.fetch_page(
        db, platform_id="p-snes", query="Nintendo"
    )

    assert total == 1
    assert [row["romm_id"] for row in rows] == ["rom-1"]


def test_catalog_paginates_stably_by_title(db):
    for number, title in enumerate(["Gamma", "Alpha", "Beta"], start=1):
        romm_records.persist_candidate(
            db, _candidate(f"rom-{number}", "p-one", "snes", title)
        )

    first, total = romm_catalog.fetch_page(db, limit=2, offset=0)
    second, _ = romm_catalog.fetch_page(db, limit=2, offset=2)

    assert total == 3
    assert [row["title"] for row in first] == ["Alpha", "Beta"]
    assert [row["title"] for row in second] == ["Gamma"]


def test_catalogue_route_added_without_changing_romm_api_paths():
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/romm/library" in paths
    assert "/api/romm/status" in paths
    assert "/api/romm/sync/stream" in paths
    assert "/api/romm/items/{item_id}/action" in paths


def test_catalog_honours_library_visibility_predicate(db):
    visible = romm_records.persist_candidate(
        db, _candidate("rom-visible", "p-one", "snes", "Visible Game")
    )
    romm_records.persist_candidate(
        db, _candidate("rom-hidden", "p-one", "snes", "Hidden Game")
    )

    rows, total = romm_catalog.fetch_page(
        db,
        access_sql="i.id = ?",
        access_params=[visible["item_id"]],
    )
    summaries = romm_catalog.platform_summaries(
        db,
        access_sql="i.id = ?",
        access_params=[visible["item_id"]],
    )

    assert total == 1
    assert [row["title"] for row in rows] == ["Visible Game"]
    assert sum(row["game_count"] for row in summaries) == 1


def test_romm_settings_links_to_synced_library():
    from pathlib import Path

    html = Path("app/templates/fragments/settings/romm.html").read_text()
    assert 'href="/romm/library"' in html
    assert "Browse synced library" in html
