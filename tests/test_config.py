"""Tests for config utilities — get_client_ip, get_setting_value, is_env_override."""

from unittest.mock import MagicMock

from app.config import get_client_ip, get_setting_value, is_env_override


class TestGetClientIp:
    def _make_request(self, host="1.2.3.4", headers=None):
        req = MagicMock()
        req.client.host = host
        req.headers = headers or {}
        return req

    def test_direct_ip_when_no_trusted_proxies(self, monkeypatch):
        monkeypatch.setattr("app.config.TRUSTED_PROXIES", frozenset())
        req = self._make_request("192.168.1.100")
        assert get_client_ip(req) == "192.168.1.100"

    def test_cf_connecting_ip_used_when_trusted_proxy(self, monkeypatch):
        monkeypatch.setattr("app.config.TRUSTED_PROXIES", frozenset({"10.0.0.1"}))
        req = self._make_request("10.0.0.1", {"cf-connecting-ip": "203.0.113.5"})
        assert get_client_ip(req) == "203.0.113.5"

    def test_xff_used_when_no_cf_header(self, monkeypatch):
        monkeypatch.setattr("app.config.TRUSTED_PROXIES", frozenset({"10.0.0.1"}))
        req = self._make_request("10.0.0.1", {"x-forwarded-for": "198.51.100.1"})
        assert get_client_ip(req) == "198.51.100.1"

    def test_xff_first_entry_returned(self, monkeypatch):
        monkeypatch.setattr("app.config.TRUSTED_PROXIES", frozenset({"10.0.0.1"}))
        req = self._make_request("10.0.0.1", {"x-forwarded-for": "198.51.100.1, 10.0.0.2, 10.0.0.3"})
        assert get_client_ip(req) == "198.51.100.1"

    def test_untrusted_proxy_ignores_headers(self, monkeypatch):
        monkeypatch.setattr("app.config.TRUSTED_PROXIES", frozenset({"10.0.0.1"}))
        req = self._make_request("99.99.99.99", {"x-forwarded-for": "spoofed.ip", "cf-connecting-ip": "also.spoofed"})
        assert get_client_ip(req) == "99.99.99.99"


class TestGetSettingValue:
    def test_env_var_takes_priority(self, monkeypatch):
        monkeypatch.setenv("ABS_URL", "http://env-abs.local")
        assert get_setting_value("abs_url", "http://db-abs.local") == "http://env-abs.local"

    def test_env_var_ignored_for_non_whitelisted_key(self, monkeypatch):
        monkeypatch.setenv("RANDOM_KEY", "should-not-appear")
        assert get_setting_value("random_key", "db-value") == "db-value"

    def test_db_value_used_when_no_env(self, monkeypatch):
        monkeypatch.delenv("ABS_URL", raising=False)
        assert get_setting_value("abs_url", "http://db-abs.local") == "http://db-abs.local"

    def test_empty_string_when_no_value(self):
        assert get_setting_value("abs_url") == ""


class TestIsEnvOverride:
    def test_returns_true_when_env_set(self, monkeypatch):
        monkeypatch.setenv("ABS_TOKEN", "some-token")
        assert is_env_override("abs_token") is True

    def test_returns_false_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("ABS_TOKEN", raising=False)
        assert is_env_override("abs_token") is False

    def test_returns_false_for_unknown_key(self):
        assert is_env_override("nonexistent_key") is False
