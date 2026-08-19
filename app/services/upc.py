"""UPC/EAN barcode detection and validation."""

import re


def normalize_barcode(raw: str) -> str:
    return re.sub(r"[^0-9]", "", raw.strip())


def normalize_upc(raw: str) -> str:
    """Canonical storage form for a UPC/EAN barcode: EAN-13.

    UPC-A is EAN-13 with a leading zero, so the same disc scanned as a
    12-digit UPC-A and as a 13-digit EAN-13 has to land on one value or the
    duplicate check silently misses. Everything is stored padded to 13.
    Idempotent — passing an already-canonical code back through is a no-op.

    This is the *storage* form only. External lookups (UPC Item DB, TMDb)
    still get the barcode as scanned.
    """
    code = normalize_barcode(raw)
    if len(code) == 12:
        code = "0" + code
    return code


def detect_barcode_type(code: str) -> str:
    """Detect barcode type: 'isbn', 'upc', or 'unknown'."""
    code = normalize_barcode(code)

    if len(code) == 10:
        return "isbn"  # ISBN-10
    if len(code) == 13:
        if code.startswith("978") or code.startswith("979"):
            return "isbn"
        return "upc"  # EAN-13 (non-ISBN)
    if len(code) == 12:
        return "upc"  # UPC-A
    return "unknown"


def validate_upc(code: str) -> bool:
    """Validate a UPC-A (12-digit) check digit."""
    code = normalize_barcode(code)
    if len(code) != 12 or not code.isdigit():
        return False
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(code[:11]))
    check = (10 - (total % 10)) % 10
    return int(code[11]) == check
