from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services import item_write, periodical_google, periodicals, provider_result


BARCODE = "977016173700805"
SCAN_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "templates"
    / "fragments"
    / "periodical_scan_result.html"
)


def test_google_periodical_parser_rejects_books_and_keeps_issue_fields():
    assert periodical_google._issue_from_item({
        "id": "book-id",
        "volumeInfo": {"printType": "BOOK", "title": "Not a magazine"},
    }) is None

    issue = periodical_google._issue_from_item({
        "id": "mag-1",
        "volumeInfo": {
            "printType": "MAGAZINE",
            "title": "Popular Science",
            "publisher": "Bonnier",
            "publishedDate": "2026-03-01",
            "language": "en",
            "industryIdentifiers": [{"type": "ISSN", "identifier": "2049-3630"}],
        },
    })
    assert issue["google_volume_id"] == "mag-1"
    assert issue["title"] == "Popular Science"
    assert issue["issue_date"] == "2026-03-01"
    assert issue["issn"] == "2049-3630"


def test_confirmed_issn_is_checksum_validated():
    assert periodicals.normalise_issn("20493630") == "2049-3630"
    try:
        periodicals.normalise_issn("2049-3631")
    except ValueError as exc:
        assert "check digit" in str(exc)
    else:
        raise AssertionError("bad ISSN check digit was accepted")


def test_assisted_routes_are_registered_through_main():
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/api/periodicals/assist/search" in paths
    assert "/api/periodicals/assist/select" in paths


def test_assisted_search_preserves_scanned_barcode(admin_client):
    found = provider_result.found("google", [{
        "google_volume_id": "candidate-1",
        "title": "Popular Science",
        "publisher": "Bonnier",
        "issue_date": "2026-03-01",
        "issn": "2049-3630",
        "cover_url": "https://books.google.com/preview.jpg",
    }])
    with patch(
        "app.routers.periodicals.periodical_google.search_issues",
        new=AsyncMock(return_value=found),
    ):
        response = admin_client.get(
            "/api/periodicals/assist/search",
            params={"q": "Popular Science", "raw_barcode": BARCODE, "mode": "add"},
        )

    assert response.status_code == 200
    assert "Popular Science" in response.text
    assert BARCODE in response.text
    assert "candidate-1" in response.text
    assert "https://books.google.com/preview.jpg" in response.text


def test_invalid_assisted_search_is_visible_to_htmx(admin_client):
    response = admin_client.get(
        "/api/periodicals/assist/search",
        params={"q": "Popular Science", "raw_barcode": "not-a-barcode"},
    )

    assert response.status_code == 200
    assert "valid periodical barcode" in response.text


def test_selecting_candidate_refills_confirmation_without_losing_addon(admin_client):
    selected = provider_result.found("google", {
        "google_volume_id": "candidate-1",
        "title": "Popular Science",
        "publisher": "Bonnier",
        "issue_date": "2026-03-01",
        "issn": "2049-3630",
        "language": "en",
    })
    with patch(
        "app.routers.periodicals.periodical_google.lookup_issue",
        new=AsyncMock(return_value=selected),
    ):
        response = admin_client.get(
            "/api/periodicals/assist/select",
            params={"volume_id": "candidate-1", "raw_barcode": BARCODE, "mode": "add"},
        )

    assert response.status_code == 200
    assert 'name="raw_barcode" value="' + BARCODE + '"' in response.text
    assert 'name="publication_issn" value="2049-3630"' in response.text
    assert 'action="/api/periodicals/confirm"' in response.text
    assert "+ 05" in response.text
    assert "2026-03-01" in response.text


def test_disappeared_selected_result_retargets_visible_message(admin_client):
    with patch(
        "app.routers.periodicals.periodical_google.lookup_issue",
        new=AsyncMock(return_value=provider_result.no_match("google")),
    ):
        response = admin_client.get(
            "/api/periodicals/assist/select",
            params={"volume_id": "candidate-1", "raw_barcode": BARCODE},
        )

    assert response.status_code == 200
    assert response.headers["HX-Retarget"] == f"#periodical-assisted-{BARCODE}"
    assert response.headers["HX-Reswap"] == "innerHTML"
    assert "no longer available" in response.text


def test_bad_provider_issn_falls_back_to_scanned_carrier_hint(admin_client):
    selected = provider_result.found("google", {
        "google_volume_id": "candidate-1",
        "title": "Popular Science",
        "issn": "2049-3631",
    })
    with patch(
        "app.routers.periodicals.periodical_google.lookup_issue",
        new=AsyncMock(return_value=selected),
    ):
        response = admin_client.get(
            "/api/periodicals/assist/select",
            params={"volume_id": "candidate-1", "raw_barcode": BARCODE},
        )

    serial = periodicals.parse_barcode(BARCODE)
    assert response.status_code == 200
    assert serial is not None
    assert f'name="publication_issn" value="{serial.issn}"' in response.text


def test_assisted_confirm_can_override_barcode_hint_issn(admin_client, db):
    response = admin_client.post(
        "/api/periodicals/confirm",
        data={
            "raw_barcode": BARCODE,
            "publication_title": "Popular Science",
            "publication_issn": "2049-3630",
            "publisher": "Bonnier",
            "issue_number": "3",
            "issue_date": "2026-03-01",
            "mode": "add",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    publication = db.execute(
        "SELECT title, issn FROM periodical_publications WHERE title = ?",
        ("Popular Science",),
    ).fetchone()
    issue = db.execute(
        "SELECT barcode_ean, barcode_supplement FROM periodical_issues ORDER BY item_id DESC LIMIT 1"
    ).fetchone()
    assert publication["issn"] == "2049-3630"
    assert issue["barcode_ean"] == BARCODE[:13]
    assert issue["barcode_supplement"] == "05"


def test_assisted_confirm_preserves_0451_trash_restore(admin_client, db):
    first = admin_client.post(
        "/api/periodicals/confirm",
        data={
            "raw_barcode": BARCODE,
            "publication_title": "Private Eye",
            "issue_number": "1600",
            "issue_date": "2026-03-01",
            "mode": "add",
        },
        follow_redirects=False,
    )
    assert first.status_code == 303
    item_id = db.execute(
        "SELECT item_id FROM periodical_issues ORDER BY item_id DESC LIMIT 1"
    ).fetchone()["item_id"]
    item_write.trash_item(db, item_id)
    db.commit()

    second = admin_client.post(
        "/api/periodicals/confirm",
        data={
            "raw_barcode": BARCODE,
            "publication_title": "Private Eye",
            "issue_number": "1600",
            "issue_date": "2026-03-01",
            "mode": "add",
        },
        follow_redirects=False,
    )

    assert second.status_code == 303
    assert second.headers["location"] == f"/item/{item_id}"
    row = db.execute("SELECT deleted_at FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["deleted_at"] is None
    assert db.execute(
        "SELECT COUNT(*) FROM periodical_issues WHERE item_id = ?", (item_id,)
    ).fetchone()[0] == 1


def test_invalid_confirmed_issn_refuses_write(admin_client, db):
    response = admin_client.post(
        "/api/periodicals/confirm",
        data={
            "raw_barcode": BARCODE,
            "publication_title": "Popular Science",
            "publication_issn": "2049-3631",
            "mode": "add",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "check digit" in response.text
    assert db.execute("SELECT COUNT(*) FROM periodical_publications").fetchone()[0] == 0


def test_periodical_assist_has_no_always_on_marketplace_link():
    source = SCAN_TEMPLATE.read_text()
    assert "google.com/search" in source
    assert "ebay." not in source.casefold()
