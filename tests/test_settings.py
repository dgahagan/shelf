"""Tests for app.database settings helpers and app.config env var overrides."""

import os
import pytest

from app.database import get_db, get_setting, get_all_settings


class TestGetSetting:
    def test_returns_db_value(self, db):
        db.execute("INSERT INTO settings (key, value) VALUES ('abs_url', 'http://abs.local')")
        assert get_setting(db, "abs_url") == "http://abs.local"

    def test_returns_empty_for_missing_key(self, db):
        assert get_setting(db, "nonexistent") == ""

    def test_env_var_overrides_db(self, db, monkeypatch):
        db.execute("INSERT INTO settings (key, value) VALUES ('abs_url', 'http://db-value')")
        monkeypatch.setenv("ABS_URL", "http://env-value")
        assert get_setting(db, "abs_url") == "http://env-value"

    def test_env_var_used_when_no_db_value(self, db, monkeypatch):
        monkeypatch.setenv("HARDCOVER_TOKEN", "env-token")
        assert get_setting(db, "hardcover_token") == "env-token"

    def test_no_env_override_for_unknown_keys(self, db, monkeypatch):
        """Keys not in SECRET_ENV_VARS should not check env."""
        db.execute("INSERT INTO settings (key, value) VALUES ('custom_key', 'db-val')")
        monkeypatch.setenv("CUSTOM_KEY", "env-val")
        assert get_setting(db, "custom_key") == "db-val"


class TestGetAllSettings:
    def test_returns_all_settings(self, db):
        db.execute("INSERT INTO settings (key, value) VALUES ('abs_url', 'http://abs.local')")
        db.execute("INSERT INTO settings (key, value) VALUES ('abs_token', 'tok123')")
        result = get_all_settings(db)
        assert result["abs_url"] == "http://abs.local"
        assert result["abs_token"] == "tok123"

    def test_env_overrides_applied(self, db, monkeypatch):
        db.execute("INSERT INTO settings (key, value) VALUES ('abs_url', 'http://db')")
        monkeypatch.setenv("ABS_URL", "http://env")
        result = get_all_settings(db)
        assert result["abs_url"] == "http://env"
