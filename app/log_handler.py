"""Custom logging handler that writes log records to the SQLite log_entries table."""

import logging
import time
from datetime import datetime, timezone


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
