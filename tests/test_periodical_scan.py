from unittest.mock import AsyncMock, patch

import pytest

from app.services import periodical_scan, provider_result


POPULAR_SCIENCE_EAN = "9770161737008"


@pytest.mark.asyncio
async def test_invalid_barcode_never_calls_issn_provider():
    lookup = AsyncMock()
    with patch("app.services.periodical_scan.issn_portal.lookup", new=lookup):
        result = await periodical_scan.resolve_barcode("4006381333931", object())

    assert result.outcome == "no_match"
    assert result.provider == "periodical_barcode"
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_977_scan_resolves_publication_but_not_issue_identity():
    lookup = AsyncMock(return_value=provider_result.found("issn_portal", {
        "title": "Popular Science",
        "publisher": "Bonnier",
        "language": "en",
        "issn": "0161-7370",
    }))

    with patch("app.services.periodical_scan.issn_portal.lookup", new=lookup):
        result = await periodical_scan.resolve_barcode(
            POPULAR_SCIENCE_EAN + "05", object()
        )

    assert result.found
    assert result.payload["publication_title"] == "Popular Science"
    assert result.payload["issn"] == "0161-7370"
    assert result.payload["barcode_ean"] == POPULAR_SCIENCE_EAN
    assert result.payload["barcode_supplement"] == "05"
    assert result.payload["barcode_variant"] == "00"
    assert result.payload["issue_number"] is None
    assert result.payload["issue_date"] is None
    assert result.payload["requires_issue_confirmation"] is True
    lookup.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_failure_keeps_decoded_barcode_for_manual_confirmation():
    lookup = AsyncMock(return_value=provider_result.transport_failed("issn_portal"))

    with patch("app.services.periodical_scan.issn_portal.lookup", new=lookup):
        result = await periodical_scan.resolve_barcode(POPULAR_SCIENCE_EAN, object())

    assert result.outcome == "transport_failed"
    assert result.payload["issn"] == "0161-7370"
    assert result.payload["publication_title"] is None
    assert result.payload["requires_issue_confirmation"] is True


def test_attach_issue_uses_user_confirmed_issue_fields_without_inferring_supplement(db):
    item_id = db.execute(
        "INSERT INTO items (title, media_type, owned) VALUES (?, 'magazine', 1)",
        ("Popular Science — March 2026",),
    ).lastrowid
    candidate = {
        "publication_title": "Popular Science",
        "publisher": "Bonnier",
        "language": "en",
        "issn": "0161-7370",
        "barcode_ean": POPULAR_SCIENCE_EAN,
        "barcode_supplement": "05",
    }

    publication_id = periodical_scan.attach_issue_identity(
        db,
        item_id=item_id,
        candidate=candidate,
        issue_number="3",
        volume="154",
        issue_date="2026-03-01",
        cover_date_label="March 2026",
    )

    publication = db.execute(
        "SELECT * FROM periodical_publications WHERE id = ?", (publication_id,)
    ).fetchone()
    issue = db.execute(
        "SELECT * FROM periodical_issues WHERE item_id = ?", (item_id,)
    ).fetchone()

    assert publication["title"] == "Popular Science"
    assert publication["issn"] == "0161-7370"
    assert issue["issue_number"] == "3"
    assert issue["volume"] == "154"
    assert issue["barcode_supplement"] == "05"
    assert issue["issue_number"] != issue["barcode_supplement"]


def test_attach_issue_requires_publication_title_when_lookup_did_not_find_one(db):
    item_id = db.execute(
        "INSERT INTO items (title, media_type, owned) VALUES (?, 'magazine', 1)",
        ("Unknown issue",),
    ).lastrowid

    with pytest.raises(ValueError, match="publication title"):
        periodical_scan.attach_issue_identity(
            db,
            item_id=item_id,
            candidate={
                "issn": "0161-7370",
                "barcode_ean": POPULAR_SCIENCE_EAN,
                "publication_title": None,
            },
        )
