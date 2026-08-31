"""Tests for app.services.isbn and app.services.upc — pure validation logic."""

import pytest

from app.services.isbn import (
    normalize_isbn,
    isbn10_to_isbn13,
    to_isbn13,
    isbn13_to_isbn10,
    validate_isbn10,
    validate_isbn13,
    canonical_isbn_pair,
)
from app.services.upc import normalize_barcode, detect_barcode_type, validate_upc


# --- ISBN ---


class TestNormalizeIsbn:
    def test_strips_hyphens(self):
        assert normalize_isbn("978-0-13-468599-1") == "9780134685991"

    def test_strips_spaces(self):
        assert normalize_isbn("978 0 13 468599 1") == "9780134685991"

    def test_preserves_x(self):
        assert normalize_isbn("080442957x") == "080442957X"


class TestIsbn10ToIsbn13:
    def test_valid_conversion(self):
        assert isbn10_to_isbn13("0804429573") == "9780804429573"

    def test_rejects_wrong_length(self):
        assert isbn10_to_isbn13("123") is None

    def test_the_hobbit(self):
        assert isbn10_to_isbn13("054792822X") == "9780547928227"


class TestIsbn13ToIsbn10:
    def test_valid_conversion(self):
        result = isbn13_to_isbn10("9780804429573")
        assert result == "080442957X"

    def test_rejects_non_978_prefix(self):
        assert isbn13_to_isbn10("9790000000000") is None

    def test_rejects_wrong_length(self):
        assert isbn13_to_isbn10("978") is None

    def test_check_digit_x(self):
        result = isbn13_to_isbn10("9780074625422")
        assert result == "007462542X"


class TestToIsbn13:
    def test_isbn13_passthrough(self):
        assert to_isbn13("9780134685991") == "9780134685991"

    def test_isbn10_conversion(self):
        assert to_isbn13("0804429573") == "9780804429573"

    def test_isbn_with_hyphens(self):
        assert to_isbn13("978-0-13-468599-1") == "9780134685991"

    def test_upc_12_digit_prepends_zero(self):
        assert to_isbn13("012345678905") == "0012345678905"

    def test_non_isbn_ean13_keeps_legacy_passthrough(self):
        assert to_isbn13("4006381333931") == "4006381333931"

    def test_invalid_returns_none(self):
        assert to_isbn13("invalid") is None
        assert to_isbn13("12345") is None


class TestValidateIsbn10:
    def test_valid_isbn10(self):
        assert validate_isbn10("0441172717") is True

    def test_valid_isbn10_with_x_check_digit(self):
        assert validate_isbn10("054792822X") is True

    def test_x_only_valid_in_check_digit_position(self):
        assert validate_isbn10("04411X2717") is False

    def test_wrong_length_rejected(self):
        assert validate_isbn10("044117271") is False
        assert validate_isbn10("04411727171") is False

    def test_bad_check_digit_rejected(self):
        assert validate_isbn10("0441172718") is False

    def test_none_returns_false(self):
        assert validate_isbn10(None) is False

    def test_empty_string_returns_false(self):
        assert validate_isbn10("") is False

    def test_non_string_returns_false(self):
        assert validate_isbn10(123) is False


class TestValidateIsbn13:
    def test_valid_isbn13_978(self):
        assert validate_isbn13("9780441172719") is True

    def test_valid_isbn13_from_isbn10_conversion(self):
        assert validate_isbn13("9780547928227") is True

    def test_valid_isbn13_979_prefix(self):
        assert validate_isbn13("9791234567896") is True

    def test_transposition_rejected(self):
        assert validate_isbn13("9780441172791") is False

    def test_single_digit_slip_rejected(self):
        assert validate_isbn13("9780441172710") is False

    def test_977_prefix_rejected(self):
        assert validate_isbn13("9770441172718") is False

    def test_wrong_length_rejected(self):
        assert validate_isbn13("978044117271") is False
        assert validate_isbn13("97804411727191") is False

    def test_none_returns_false(self):
        assert validate_isbn13(None) is False

    def test_empty_string_returns_false(self):
        assert validate_isbn13("") is False

    def test_non_string_returns_false(self):
        assert validate_isbn13(123) is False


class TestCanonicalIsbnPair:
    def test_isbn10_returns_matching_pair(self):
        assert canonical_isbn_pair("054792822X") == ("9780547928227", "054792822X")

    def test_isbn13_returns_matching_isbn10(self):
        assert canonical_isbn_pair("9780441172719") == ("9780441172719", "0441172717")

    def test_979_has_no_isbn10_equivalent(self):
        assert canonical_isbn_pair("9791234567896") == ("9791234567896", None)

    def test_normalizes_formatting(self):
        assert canonical_isbn_pair("978-0-54-792822-7") == ("9780547928227", "054792822X")

    def test_rejects_bad_checksum(self):
        assert canonical_isbn_pair("9780441172710") is None
        assert canonical_isbn_pair("0441172718") is None

    def test_rejects_upc_even_though_legacy_converter_accepts_it(self):
        assert canonical_isbn_pair("012345678905") is None

    def test_blank_or_non_string_is_invalid(self):
        assert canonical_isbn_pair("") is None
        assert canonical_isbn_pair(None) is None


# --- UPC ---


class TestDetectBarcodeType:
    def test_isbn10(self):
        assert detect_barcode_type("0804429573") == "isbn"

    def test_isbn13(self):
        assert detect_barcode_type("9780134685991") == "isbn"

    def test_isbn13_979(self):
        assert detect_barcode_type("9791032305690") == "isbn"

    def test_upc_12(self):
        assert detect_barcode_type("012345678905") == "upc"

    def test_ean13_non_isbn(self):
        assert detect_barcode_type("4006381333931") == "upc"

    def test_unknown(self):
        assert detect_barcode_type("12345") == "unknown"


class TestValidateUpc:
    def test_valid_upc(self):
        assert validate_upc("012345678905") is True

    def test_invalid_check_digit(self):
        assert validate_upc("012345678900") is False

    def test_wrong_length(self):
        assert validate_upc("12345") is False

    def test_non_numeric(self):
        assert validate_upc("abcdefghijkl") is False
