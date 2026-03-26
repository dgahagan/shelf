"""Tests for app.auth — password hashing, JWT tokens, role enforcement."""

import time
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from app.auth import (
    hash_password,
    verify_password,
    create_token,
    decode_token,
    should_refresh_token,
    get_secret_key,
    get_user_count,
    _cached_secret_key,
)


# --- Password hashing ---


def test_hash_and_verify_roundtrip():
    hashed = hash_password("mysecret")
    assert hashed != "mysecret"
    assert verify_password("mysecret", hashed)


def test_verify_wrong_password():
    hashed = hash_password("correct")
    assert not verify_password("wrong", hashed)


def test_hash_produces_unique_salts():
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != h2  # bcrypt uses random salt


# --- JWT tokens ---


def test_create_and_decode_token():
    token = create_token(1, "alice", "admin", "Alice")
    payload = decode_token(token)
    assert payload is not None
    assert payload["sub"] == 1
    assert payload["username"] == "alice"
    assert payload["role"] == "admin"
    assert payload["display_name"] == "Alice"


def test_decode_expired_token():
    """An expired token should return None."""
    from app.config import JWT_ALGORITHM
    import jwt as pyjwt
    from datetime import datetime, timezone, timedelta

    payload = {
        "sub": 1,
        "username": "alice",
        "role": "admin",
        "display_name": "Alice",
        "iat": datetime.now(timezone.utc) - timedelta(days=30),
        "exp": datetime.now(timezone.utc) - timedelta(days=1),
    }
    token = pyjwt.encode(payload, get_secret_key(), algorithm=JWT_ALGORITHM)
    assert decode_token(token) is None


def test_decode_tampered_token():
    token = create_token(1, "alice", "admin")
    # Tamper with the token
    tampered = token[:-5] + "XXXXX"
    assert decode_token(tampered) is None


def test_create_token_default_display_name():
    """display_name defaults to username when not provided."""
    token = create_token(1, "bob", "viewer")
    payload = decode_token(token)
    assert payload["display_name"] == "bob"


# --- Token refresh ---


def test_should_refresh_token_before_halflife(client, admin_user):
    """Fresh tokens should NOT be refreshed."""
    from app.auth import create_token
    token = create_token(admin_user["id"], "admin", "admin")
    # Simulate a request with a fresh cookie
    from starlette.testclient import TestClient
    from app.main import app
    c = TestClient(app, base_url="https://testserver", cookies={"access_token": token})
    # Make a request and check no refresh happened
    from unittest.mock import MagicMock
    from starlette.requests import Request
    req = MagicMock()
    req.cookies = {"access_token": token}
    result = should_refresh_token(req)
    assert result is None  # too fresh to refresh


def test_should_refresh_token_after_halflife():
    """Tokens past half-life should return a new token."""
    from app.config import JWT_ALGORITHM, JWT_EXPIRY_SECONDS
    import jwt as pyjwt
    from datetime import datetime, timezone, timedelta

    # Create a token that was issued long enough ago to be past half-life
    iat = datetime.now(timezone.utc) - timedelta(seconds=JWT_EXPIRY_SECONDS * 0.6)
    exp = iat + timedelta(seconds=JWT_EXPIRY_SECONDS)
    payload = {
        "sub": 1, "username": "alice", "role": "admin", "display_name": "Alice",
        "iat": iat, "exp": exp,
    }
    old_token = pyjwt.encode(payload, get_secret_key(), algorithm=JWT_ALGORITHM)

    from unittest.mock import MagicMock
    req = MagicMock()
    req.cookies = {"access_token": old_token}
    new_token = should_refresh_token(req)
    assert new_token is not None
    assert new_token != old_token


# --- Secret key ---


def test_get_secret_key_generates_and_caches(db, monkeypatch):
    """If no env var and no DB value, key is generated, stored, and cached."""
    import app.auth as auth_mod
    monkeypatch.setattr(auth_mod, "_cached_secret_key", None)
    monkeypatch.setattr("app.config.SECRET_KEY", "")

    key1 = get_secret_key()
    assert key1
    assert len(key1) == 64  # hex of 32 bytes

    # Calling again returns the cached version
    key2 = get_secret_key()
    assert key1 == key2


def test_get_secret_key_from_env(monkeypatch):
    import app.auth as auth_mod
    monkeypatch.setattr(auth_mod, "_cached_secret_key", None)
    monkeypatch.setattr("app.auth.SECRET_KEY", "env-secret-key")
    key = get_secret_key()
    assert key == "env-secret-key"


# --- User count ---


def test_user_count_zero():
    assert get_user_count() == 0


def test_user_count_after_insert(admin_user):
    assert get_user_count() == 1
