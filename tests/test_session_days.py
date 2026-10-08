"""SHELF_SESSION_DAYS sets the session lifetime (#149)."""

import logging
import os
import subprocess
import sys
from pathlib import Path

import jwt as pyjwt
import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.config import JWT_ALGORITHM, session_days

APP_DIR = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_unset_or_blank_is_default_and_silent(raw, caplog):
    with caplog.at_level(logging.WARNING):
        assert session_days(raw) == 7
    assert not caplog.records


@pytest.mark.parametrize("raw,expected", [("1", 1), ("30", 30), ("365", 365), (" 30 ", 30)])
def test_valid_values_pass_through(raw, expected, caplog):
    with caplog.at_level(logging.WARNING):
        assert session_days(raw) == expected
    assert not caplog.records


@pytest.mark.parametrize("raw", ["0", "-3", "366", "30.5", "abc"])
def test_invalid_values_fall_back_with_one_warning(raw, caplog):
    with caplog.at_level(logging.WARNING):
        assert session_days(raw) == 7
    assert len(caplog.records) == 1
    assert "SHELF_SESSION_DAYS" in caplog.records[0].getMessage()


def test_token_lifetime_follows_auth_constant(monkeypatch):
    # auth.py from-imports the constant, so the patch must target app.auth.
    monkeypatch.setattr("app.auth.JWT_EXPIRY_SECONDS", 30 * 86400)
    from app.auth import create_token, get_secret_key

    token = create_token(1, "u", "admin", token_version=1)
    claims = pyjwt.decode(token, get_secret_key(), algorithms=[JWT_ALGORITHM])
    assert claims["exp"] - claims["iat"] == 30 * 86400


def test_cookies_max_age_follows_auth_constant(monkeypatch):
    monkeypatch.setattr("app.auth.JWT_EXPIRY_SECONDS", 30 * 86400)
    from app.auth import set_auth_cookie

    request = Request({"type": "http", "scheme": "https", "path": "/", "headers": [], "server": ("testserver", 443)})
    response = Response()
    set_auth_cookie(request, response, "tok", "csrf")
    cookies = {h.split("=", 1)[0].strip(): h for h in response.headers.getlist("set-cookie")}
    assert "Max-Age=2592000" in cookies["access_token"]
    assert "Max-Age=2592000" in cookies["csrf_token"]


def _expiry_in_subprocess(tmp_path, days):
    env = {k: v for k, v in os.environ.items() if k != "SHELF_SESSION_DAYS"}
    env["DATA_DIR"] = str(tmp_path)
    if days is not None:
        env["SHELF_SESSION_DAYS"] = days
    out = subprocess.run(
        [sys.executable, "-c", "import app.auth as a; print(a.JWT_EXPIRY_SECONDS)"],
        env=env, cwd=APP_DIR, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip().splitlines()[-1]


def test_env_var_sets_expiry_at_import(tmp_path):
    assert _expiry_in_subprocess(tmp_path, "30") == "2592000"


def test_absent_env_var_is_seven_days(tmp_path):
    assert _expiry_in_subprocess(tmp_path, None) == "604800"
