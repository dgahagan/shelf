"""Logging plumbing: the SQLite log handler, and the credential-redacting filter."""

import logging
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


LOG_RETENTION_DAYS = 30
_last_prune = 0.0
_PRUNE_INTERVAL = 3600  # check once per hour


class SQLiteHandler(logging.Handler):
    """Logging handler that inserts records into the log_entries table.

    Uses its own connection per emit() call to avoid threading/async issues
    with shared connections. Only captures INFO+ by default.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from app.database import get_db
            msg = self.format(record)
            ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            with get_db() as db:
                db.execute(
                    "INSERT INTO log_entries (timestamp, level, module, message) VALUES (?, ?, ?, ?)",
                    (ts, record.levelname, record.name, msg),
                )
            self._maybe_prune()
        except Exception:
            self.handleError(record)

    def _maybe_prune(self) -> None:
        """Periodically delete old log entries."""
        global _last_prune
        now = time.monotonic()
        if now - _last_prune < _PRUNE_INTERVAL:
            return
        _last_prune = now
        try:
            from app.database import get_db
            with get_db() as db:
                db.execute(
                    "DELETE FROM log_entries WHERE timestamp < datetime('now', ?)",
                    (f"-{LOG_RETENTION_DAYS} days",),
                )
        except Exception:
            pass


# Query-string keys whose *value* must never reach a log line. Matched
# case-insensitively. TMDb v3 authentication requires the key in the query
# string, so the transport cannot avoid it — this filter is what keeps it out
# of `docker compose logs`.
REDACT_QUERY_KEYS = {
    "api_key",
    "client_secret",
    "client_id",
    "key",
    "token",
    "access_token",
    "secret",
}


def _redact_url(text: str) -> str:
    """Blank credential-valued query parameters in a URL string.

    Structural (urlparse/parse_qsl/urlencode), never a regex over the formatted
    message: a regex over prose cannot tell a query value from the rest of the
    line. Returns `text` unchanged when there is nothing to redact, so URLs
    without a credential key are byte-identical afterwards.
    """
    parts = urlparse(text)
    if not parts.query:
        return text
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if not any(k.lower() in REDACT_QUERY_KEYS for k, _ in pairs):
        return text
    redacted = [
        (k, "***" if k.lower() in REDACT_QUERY_KEYS else v) for k, v in pairs
    ]
    # safe="*" so the placeholder stays readable rather than becoming %2A%2A%2A.
    return urlunparse(parts._replace(query=urlencode(redacted, safe="*")))


def _redact_arg(value):
    """Rewrite one logging argument if it is a URL; otherwise return it as-is."""
    text = value if isinstance(value, str) else None
    if text is None:
        # httpx passes an httpx.URL, not a str. Duck-type rather than import
        # httpx here — this module is logging plumbing and is imported by tests
        # that must not drag the app in.
        if type(value).__name__ != "URL":
            return value
        text = str(value)
    if not text.startswith(("http://", "https://")):
        return value
    redacted = _redact_url(text)
    # Unchanged → hand back the original object so the message is untouched.
    return redacted if redacted != text else value


class RedactQueryFilter(logging.Filter):
    """Blank credential query values in the URL arguments of a log record.

    Installed on the `httpx` logger, which logs every outbound request URL at
    INFO. It *edits* records and never drops them, and it never raises: a
    logging filter that throws takes the request down with it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            args = record.args
            if isinstance(args, tuple):
                record.args = tuple(_redact_arg(a) for a in args)
            elif isinstance(args, dict):
                record.args = {k: _redact_arg(v) for k, v in args.items()}
            elif args is not None:
                record.args = _redact_arg(args)
        except Exception:
            pass
        return True
