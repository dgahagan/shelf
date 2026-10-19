import os
import re
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _redirect_stale_path_constants(monkeypatch, data_dir, db_path, covers_dir):
    """Point every already-imported app module's copy of a config path at tmp.

    `from app.config import COVERS_DIR` freezes the value at import time, so
    patching app.config alone leaves those modules writing to the real /data
    — app.services.covers did exactly that, and a manual add with an ISBN
    tried to mkdir /data/covers from the unit suite.

    The bindings are discovered rather than listed so that a module gaining
    one later is covered without anyone remembering to update this file. Any
    app-module attribute with one of these three names is that config
    constant by construction; the Path check keeps an unrelated same-named
    value (a string, a mock) from being clobbered.
    """
    targets = {"DATA_DIR": data_dir, "DATABASE_PATH": db_path, "COVERS_DIR": covers_dir}
    for name, module in list(sys.modules.items()):
        if module is None or not (name == "app" or name.startswith("app.")):
            continue
        for attr, value in targets.items():
            current = getattr(module, attr, None)
            if isinstance(current, Path) and current != value:
                monkeypatch.setattr(module, attr, value, raising=False)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Redirect all DB and filesystem operations to a temp directory."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    covers_dir = data_dir / "covers"
    covers_dir.mkdir()
    db_path = data_dir / "shelf.db"

    # app.config is the source of truth: modules imported *after* this point
    # read these values, and so does anything that resolves them at call time.
    monkeypatch.setattr("app.config.DATA_DIR", data_dir)
    monkeypatch.setattr("app.config.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.config.COVERS_DIR", covers_dir)

    # Modules already imported that did `from app.config import COVERS_DIR`
    # bound the real /data path at import time and never see the line above
    # (app.services.archive documents the same trap), so redirect their
    # copies too.
    _redirect_stale_path_constants(monkeypatch, data_dir, db_path, covers_dir)

    # Clear every secret-credential env var so a developer's or CI runner's
    # shell can't leak a real credential into a settings-render test. Iterate
    # .values() — SECRET_ENV_VARS is settings-key -> ENV_NAME, and the two
    # collide in exactly the way that makes this easy to get backwards:
    # app/routers/pages.py iterates the *keys* (is_env_override takes a
    # settings key), this fixture needs the *values* (actual shell variable
    # names). `for env_name in SECRET_ENV_VARS` would yield 'tmdb_api_key'
    # etc. and clear nothing — a silent no-op.
    from app.config import SECRET_ENV_VARS
    for env_name in SECRET_ENV_VARS.values():
        monkeypatch.delenv(env_name, raising=False)

    # A developer shell that exports the E2E stub URL must not reach the unit suite.
    monkeypatch.delenv("SHELF_UPC_LOOKUP_URL", raising=False)
    monkeypatch.delenv("SHELF_ISSN_PORTAL_URL", raising=False)

    # Reset cached secret key so each test gets a fresh one
    import app.auth as auth_mod
    monkeypatch.setattr(auth_mod, "_cached_secret_key", None)

    # Reset cached encryption key — each test's key file lives in its own tmp dir
    import app.crypto as crypto_mod
    monkeypatch.setattr(crypto_mod, "_cached_encryption_key", None)
    # decrypt_value warns once per setting key per process; without this reset
    # the first test to see an undecryptable value silences every later one.
    monkeypatch.setattr(crypto_mod, "_warned_undecryptable", set())

    # Reset the nav settings cache — it would otherwise carry one test's
    # integration config (and hidden-tab set) into the next test's nav.
    import app.nav as nav_mod
    monkeypatch.setattr(nav_mod, "_cached_settings", None)

    # Reset the feature-flag cache (G13) — one test's disabled feature would
    # otherwise stay disabled in the next test's routes, nav and jobs.
    import app.features as features_mod
    monkeypatch.setattr(features_mod, "_cached_flags", None)
    monkeypatch.setattr(features_mod, "_generation", 0)

    # Reset the currency cache — otherwise one test's display currency
    # leaks into the next test's money formatting.
    import app.currency as currency_mod
    monkeypatch.setattr(currency_mod, "_cached_currency", None)

    # Reset the item_copies column cache. It holds PRAGMA table_info output,
    # which is the same for every test database, so nothing leaks today — but
    # G13's rule is unconditional for a module cache read at request time, and
    # a schema-by-hand test would otherwise inherit the previous shape.
    import app.services.item_copies as item_copies_mod
    item_copies_mod.reset_column_cache()

    # Reset the Trash expired-count cache — one test's trashed rows and
    # retention setting would otherwise answer the next test's count (G13).
    import app.services.trash as trash_mod
    monkeypatch.setattr(trash_mod, "_cache", None)

    # Reset the LibraryThing related-ISBN cache (G13) — it is keyed by ISBN
    # and reset only when the token changes, so two tests using the same
    # token would share one test's cached answers.
    import app.services.librarything as librarything_mod
    librarything_mod.reset_cache()

    # Reset the IGDB token cache — otherwise one test's cached OAuth token
    # leaks into the next test's credential pair.
    import app.services.igdb as igdb_mod
    monkeypatch.setattr(igdb_mod, "_token_cache", {})

    # Reset the per-host rate limiter registry. Its asyncio.Locks bind to the
    # loop they are first awaited on, and each test runs its own loop, so a
    # carried-over registry would hand a test a lock from a dead loop.
    import app.services.outbound as outbound_mod
    outbound_mod.reset()

    # Reset the cover enrichment queue. Its asyncio.Queue binds to the loop
    # it is created on, and the counters are process-global, so a carried-over
    # queue leaks depth and gave-up counts into unrelated tests.
    import app.services.cover_queue as cover_queue_mod
    cover_queue_mod.reset()

    # Initialize schema
    from app.database import init_db
    init_db()

    # Pre-seed and cache the secret key so get_secret_key() never opens a
    # second connection while a test's db fixture connection is already open.
    from app.auth import get_secret_key
    get_secret_key()


@pytest.fixture
def db():
    """Yield a database connection for direct queries in tests."""
    from app.database import get_db
    with get_db() as conn:
        yield conn


_TEST_CSRF_TOKEN = "test-csrf-token-fixed"


@pytest.fixture
def client(monkeypatch):
    """FastAPI TestClient with rate limiting disabled and CSRF pre-seeded."""
    monkeypatch.setenv("SHELF_DISABLE_RATE_LIMIT", "1")
    monkeypatch.setenv("SHELF_DISABLE_COVER_ENRICH", "1")  # no network from import tests
    monkeypatch.setenv("SHELF_DISABLE_PRICE_ALERTS", "1")  # no ISBNdb pass from the lifespan loop
    from app.main import app
    c = TestClient(app, base_url="https://testserver")
    c.cookies.set("csrf_token", _TEST_CSRF_TOKEN)
    c.headers.update({"X-CSRF-Token": _TEST_CSRF_TOKEN})
    return c


def _create_user(username, password, display_name, role):
    """Create a user using its own committed connection."""
    from app.auth import hash_password
    from app.database import get_db
    with get_db() as conn:
        conn.execute(
            "INSERT INTO users (username, password, display_name, role) VALUES (?, ?, ?, ?)",
            (username, hash_password(password), display_name, role),
        )
        row = conn.execute(
            "SELECT id, username, role, display_name FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        return dict(row)


@pytest.fixture
def admin_user():
    """Create an admin user (committed) and return their info dict."""
    return _create_user("admin", "password123", "Admin User", "admin")


@pytest.fixture
def admin_client(client, admin_user):
    """TestClient with a valid admin session cookie."""
    from app.auth import create_token
    token = create_token(admin_user["id"], admin_user["username"], admin_user["role"], admin_user["display_name"])
    client.cookies.set("access_token", token)
    return client


@pytest.fixture
def editor_user():
    """Create an editor user (committed) and return their info dict."""
    return _create_user("editor", "password123", "Editor User", "editor")


@pytest.fixture
def viewer_user():
    """Create a viewer user (committed) and return their info dict."""
    return _create_user("viewer", "password123", "Viewer User", "viewer")


@pytest.fixture
def editor_client(client, editor_user):
    """TestClient with a valid editor session cookie."""
    from app.auth import create_token
    token = create_token(editor_user["id"], editor_user["username"], editor_user["role"], editor_user["display_name"])
    client.cookies.set("access_token", token)
    return client


@pytest.fixture
def viewer_client(client, viewer_user):
    """TestClient with a valid viewer session cookie."""
    from app.auth import create_token
    token = create_token(viewer_user["id"], viewer_user["username"], viewer_user["role"], viewer_user["display_name"])
    client.cookies.set("access_token", token)
    return client


def has_bare_attribute(html: str, name: str) -> bool:
    """Is `name` an attribute in its own right, and not merely the prefix of a
    longer one?

    `assert "data-manual-add" in html` is satisfied by `data-manual-add-title`
    alone. Removing the bare hook that the click listener actually selects on
    therefore left every claiming test green while the button went inert —
    caught by mutation, not by the suite. Match the name followed by an
    attribute boundary (whitespace, `=`, `>` or `/`), never by `-` or another
    word character.
    """
    return re.search(re.escape(name) + r"(?![-\w])", html) is not None


def _insert_item(db, title="Test Book", isbn="9780000000026", media_type="book", wishlisted=False, **kwargs):
    """Insert a test item and return its ID.

    `wishlisted=True` adds the row to the wishlist after the INSERT; it is a
    keyword of this helper, not a column, so it never reaches the statement.
    `owned=0` alone is a legal "neither" state — pass `wishlisted=True`
    explicitly for a wishlist seed.
    """
    fields = {"title": title, "isbn": isbn, "media_type": media_type, "source": "test"}
    fields.update(kwargs)
    cols = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    cursor = db.execute(f"INSERT INTO items ({cols}) VALUES ({placeholders})", list(fields.values()))
    if wishlisted:
        from app.services import lists

        lists.add(db, lists.WISHLIST, cursor.lastrowid)
    return cursor.lastrowid


def _assert_ownership_partition(db):
    """Assert no item is both owned and a wishlist member.

    `owned = 1` must never coexist with wishlist membership — the one
    coupling that survives the ownership/wishlist split. `owned = 0` with no
    membership is a legal "neither" state and this check does not touch it.
    Raw-SQL test seeds bypass the app's write funnel, so this is a fixture
    sanity check, not app-code coverage.
    """
    from app.services.lists import WISHLISTED_SQL

    rows = db.execute(
        f"SELECT i.id, i.title, i.owned FROM items i "
        f"WHERE i.owned = 1 AND {WISHLISTED_SQL}"
    ).fetchall()
    assert not rows, (
        "owned items found on the wishlist: "
        + ", ".join(f"#{r['id']} {r['title']!r} (owned={r['owned']})" for r in rows)
    )


def _insert_borrower(db, name="Test Borrower"):
    """Insert a test borrower and return their ID."""
    cursor = db.execute("INSERT INTO borrowers (name) VALUES (?)", (name,))
    return cursor.lastrowid


def _insert_location(db, name="Test Location"):
    """Insert a test location and return its ID."""
    cursor = db.execute("INSERT INTO locations (name) VALUES (?)", (name,))
    return cursor.lastrowid


#: Matches the ALTER statements in MIGRATIONS, so `bootstrap_sql_before` can
#: work out which columns a given cutoff has not reached yet.
_ALTER_COLUMN = re.compile(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", re.I)


def bootstrap_sql_before(up_to: int) -> str:
    """`MIGRATION_TABLES` as it stood *before* migration ``up_to + 1`` (G98).

    A legacy-database fixture must be built from the bootstrap schema as it
    was before the migration under test — never from the current
    `MIGRATION_TABLES`. Running the current one first creates each table
    complete, so the numbered entry the fixture exists to exercise becomes a
    no-op and the test passes with that entry deleted.

    For a *table* the fixture simply omits the CREATE. For a *column* the trap
    is quieter and only opened up at migration 39. `_is_benign_migration_error`
    forgives `duplicate column name` for versions <= `_PRE_ATOMIC_MAX_VERSION`
    (21), and every column added to a MIGRATION_TABLES-managed table before
    that — 16-19 on `series_meta` — sat under the amnesty, so a fixture could
    run the current CREATE and the redundant ALTER was waved through. Migration
    39 (`tags.media_type`) is the first one above the line, and there the same
    fixture raises instead.

    So this strips every column added by a migration **above** `up_to`,
    derived from `MIGRATIONS` rather than listed by hand: migration 40 strips
    itself and no fixture has to be revisited.
    """
    from app.database import MIGRATION_TABLES, MIGRATIONS

    sql = MIGRATION_TABLES
    for version, _description, statement in MIGRATIONS:
        if version <= up_to:
            continue
        match = _ALTER_COLUMN.search(statement or "")
        if not match:
            continue
        table, column = match.group(1), match.group(2)
        # Only touch the column inside that table's own CREATE, so a column
        # name two tables share cannot be stripped from the wrong one.
        create = re.search(
            rf"(CREATE TABLE IF NOT EXISTS {table} \()(.*?)(\n\);)",
            sql,
            flags=re.S,
        )
        if not create:
            continue  # the table is not one MIGRATION_TABLES creates
        # A column the CREATE never carried needs no stripping: that is the
        # MIGRATIONS-only pattern (31's item_copies.position_order, 37 and
        # 38's deleted_at), where a copy in the CREATE would make the ALTER
        # raise on the fresh path instead. Only a column that IS in the body
        # has to come out.
        if not re.search(rf"\n\s*{column}\s", create.group(2)):
            continue
        # Two shapes, because the last column in a CREATE carries no trailing
        # comma: strip `\n  col ...,` normally, and `,\n  col ...` when the
        # column is last. Missing the second silently leaves the column in
        # place and the fixture stops being a legacy one at all.
        body, hits = re.subn(
            rf"\n\s*{column}\s+[^,\n]+,", "", create.group(2), count=1
        )
        if not hits:
            body, hits = re.subn(
                rf",\n\s*{column}\s+[^,\n]+(?=\n|$)", "", create.group(2), count=1
            )
        assert hits, (
            f"bootstrap_sql_before({up_to}) found {table}.{column} in its "
            f"CREATE but could not strip it, so the fixture it builds would "
            f"already have a column migration {version} is supposed to add — "
            f"the test would be vacuously green (G98)."
        )
        sql = sql[: create.start(2)] + body + sql[create.end(2) :]
    return sql
