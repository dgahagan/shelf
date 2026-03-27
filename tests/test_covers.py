"""Tests for covers service pure functions — is_allowed_cover_url, _looks_like_image, save_uploaded_cover."""

from app.services.covers import is_allowed_cover_url, _looks_like_image, save_uploaded_cover, _isbn13_to_isbn10_for_amazon


class TestIsAllowedCoverUrl:
    def test_trusted_domain_accepted(self):
        assert is_allowed_cover_url("https://covers.openlibrary.org/b/id/12345-L.jpg") is True

    def test_untrusted_domain_rejected(self):
        assert is_allowed_cover_url("https://evil.example.com/image.jpg") is False

    def test_malformed_url_returns_false(self):
        assert is_allowed_cover_url("not-a-url") is False

    def test_http_scheme_accepted(self):
        assert is_allowed_cover_url("http://covers.openlibrary.org/b/id/1-L.jpg") is True

    def test_https_scheme_accepted(self):
        assert is_allowed_cover_url("https://books.google.com/cover.jpg") is True

    def test_suffix_of_trusted_domain_rejected(self):
        # evil.covers.openlibrary.org is NOT in the allowed set
        assert is_allowed_cover_url("https://evil.covers.openlibrary.org/img.jpg") is False

    def test_ftp_scheme_rejected(self):
        assert is_allowed_cover_url("ftp://covers.openlibrary.org/img.jpg") is False

    def test_empty_string(self):
        assert is_allowed_cover_url("") is False

    def test_all_trusted_domains(self):
        from app.services.covers import ALLOWED_COVER_DOMAINS
        for domain in ALLOWED_COVER_DOMAINS:
            assert is_allowed_cover_url(f"https://{domain}/img.jpg") is True


class TestLooksLikeImage:
    def test_jpeg_magic_bytes(self):
        assert _looks_like_image(b"\xff\xd8\xff" + b"\x00" * 100) is True

    def test_png_magic_bytes(self):
        assert _looks_like_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100) is True

    def test_gif87a_magic_bytes(self):
        assert _looks_like_image(b"GIF87a" + b"\x00" * 100) is True

    def test_gif89a_magic_bytes(self):
        assert _looks_like_image(b"GIF89a" + b"\x00" * 100) is True

    def test_webp_magic_bytes(self):
        assert _looks_like_image(b"RIFF" + b"\x00" * 100) is True

    def test_arbitrary_bytes_rejected(self):
        assert _looks_like_image(b"This is just text content") is False

    def test_empty_bytes_rejected(self):
        assert _looks_like_image(b"") is False


class TestSaveUploadedCover:
    def test_content_below_min_size_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.covers.COVERS_DIR", tmp_path / "covers")
        assert save_uploaded_cover(1, b"\xff\xd8\xff" + b"\x00" * 10) is None

    def test_content_above_max_size_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.covers.COVERS_DIR", tmp_path / "covers")
        from app.services.covers import MAX_COVER_SIZE
        content = b"\xff\xd8\xff" + b"\x00" * (MAX_COVER_SIZE + 1)
        assert save_uploaded_cover(1, content) is None

    def test_non_image_content_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.covers.COVERS_DIR", tmp_path / "covers")
        assert save_uploaded_cover(1, b"x" * 200) is None

    def test_valid_jpeg_writes_file_and_returns_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.covers.COVERS_DIR", tmp_path / "covers")
        content = b"\xff\xd8\xff" + b"\x00" * 200
        result = save_uploaded_cover(1, content)
        assert result == "covers/1.jpg"
        assert (tmp_path / "covers" / "1.jpg").exists()


class TestIsbn13ToIsbn10ForAmazon:
    def test_valid_isbn13_converts(self):
        # 9780134685991 -> 0134685997
        result = _isbn13_to_isbn10_for_amazon("9780134685991")
        assert result != "9780134685991"  # conversion happened
        assert len(result) == 10

    def test_non_978_prefix_returns_original(self):
        result = _isbn13_to_isbn10_for_amazon("9790000000001")
        # isbn13_to_isbn10 returns None for non-978, so function returns original
        assert result == "9790000000001"
