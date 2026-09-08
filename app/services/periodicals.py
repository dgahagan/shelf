"""Helpers for ISSN-based periodical barcodes.

Consumer magazines and other serials commonly use an EAN-13 beginning 977.
The seven digits after that prefix are the first seven digits of the ISSN;
the ISSN check character is reconstructed independently from the EAN check
character. The two digits before the EAN check digit are the serial variant
value and must not be treated as an issue number.

A 977 carrier may be followed by a 2- or 5-digit add-on. The add-on is useful
issue-discriminator data, but its interpretation is publisher/cadence-specific,
so Shelf preserves it verbatim rather than guessing an issue number or date.
"""

from dataclasses import dataclass

from app.services.upc import normalize_barcode


@dataclass(frozen=True, slots=True)
class PeriodicalBarcode:
    """Decoded information from a 977 serial carrier and optional add-on."""

    ean13: str
    issn: str
    variant: str
    supplement: str | None = None

    @property
    def full_code(self) -> str:
        return self.ean13 + (self.supplement or "")


def _valid_ean13(code: str) -> bool:
    if len(code) != 13 or not code.isdigit():
        return False
    total = sum(
        int(digit) * (3 if index % 2 else 1)
        for index, digit in enumerate(code[:12])
    )
    check = (10 - (total % 10)) % 10
    return check == int(code[-1])


def issn_from_seven_digits(stem: str) -> str | None:
    """Return the formatted ISSN for a seven-digit ISSN stem."""
    if len(stem) != 7 or not stem.isdigit():
        return None

    weighted = sum(
        int(digit) * weight
        for digit, weight in zip(stem, range(8, 1, -1))
    )
    value = (11 - (weighted % 11)) % 11
    check = "X" if value == 10 else str(value)
    return f"{stem[:4]}-{stem[4:]}{check}"


def parse_barcode(raw: str) -> PeriodicalBarcode | None:
    """Decode a valid 977 EAN-13 with an optional 2/5-digit add-on.

    Camera decoding commonly returns only the 13-digit carrier, while some
    USB/keyboard scanners concatenate the add-on. Accept both without
    assigning publisher-specific meaning to the supplement.
    """
    code = normalize_barcode(raw)
    if len(code) not in (13, 15, 18):
        return None

    carrier = code[:13]
    supplement = code[13:] or None
    if not carrier.startswith("977") or not _valid_ean13(carrier):
        return None
    if supplement is not None and len(supplement) not in (2, 5):
        return None

    issn = issn_from_seven_digits(carrier[3:10])
    if not issn:
        return None
    return PeriodicalBarcode(
        ean13=carrier,
        issn=issn,
        variant=carrier[10:12],
        supplement=supplement,
    )
