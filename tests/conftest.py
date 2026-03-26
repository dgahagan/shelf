import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Redirect all DB and filesystem operations to a temp directory."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    covers_dir = data_dir / "covers"
    covers_dir.mkdir()
    db_path = data_dir / "shelf.db"

    monkeypatch.setattr("app.config.DATA_DIR", data_dir)
    monkeypatch.setattr("app.config.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.config.COVERS_DIR", covers_dir)

    # Also patch database module's imports (already resolved at import time)
    monkeypatch.setattr("app.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.database.COVERS_DIR", covers_dir)

    # Reset cached secret key so each test gets a fresh one
    import app.auth as auth_mod
    monkeypatch.setattr(auth_mod, "_cached_secret_key", None)

    # Initialize schema
    from app.database import init_db
    init_db()


@pytest.fixture
def db():
    """Yield a database connection for direct queries in tests."""
    from app.database import get_db
    with get_db() as conn:
        yield conn


@pytest.fixture
def client():
    """FastAPI TestClient."""
    from app.main import app
    return TestClient(app, base_url="https://testserver")


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
