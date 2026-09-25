"""The logged client IP is uvicorn's right-to-left X-Forwarded-For walk, never a header the app parses.

The harness boots uvicorn without entrypoint.sh, so SHELF_TRUST_PROXY=1 reaches
only the app, and uvicorn trusts the 127.0.0.1 peer by its own default.
"""
import sqlite3

import httpx
import pytest

pytestmark = pytest.mark.e2e


def _read_login_warning(data_dir, username: str) -> str:
    db_path = data_dir / "shelf.db"
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT message FROM log_entries "
            "WHERE level = 'WARNING' AND message LIKE ? "
            "ORDER BY id DESC LIMIT 1",
            (f"Failed login attempt for username={username}%",),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else ""


def test_forwarded_client_is_the_rightmost_untrusted_hop(server_factory):
    """A two-hop X-Forwarded-For logs the rightmost entry uvicorn left
    untrusted (198.51.100.7), never the client-supplied leftmost one
    (203.0.113.9) the app used to log on `main`."""
    server = server_factory({"SHELF_TRUST_PROXY": "1"})
    username = "t3-rightmost-hop"

    resp = httpx.post(
        f"{server['url']}/login",
        data={"username": username, "password": "wrong"},
        headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.7"},
        follow_redirects=False,
    )
    assert resp.status_code == 401

    message = _read_login_warning(server["data_dir"], username)
    assert message, f"no failed-login log line for username={username}"
    assert message.endswith("from 198.51.100.7"), message


def test_cf_connecting_ip_is_not_read(server_factory):
    """CF-Connecting-IP is never read; with no X-Forwarded-For the logged
    address is the real socket peer, 127.0.0.1."""
    server = server_factory({"SHELF_TRUST_PROXY": "1"})
    username = "t3-cf-connecting-ip"

    resp = httpx.post(
        f"{server['url']}/login",
        data={"username": username, "password": "wrong"},
        headers={"CF-Connecting-IP": "203.0.113.50"},
        follow_redirects=False,
    )
    assert resp.status_code == 401

    message = _read_login_warning(server["data_dir"], username)
    assert message, f"no failed-login log line for username={username}"
    assert message.endswith("from 127.0.0.1"), message
