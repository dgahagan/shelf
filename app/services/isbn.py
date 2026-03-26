import re


def normalize_isbn(isbn: str) -> str:
    return re.sub(r"[^0-9X]", "", isbn.upper())


def isbn10_to_isbn13(isbn10: str) -> str | None:
    if len(isbn10) != 10:
        return None
    digits = "978" + isbn10[:9]
    check = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits))
    check = (10 - (check % 10)) % 10
    return digits + str(check)


def to_isbn13(raw: str) -> str | None:
    isbn = normalize_isbn(raw)
    # UPC-A (12 digits) -> EAN-13 by prepending 0
    if len(isbn) == 12 and isbn.isdigit():
        isbn = "0" + isbn
    if len(isbn) == 13 and isbn.isdigit():
        return isbn
    if len(isbn) == 10:
        return isbn10_to_isbn13(isbn)
    return None


def isbn13_to_isbn10(isbn13: str) -> str | None:
    if len(isbn13) != 13 or not isbn13.startswith("978"):
        return None
    body = isbn13[3:12]
    total = sum(int(d) * (10 - i) for i, d in enumerate(body))
    check = (11 - (total % 11)) % 11
    check_char = "X" if check == 10 else str(check)
    return body + check_char
