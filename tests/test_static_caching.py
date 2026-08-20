"""Regression tests for issue #21 — static assets must revalidate.

The /static and /covers mounts send Cache-Control: no-cache so browsers
revalidate on every use instead of falling back to heuristic freshness
(which served stale JS for weeks after an upgrade). The existing
ETag/Last-Modified validators turn each check into a cheap 304.
"""
from pathlib import Path


def _covers_mount_directory() -> str:
    # Import inside the helper: a module-level `from app.main import app`
    # would run at collection time, before the conftest isolates DATA_DIR.
    from app.main import app

    # The /covers mount freezes its directory at first import of app.main,
    # so the per-test app.config.COVERS_DIR is NOT what the mount serves —
    # resolve the mounted instance's live directory instead (see
    # shelf/CLAUDE.md "Config import trap").
    return next(
        r for r in app.routes if getattr(r, "name", None) == "covers"
    ).app.directory


class TestStaticCacheControl:
    def test_static_asset_sends_no_cache_and_etag(self, client):
        resp = client.get("/static/js/components.js")
        assert resp.status_code == 200
        assert "no-cache" in resp.headers["cache-control"]
        assert resp.headers.get("etag")

    def test_conditional_request_returns_304_with_no_cache(self, client):
        first = client.get("/static/js/components.js")
        etag = first.headers["etag"]
        resp = client.get(
            "/static/js/components.js", headers={"If-None-Match": etag}
        )
        assert resp.status_code == 304
        assert "no-cache" in resp.headers["cache-control"]

    def test_covers_mount_sends_no_cache(self, client):
        covers_dir = Path(_covers_mount_directory())
        covers_dir.mkdir(parents=True, exist_ok=True)
        cover = covers_dir / "test-static-caching.jpg"
        cover.write_bytes(b"\xff\xd8\xff\xe0fakejpg")
        try:
            resp = client.get("/covers/test-static-caching.jpg")
            assert resp.status_code == 200
            assert "no-cache" in resp.headers["cache-control"]
        finally:
            cover.unlink(missing_ok=True)

    def test_html_pages_unchanged(self, admin_client):
        # Guard against the header leaking onto app routes: HTML responses
        # must not pick up the static mounts' no-cache.
        resp = admin_client.get("/browse")
        assert resp.status_code == 200
        assert "no-cache" not in resp.headers.get("cache-control", "")
