"""Unit and schema regressions for legacy price-point book barcodes."""

import asyncio
import sqlite3

import pytest

from app.database import MIGRATIONS, _run_migrations
from app.services import provider_result
from app.services import upc as upc_svc
from app.services import legacy_book


KRISTY_UPC5 = "07807300350143506"
KRISTY_ISBN13 = "9780590435062"
KRISTY_OTHER_ISBN13 = "9780439435062"
KRISTY_EAN13_PLUS5 = "007807300350143506"


def _metadata(title: str) -> dict:
    return {"title": title, "authors": "Ann M. Martin"}


def test_legacy_parser_uses_the_current_upca_validator():
    assert upc_svc.validate_upc("036000291452")
    assert upc_svc.validate_upc("078073003501")
    assert not upc_svc.validate_upc("078073003502")


def test_real_scholastic_barcode_generates_only_evidence_backed_candidates():
    parsed = legacy_book.parse(KRISTY_UPC5)

    assert parsed is not None
    assert parsed.upc == "078073003501"
    assert parsed.supplement == "43506"
    assert parsed.isbn10_prefixes == ("0590", "0439")
    assert legacy_book.isbn13_candidates(KRISTY_UPC5) == (
        KRISTY_ISBN13,
        KRISTY_OTHER_ISBN13,
    )


def test_formatted_and_zero_padded_scans_share_one_identity():
    assert legacy_book.isbn13_candidates("0 78073 00350 1 + 43506") == (
        KRISTY_ISBN13,
        KRISTY_OTHER_ISBN13,
    )
    assert legacy_book.mapping_key(KRISTY_EAN13_PLUS5) == KRISTY_UPC5


@pytest.mark.parametrize(
    "raw",
    [
        "107807300350143506",  # arbitrary non-zero EAN-13 + 5
        "07807300350243506",  # invalid UPC-A check digit
        "03600029145243506",  # valid UPC, unsupported publisher prefix
    ],
)
def test_unsupported_or_invalid_inputs_fail_closed(raw):
    assert legacy_book.parse(raw) is None
    assert legacy_book.isbn13_candidates(raw) == ()


def test_mapping_table_rejects_noncanonical_values(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO legacy_book_mappings (barcode, isbn13) VALUES (?, ?)",
            ("07807300350143506", "978059043506X"),
        )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO legacy_book_mappings (barcode, isbn13) VALUES (?, ?)",
            ("0780730035014350X", KRISTY_ISBN13),
        )


def test_resolver_requires_one_verified_candidate_and_clean_misses_for_the_rest():
    async def lookup(isbn: str):
        if isbn == KRISTY_ISBN13:
            return (
                _metadata("Kristy and the Mother's Day Surprise"),
                "openlibrary",
                {},
                provider_result.found("openlibrary", {}),
            )
        return None, "manual", {}, provider_result.no_match("openlibrary")

    resolution = asyncio.run(legacy_book.resolve(KRISTY_UPC5, lookup))

    assert resolution.outcome == "found"
    assert resolution.isbn13 == KRISTY_ISBN13
    assert [match.isbn13 for match in resolution.matches] == [KRISTY_ISBN13]


def test_resolver_keeps_two_verified_candidates_ambiguous():
    async def lookup(isbn: str):
        metadata = _metadata(f"Candidate {isbn}")
        return metadata, "openlibrary", {}, provider_result.found("openlibrary", metadata)

    resolution = asyncio.run(legacy_book.resolve(KRISTY_UPC5, lookup))

    assert resolution.outcome == "ambiguous"
    assert [match.isbn13 for match in resolution.matches] == [
        KRISTY_ISBN13,
        KRISTY_OTHER_ISBN13,
    ]


@pytest.mark.parametrize(
    "failure",
    [
        provider_result.transport_failed("openlibrary"),
        provider_result.rate_limited("openlibrary"),
        provider_result.rejected("openlibrary", status=401),
    ],
    ids=["transport", "rate-limit", "rejected"],
)
def test_resolver_does_not_treat_provider_uncertainty_as_a_candidate_miss(failure):
    async def lookup(isbn: str):
        if isbn == KRISTY_ISBN13:
            metadata = _metadata("Kristy and the Mother's Day Surprise")
            return metadata, "openlibrary", {}, provider_result.found("openlibrary", metadata)
        return None, "manual", {}, failure

    resolution = asyncio.run(legacy_book.resolve(KRISTY_UPC5, lookup))

    assert resolution.outcome == "inconclusive"
    assert resolution.isbn13 is None
    assert [match.isbn13 for match in resolution.matches] == [KRISTY_ISBN13]


def test_resolver_does_not_swallow_cancellation():
    async def lookup(_isbn: str):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(legacy_book.resolve(KRISTY_UPC5, lookup))


def test_mapping_table_exists_in_a_fresh_database_and_migration_24_is_append_only(db):
    latest = max(version for version, _, _ in MIGRATIONS)
    assert latest == 24

    columns = {
        row[1] for row in db.execute("PRAGMA table_info(legacy_book_mappings)")
    }
    assert columns == {"barcode", "isbn13", "confirmed_at"}


def test_mapping_migration_upgrades_a_database_that_has_only_previous_versions(db):
    db.execute("DROP TABLE legacy_book_mappings")
    db.execute("DELETE FROM schema_version WHERE version = 24")
    db.commit()

    _run_migrations(db)
    db.commit()

    assert db.execute(
        "SELECT description FROM schema_version WHERE version = 24"
    ).fetchone()["description"] == "Add confirmed legacy book barcode mappings"
    assert {
        row[1] for row in db.execute("PRAGMA table_info(legacy_book_mappings)")
    } == {"barcode", "isbn13", "confirmed_at"}
