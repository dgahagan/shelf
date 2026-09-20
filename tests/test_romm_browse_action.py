from pathlib import Path

from app.database import get_db
from app.services import romm_records, romm_sync


CARD = Path(__file__).resolve().parents[1] / "app" / "templates" / "fragments" / "item_card.html"


def _seed_romm_item():
    with get_db() as conn:
        return romm_records.persist_candidate(conn, {
            "romm_id": "rom-browse-123",
            "romm_platform_id": "1",
            "title": "Chrono Trigger",
            "platform": "snes",
            "platform_name": "SNES",
        })["item_id"]


def test_romm_browse_card_has_a_separate_external_action(viewer_client, db):
    romm_sync.save_configuration(
        url="http://romm:8080", public_url="https://romm.example", token="rmm_secret"
    )
    item_id = _seed_romm_item()

    response = viewer_client.get("/browse?source_filter=romm")
    assert response.status_code == 200
    assert 'data-testid="romm-card-action"' in response.text
    assert f'href="/api/romm/items/{item_id}/open"' in response.text


def test_romm_browse_open_redirect_uses_browser_facing_root(viewer_client, db):
    romm_sync.save_configuration(
        url="http://romm:8080", public_url="https://romm.example", token="rmm_secret"
    )
    item_id = _seed_romm_item()

    response = viewer_client.get(
        f"/api/romm/items/{item_id}/open", follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["location"] == "https://romm.example/rom/rom-browse-123"


def test_romm_browse_open_degrades_to_item_page_when_action_is_unavailable(viewer_client):
    response = viewer_client.get("/api/romm/items/999999/open", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/item/999999"


def test_romm_action_is_a_sibling_of_the_normal_item_link():
    src = CARD.read_text()
    action_at = src.index('data-testid="romm-card-action"')
    normal_link_closes_at = src.index("</a>")
    assert src.startswith('<div class="relative">\n<a href="/item/{{ item.id }}"')
    assert action_at > normal_link_closes_at
    assert "{% if item.source == 'romm' %}" in src


def test_romm_open_route_is_registered():
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/api/romm/items/{item_id}/open" in paths
