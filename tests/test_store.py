"""Tests for the PWA store mode (routers/store.py)."""
import hashlib
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import _insert_item

REPO_ROOT = Path(__file__).resolve().parents[1]
SW_PATH = REPO_ROOT / "static" / "sw.js"
STATIC_ROOT = REPO_ROOT / "static"

# Pinned sha256 digest of the precached files' contents, keyed by SW_VERSION.
# Recompute and update this after bumping SW_VERSION in static/sw.js (e.g.
# following a `make css` rebuild of app.css).
PINNED = {
    # v2 shipped across more than one release with *different* app.css
    # contents (this digest is only the last of them), so browsers that
    # precached under v2 kept serving whichever app.css they saw first --
    # cache-first, so neither Cache-Control nor a hard refresh dislodged it.
    # v3 renames the cache, which makes activate() purge the stale one.
    "v2": "b90ab85864792d4f2089bebd97af476e3e660990fc472f91af3604d267132957",
    "v3": "b90ab85864792d4f2089bebd97af476e3e660990fc472f91af3604d267132957",
    # v4 adds scanner-engine.js and the vendored zxing-browser blob to
    # PRECACHE -- the store PWA now uses the shared scanner engine.
    "v4": "9ce800c0bb4c3c1b6352582278a90be717ce651e02f7fb94d2c558ad3a139a6f",
    # v5: app.css rebuild — the intake "working" spinner panels emit new
    # utilities (border-shelf-accent/40, opacity-25/75 at top level).
    "v5": "1d9ebffdde8b3970bd28ea41ae248efde0e41ccae5f6bbe95bdd7b44a318ec29",
    # v6: the store.js provenance comment was repointed from docs/ to
    # .devdocs/ (workflow-migration Phase 3). Comment-only — no behaviour
    # change — but store.js is precached, so its bytes are pinned here.
    "v6": "7ea788c2ffc51038262a04a0f440f1afc932ca02235349308fe2b32b792f120f",
    # v7: app.css rebuild — intake.js's downscale helper (issue #32) uses the
    # bare words `shrink` and `resize` in a variable name and its comments, and
    # Tailwind's extractor scans static/js/**, so it now emits .shrink and
    # .resize. Two dead rules, but app.css is precached, so the bump is real.
    "v7": "7da4b1f46b6d2137a3bc6428933023478ab4b206c92a0ea84d17ed499ab6feec",
}


class TestStorePage:
    def test_store_page_renders(self, admin_client):
        resp = admin_client.get("/store")
        assert resp.status_code == 200
        assert "Store Mode" in resp.text
        assert "/static/js/store.js" in resp.text
        assert "manifest.webmanifest" in resp.text

    def test_requires_login(self, client, admin_user):
        resp = client.get("/store", follow_redirects=False)
        assert resp.status_code == 303  # -> /login

    def test_sw_served_from_root_without_auth(self, client, admin_user):
        resp = client.get("/sw.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]
        assert "shelf-store" in resp.text  # cache name marker

    def test_manifest_served(self, admin_client):
        resp = admin_client.get("/static/manifest.webmanifest")
        assert resp.status_code == 200
        assert '"start_url": "/store"' in resp.text


class TestSwPrecacheDigest:
    def _parse_sw(self):
        src = SW_PATH.read_text()

        version_match = re.search(r"SW_VERSION\s*=\s*['\"]([^'\"]+)['\"]", src)
        assert version_match, (
            f"Could not find SW_VERSION in {SW_PATH} — the parsing regex in "
            "TestSwPrecacheDigest no longer matches the source. Update the "
            "regex (or this tripwire is silently disarmed)."
        )
        version = version_match.group(1)

        precache_match = re.search(r"PRECACHE\s*=\s*\[(.*?)\]", src, re.S)
        assert precache_match, (
            f"Could not find a PRECACHE = [...] list in {SW_PATH} — the "
            "parsing regex in TestSwPrecacheDigest no longer matches the "
            "source. Update the regex (or this tripwire is silently "
            "disarmed)."
        )
        # Capture every quoted entry (not just /static/ ones) so a future
        # out-of-tree entry fails the startswith assertion instead of being
        # silently dropped from the digest.
        entries = re.findall(r"['\"]([^'\"]+)['\"]", precache_match.group(1))
        assert entries, (
            f"PRECACHE in {SW_PATH} parsed as empty — the parsing regex in "
            "TestSwPrecacheDigest no longer matches the source. Update the "
            "regex (or this tripwire is silently disarmed)."
        )

        return version, entries

    def test_precache_entries_resolve_to_static_files(self):
        _version, entries = self._parse_sw()

        for url_path in entries:
            assert url_path.startswith("/static/"), (
                f"PRECACHE entry {url_path!r} in {SW_PATH} does not start "
                "with /static/ — service worker precaching only covers "
                "files under static/."
            )
            assert ".." not in Path(url_path).parts, (
                f"PRECACHE entry {url_path!r} in {SW_PATH} contains a '..' "
                "path component — refusing to resolve it to disk."
            )
            disk_path = STATIC_ROOT / url_path.removeprefix("/static/")
            assert disk_path.is_file(), (
                f"PRECACHE entry {url_path!r} in {SW_PATH} does not map to "
                f"an existing file at {disk_path}."
            )

    def test_precache_digest_matches_sw_version(self):
        version, entries = self._parse_sw()

        h = hashlib.sha256()
        for url_path in sorted(entries):
            disk_path = STATIC_ROOT / url_path.removeprefix("/static/")
            h.update(url_path.encode("utf-8"))
            h.update(disk_path.read_bytes())
        digest = h.hexdigest()

        assert version in PINNED, (
            f"SW_VERSION {version!r} in {SW_PATH} has no pinned digest in "
            "tests/test_store.py's PINNED dict. Record the new digest "
            f"there: PINNED[{version!r}] = {digest!r}"
        )
        assert digest == PINNED[version], (
            "A precached file's contents changed without bumping "
            f"SW_VERSION (currently {version!r} in {SW_PATH}). This "
            "includes `make css` rebuilds of static/css/app.css — that is "
            "expected to trip this check. Fix: bump SW_VERSION in "
            "static/sw.js, then re-pin the digest in this test's PINNED "
            f"dict to {digest!r}."
        )


class TestStoreData:
    def test_returns_items_with_code_expansion(self, admin_client, db):
        _insert_item(db, title="Owned Book", isbn="9780441013593")
        _insert_item(db, title="Wishlist Book", isbn="9780553283686", owned=0)
        db.execute("COMMIT")

        data = admin_client.get("/api/store/data").json()
        assert data["count"] == 2
        by_title = {i["title"]: i for i in data["items"]}

        owned = by_title["Owned Book"]
        assert owned["owned"] is True
        # ISBN-13 stored -> ISBN-10 conversion included for barcode matching
        assert "9780441013593" in owned["codes"]
        assert "0441013597" in owned["codes"]

        assert by_title["Wishlist Book"]["owned"] is False

    def test_isbn10_only_item_gets_isbn13_code(self, admin_client, db):
        _insert_item(db, title="Old Entry", isbn=None, isbn10="0441013597")
        db.execute("COMMIT")

        data = admin_client.get("/api/store/data").json()
        item = data["items"][0]
        assert "9780441013593" in item["codes"]

    def test_items_without_isbn_excluded(self, admin_client, db):
        _insert_item(db, title="No ISBN", isbn=None)
        db.execute("COMMIT")
        data = admin_client.get("/api/store/data").json()
        assert data["count"] == 0

    def test_viewer_can_read(self, client, viewer_user):
        from app.auth import create_token
        token = create_token(viewer_user["id"], viewer_user["username"], viewer_user["role"],
                             viewer_user["display_name"])
        client.cookies.set("access_token", token)
        assert client.get("/api/store/data").status_code == 200


class TestStoreQueue:
    def _meta(self, title="Found Book"):
        return {"title": title, "authors": "An Author"}

    def test_wishlisted_with_metadata(self, admin_client, db):
        with patch("app.routers.items._lookup_metadata",
                   new=AsyncMock(return_value=(self._meta(), "openlibrary", {}))), \
             patch("app.routers.store.covers.download_cover", new=AsyncMock(return_value=None)):
            resp = admin_client.post("/api/store/queue", json={"isbns": ["9780441013593"]})
        results = resp.json()["results"]
        assert results[0]["status"] == "wishlisted"
        assert results[0]["title"] == "Found Book"

        item = db.execute("SELECT * FROM items WHERE isbn = '9780441013593'").fetchone()
        assert item["owned"] == 0

    def test_bare_add_when_lookup_fails(self, admin_client, db):
        with patch("app.routers.items._lookup_metadata",
                   new=AsyncMock(side_effect=Exception("network down"))):
            resp = admin_client.post("/api/store/queue", json={"isbns": ["9780553283686"]})
        results = resp.json()["results"]
        assert results[0]["status"] == "added_bare"

        item = db.execute("SELECT * FROM items WHERE isbn = '9780553283686'").fetchone()
        assert item["owned"] == 0
        assert item["source"] == "store_queue"
        assert "9780553283686" in item["title"]

    def test_bare_add_when_nothing_found(self, admin_client, db):
        with patch("app.routers.items._lookup_metadata",
                   new=AsyncMock(return_value=(None, None, {}))):
            resp = admin_client.post("/api/store/queue", json={"isbns": ["9780900000011"]})
        assert resp.json()["results"][0]["status"] == "added_bare"

    def test_duplicate_reported(self, admin_client, db):
        _insert_item(db, title="Already Here", isbn="9780441013593")
        db.execute("COMMIT")
        resp = admin_client.post("/api/store/queue", json={"isbns": ["9780441013593"]})
        result = resp.json()["results"][0]
        assert result["status"] == "duplicate"
        assert result["title"] == "Already Here"

    def test_isbn10_input_normalized(self, admin_client, db):
        with patch("app.routers.items._lookup_metadata",
                   new=AsyncMock(return_value=(None, None, {}))):
            resp = admin_client.post("/api/store/queue", json={"isbns": ["0441013597"]})
        assert resp.json()["results"][0]["isbn"] == "9780441013593"

    def test_invalid_isbn(self, admin_client):
        resp = admin_client.post("/api/store/queue", json={"isbns": ["not-an-isbn"]})
        assert resp.json()["results"][0]["status"] == "invalid"

    def test_batch_deduped_and_capped(self, admin_client):
        isbns = ["junk"] * 10 + [f"junk{i}" for i in range(60)]
        resp = admin_client.post("/api/store/queue", json={"isbns": isbns})
        results = resp.json()["results"]
        assert len(results) <= 50  # deduped ("junk" once) and capped

    def test_bad_body_rejected(self, admin_client):
        assert admin_client.post("/api/store/queue", json={"isbns": "nope"}).status_code == 400
        assert admin_client.post("/api/store/queue", json={"isbns": [123]}).status_code == 400

    def test_viewer_cannot_queue(self, client, viewer_user):
        from app.auth import create_token
        token = create_token(viewer_user["id"], viewer_user["username"], viewer_user["role"],
                             viewer_user["display_name"])
        client.cookies.set("access_token", token)
        resp = client.post("/api/store/queue", json={"isbns": ["9780441013593"]})
        assert resp.status_code == 403
