"""`app/database.py` owns every table Shelf creates.

Contributed integrations arrived carrying their own `_SCHEMA` string and an
`ensure_schema(db)` called from the request path, on the reasoning that a
local table "does not consume an upstream migration number while other schema
proposals are in flight". The reasoning is sound and the conclusion is not:
MIGRATION_TABLES consumes no migration number either. It is a plain block of
CREATE TABLE IF NOT EXISTS that `init_db` executescripts on every boot, fresh
install and upgrade alike — exactly what a new table wants, and it keeps the
table set readable in one file (G1).

A table created lazily on first request is also invisible to backup and
upgrade docs until someone happens to exercise the feature.
"""

from pathlib import Path


APP = Path(__file__).parents[1] / "app"


def test_only_database_py_creates_tables():
    offenders = sorted(
        p.relative_to(APP.parent).as_posix()
        for p in APP.rglob("*.py")
        if p.name != "database.py" and "CREATE TABLE" in p.read_text()
    )
    assert offenders == [], (
        "CREATE TABLE outside app/database.py: "
        f"{offenders} — add the DDL to MIGRATION_TABLES instead")


def test_no_ensure_schema_helpers_remain():
    offenders = sorted(
        p.relative_to(APP.parent).as_posix()
        for p in APP.rglob("*.py")
        if "def ensure_schema" in p.read_text()
    )
    assert offenders == [], (
        f"ensure_schema() helpers still present: {offenders} — tables belong "
        "in MIGRATION_TABLES, not in a request-path call")
