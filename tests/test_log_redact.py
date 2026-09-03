"""The httpx request-log filter must blank credential query values (issue #36 §1)."""

import logging

import httpx

from app.log_handler import REDACT_QUERY_KEYS, RedactQueryFilter


HTTPX_MSG = 'HTTP Request: %s %s "%s %d %s"'


def _httpx_record(url):
    """A LogRecord shaped exactly like the one httpx emits per request.

    httpx._client logs `('HTTP Request: %s %s "%s %d %s"', method, request.url,
    http_version, status_code, reason_phrase)` — so args[1] is an httpx.URL,
    not a string. See .devdocs/…-probes/httpx_query_logging.py.
    """
    return logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=HTTPX_MSG,
        args=("POST", url, "HTTP/1.1", 200, "OK"),
        exc_info=None,
    )


class TestRedactQueryFilter:
    def test_twitch_client_secret_is_blanked(self):
        record = _httpx_record(
            httpx.URL(
                "https://id.twitch.tv/oauth2/token"
                "?client_id=CID&client_secret=SUPERSECRET"
                "&grant_type=client_credentials"
            )
        )
        assert RedactQueryFilter().filter(record) is True
        message = record.getMessage()
        assert "client_id=***&client_secret=***&grant_type=client_credentials" in message
        assert "SUPERSECRET" not in message
        assert "CID" not in message

    def test_tmdb_v3_key_is_blanked_and_the_query_survives(self):
        record = _httpx_record(
            httpx.URL(
                "https://api.themoviedb.org/3/search/movie"
                "?api_key=0123456789abcdef0123456789abcdef&query=The+Matrix"
            )
        )
        RedactQueryFilter().filter(record)
        message = record.getMessage()
        assert "api_key=***&query=The+Matrix" in message
        assert "0123456789abcdef0123456789abcdef" not in message

    def test_a_url_with_no_credential_key_is_untouched(self):
        url = httpx.URL("https://api.upcitemdb.com/prod/trial/lookup?upc=085391163121")
        record = _httpx_record(url)
        before = record.getMessage()
        RedactQueryFilter().filter(record)
        assert record.getMessage() == before
        assert record.args[1] is url

    def test_a_url_with_no_query_at_all_is_untouched(self):
        record = _httpx_record(httpx.URL("https://id.twitch.tv/oauth2/token"))
        before = record.getMessage()
        RedactQueryFilter().filter(record)
        assert record.getMessage() == before

    def test_a_plain_string_url_is_redacted_too(self):
        record = _httpx_record("https://example.test/x?token=abc123&page=2")
        RedactQueryFilter().filter(record)
        assert "token=***&page=2" in record.getMessage()
        assert "abc123" not in record.getMessage()

    def test_matching_is_case_insensitive(self):
        record = _httpx_record("https://example.test/x?API_KEY=shh&keep=1")
        RedactQueryFilter().filter(record)
        assert "API_KEY=***&keep=1" in record.getMessage()
        assert "shh" not in record.getMessage()

    def test_a_record_whose_args_carry_no_url_is_untouched(self):
        record = logging.LogRecord(
            name="httpx",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="retrying %s after %d failures",
            args=("token refresh", 2),
            exc_info=None,
        )
        RedactQueryFilter().filter(record)
        assert record.getMessage() == "retrying token refresh after 2 failures"

    def test_a_single_non_tuple_arg_is_handled(self):
        record = logging.LogRecord(
            name="httpx",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="%s",
            args=("https://example.test/x?secret=hunter2",),
            exc_info=None,
        )
        RedactQueryFilter().filter(record)
        assert record.getMessage() == "https://example.test/x?secret=***"

    def test_a_mapping_arg_is_handled(self):
        # A sole mapping argument is passed as a 1-tuple; LogRecord unwraps it,
        # so record.args ends up a bare dict (logging/__init__.py:324).
        record = logging.LogRecord(
            name="httpx",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="%(url)s",
            args=({"url": "https://example.test/x?access_token=abc"},),
            exc_info=None,
        )
        assert isinstance(record.args, dict)
        RedactQueryFilter().filter(record)
        assert record.getMessage() == "https://example.test/x?access_token=***"

    def test_the_filter_never_raises(self):
        class Exploding:
            __class__ = type("URL", (), {})

            def __str__(self):
                raise RuntimeError("boom")

        record = _httpx_record(Exploding())
        # Returns True and does not propagate — a filter that throws takes the
        # request down with it.
        assert RedactQueryFilter().filter(record) is True

    def test_the_key_set_covers_the_credentials_this_app_sends(self):
        assert {"api_key", "client_id", "client_secret", "token"} <= REDACT_QUERY_KEYS


def test_the_filter_is_installed_on_the_httpx_logger():
    # G14: app.main must be imported inside the test, never at module level.
    import app.main  # noqa: F401

    assert any(
        isinstance(f, RedactQueryFilter)
        for f in logging.getLogger("httpx").filters
    )

class TestUserinfoIsStripped:
    """A URL's userinfo is a credential in every case, so it never survives.

    Found by `/test-drive` (Observation 1): ntfy documents
    `https://user:pass@host/topic` for an authenticated topic and httpx honours
    URL userinfo, so the query filter above ran, blanked `token=`, and printed
    the password beside it.
    """

    def test_userinfo_is_stripped_from_a_string_url(self):
        record = _httpx_record("https://NTFYUSER:NTFYPASS@ntfy.example/topic")
        RedactQueryFilter().filter(record)
        message = record.getMessage()
        assert "NTFYUSER" not in message
        assert "NTFYPASS" not in message
        assert "https://ntfy.example/topic" in message

    def test_userinfo_is_stripped_from_an_httpx_url(self):
        record = _httpx_record(httpx.URL("https://u:p@ntfy.example:8443/topic"))
        RedactQueryFilter().filter(record)
        message = record.getMessage()
        assert "u:p@" not in message
        assert "https://ntfy.example:8443/topic" in message

    def test_userinfo_and_a_credential_query_are_both_handled(self):
        record = _httpx_record("https://u:SECRET@example.test/x?token=abc&page=2")
        RedactQueryFilter().filter(record)
        message = record.getMessage()
        assert "SECRET" not in message
        assert "abc" not in message
        assert "token=***&page=2" in message

    def test_a_username_with_no_password_is_stripped_too(self):
        record = _httpx_record("https://apitoken@example.test/x")
        RedactQueryFilter().filter(record)
        assert "apitoken" not in record.getMessage()

    def test_a_url_without_userinfo_is_still_byte_identical(self):
        # The "unchanged → hand back the original object" contract must survive
        # the userinfo pass, or every provider URL gets reformatted.
        url = httpx.URL("https://api.upcitemdb.com/prod/trial/lookup?upc=085391163121")
        record = _httpx_record(url)
        before = record.getMessage()
        RedactQueryFilter().filter(record)
        assert record.getMessage() == before
        assert record.args[1] is url


class TestHttpxRequestLogIsSilenced:
    """httpx's own per-request INFO line is the sink `notify._target` cannot reach.

    `notify.py` logs `scheme://host` and httpx logs the whole URL for the same
    request, so the redaction there was undone one line later. httpx has exactly
    two log call sites (`_client.py`, both `logger.info`) and emits nothing at
    warning or error, so raising its level silences the leak and loses nothing.
    """

    def test_the_httpx_logger_is_raised_to_warning(self):
        import app.main  # noqa: F401  — importing installs the logging config

        assert logging.getLogger("httpx").level == logging.WARNING
