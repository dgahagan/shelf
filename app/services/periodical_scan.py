"""Resolve periodical barcodes into publication-level scan candidates.

A 977 carrier identifies the serial publication through its ISSN. It does not
universally identify a concrete issue. Shelf therefore resolves the publication
first, preserves the carrier/add-on exactly, and leaves issue number/date for a
confirmation step rather than guessing publisher-specific semantics.
"""

from __future__ import annotations

import httpx

from app.services import issn_portal, periodical_records, periodicals, provider_result


def _scan_payload(serial: periodicals.PeriodicalBarcode, metadata: dict | None = None) -> dict:
    metadata = metadata or {}
    title = metadata.get("title")
    return {
        "media_type": "magazine",
        "title": title,
        "publication_title": title,
        "publisher": metadata.get("publisher"),
        "language": metadata.get("language"),
        "issn": serial.issn,
        "barcode_ean": serial.ean13,
        "barcode_supplement": serial.supplement,
        "barcode_variant": serial.variant,
        "issue_number": None,
        "issue_date": None,
        "requires_issue_confirmation": True,
    }


async def resolve_barcode(
    raw: str,
    client: httpx.AsyncClient,
) -> provider_result.ProviderResult:
    """Resolve a valid 977 barcode to its continuing publication.

    Invalid/non-periodical barcodes return ``no_match`` without making an
    outbound request. Provider failures retain the decoded barcode payload so
    the scan UI can still show the ISSN and let the user enter a publication
    title manually. A metadata hit adds the publication title/publisher but
    deliberately leaves issue identity blank.
    """
    serial = periodicals.parse_barcode(raw)
    if serial is None:
        return provider_result.no_match("periodical_barcode")

    result = await issn_portal.lookup(serial.issn, client)
    if result.found:
        metadata = result.payload if isinstance(result.payload, dict) else {}
        return result.with_payload(_scan_payload(serial, metadata))
    return result.with_payload(_scan_payload(serial))


def attach_issue_identity(
    db,
    *,
    item_id: int,
    candidate: dict,
    publication_title: str | None = None,
    issue_number: str | None = None,
    volume: str | None = None,
    issue_date: str | None = None,
    cover_date_label: str | None = None,
) -> int:
    """Persist a confirmed scan candidate against an existing Shelf item.

    The caller must supply/confirm the publication title if lookup did not find
    one. Barcode supplement/variant values are never promoted to issue number.
    Returns the publication id for follow-up navigation.
    """
    title = (publication_title or candidate.get("publication_title") or "").strip()
    if not title:
        raise ValueError("Periodical publication title is required")

    publication_id = periodical_records.upsert_publication(
        db,
        title=title,
        issn=candidate.get("issn"),
        publisher=candidate.get("publisher"),
        language=candidate.get("language"),
    )
    periodical_records.link_issue(
        db,
        item_id=item_id,
        publication_id=publication_id,
        volume=volume,
        issue_number=issue_number,
        issue_date=issue_date,
        barcode_ean=candidate.get("barcode_ean"),
        barcode_supplement=candidate.get("barcode_supplement"),
        cover_date_label=cover_date_label,
    )
    return publication_id
