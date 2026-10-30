"""The per-token rate bucket on `/api/v1`.

A valid token draws on its own bucket, so a dashboard cannot starve the
browser behind the same proxy; a refused bearer is charged to the IP, so
rotating random tokens escapes nothing. Every TestClient request comes from
one peer, `testclient`.
"""

import pytest

from app.services import api_tokens

STATS = "/api/v1/stats"
RPM = 60


def _limiter():
    """The live RateLimitMiddleware instance.

    `app.middleware_stack` is None until the first request, and every
    `build_middleware_stack()` call returns a new stack — so build it once and
    assign it, or the instance cleared here is not the one that serves.
    """
    from app.main import RateLimitMiddleware, app

    if app.middleware_stack is None:
        app.middleware_stack = app.build_middleware_stack()
    node = app.middleware_stack
    while node is not None and not isinstance(node, RateLimitMiddleware):
        node = getattr(node, "app", None)
    assert node is not None, "RateLimitMiddleware is not in the stack"
    return node


@pytest.fixture
def limited(client, monkeypatch):
    """Rate limiting on, with empty buckets before and after.

    The bucket dict is process-global, and other tests fill the `testclient`
    IP bucket.
    """
    monkeypatch.delenv("SHELF_DISABLE_RATE_LIMIT", raising=False)
    limiter = _limiter()
    limiter._hits.clear()
    yield client
    limiter._hits.clear()


def _token(db, user, name="dashboard"):
    created = api_tokens.create(db, user["id"], name)
    db.execute("COMMIT")
    return created["token"]


def _bearer(token, scheme="Bearer", gap=" "):
    return {"Authorization": f"{scheme}{gap}{token}"}


def test_a_tokens_requests_do_not_consume_the_ip_bucket(limited, viewer_client, viewer_user, db):
    token = _token(db, viewer_user)
    for _ in range(RPM):
        assert viewer_client.get(STATS, headers=_bearer(token)).status_code == 200
    assert viewer_client.get("/api/v1/stats").status_code == 200  # the cookie, on the IP bucket


def test_a_token_has_a_bucket_of_its_own(limited, viewer_user, db):
    token = _token(db, viewer_user)
    limited.cookies.clear()
    statuses = [limited.get(STATS, headers=_bearer(token)).status_code for _ in range(RPM + 1)]
    assert statuses[:RPM] == [200] * RPM
    assert statuses[RPM] == 429


def test_spellings_of_one_token_share_one_bucket(limited, viewer_user, db):
    token = _token(db, viewer_user)
    limited.cookies.clear()
    spellings = [_bearer(token), _bearer(token, "bearer"), _bearer(token, "BEARER", "   ")]
    statuses = [limited.get(STATS, headers=spellings[i % 3]).status_code for i in range(RPM + 1)]
    assert statuses[:RPM] == [200] * RPM
    assert statuses[RPM] == 429


def test_failed_bearers_are_charged_to_the_ip(limited, viewer_client, viewer_user):
    for i in range(RPM):
        resp = viewer_client.get(STATS, headers=_bearer(f"shelf_guess{i}"))
        assert resp.status_code == 401
    assert viewer_client.get("/api/v1/stats").status_code == 429


def test_a_full_ip_bucket_refuses_a_valid_bearer_too(limited, viewer_client, viewer_user, db):
    token = _token(db, viewer_user)
    for _ in range(RPM):
        assert viewer_client.get("/api/v1/stats").status_code == 200
    assert viewer_client.get(STATS, headers=_bearer(token)).status_code == 429


def test_two_tokens_have_independent_buckets(limited, viewer_user, db):
    first = _token(db, viewer_user, "first")
    second = _token(db, viewer_user, "second")
    limited.cookies.clear()
    for _ in range(RPM):
        limited.get(STATS, headers=_bearer(first))
    assert limited.get(STATS, headers=_bearer(first)).status_code == 429
    assert limited.get(STATS, headers=_bearer(second)).status_code == 200


def test_the_limiter_never_holds_a_raw_token(limited, viewer_user, db):
    token = _token(db, viewer_user)
    limited.cookies.clear()
    limited.get(STATS, headers=_bearer(token))
    keys = list(_limiter()._hits)
    assert keys and not any(token in k for k in keys)
