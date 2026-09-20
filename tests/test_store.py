"""Tests for the PWA store mode (routers/store.py)."""
import importlib.util
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import _assert_ownership_partition, _insert_item
from tests.test_intake import _install_lock_probe

REPO_ROOT = Path(__file__).resolve().parents[1]
SW_PATH = REPO_ROOT / "static" / "sw.js"

# The digest logic lives in the script that stamps SW_VERSION, so the test and
# the generator cannot disagree about what the version should be. Loaded the
# same way tests/test_alpine_csp_lint.py loads its lint.
_SCRIPT = REPO_ROOT / "scripts" / "stamp_sw_version.py"
_spec = importlib.util.spec_from_file_location("stamp_sw_version", _SCRIPT)
stamp_sw_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stamp_sw_version)

# scripts/ is not a package -- loaded standalone the same way, for the shared
# "is staleness enforceable here?" predicate (scripts/ci_context.py).
_CI_CONTEXT_SCRIPT = REPO_ROOT / "scripts" / "ci_context.py"
_ci_context_spec = importlib.util.spec_from_file_location("ci_context", _CI_CONTEXT_SCRIPT)
ci_context = importlib.util.module_from_spec(_ci_context_spec)
_ci_context_spec.loader.exec_module(ci_context)


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
    """SW_VERSION is derived, not typed — these are the gate that keeps it so.

    Before Lever 2 this class asserted that a human had performed a three-step
    ritual (bump SW_VERSION, recompute the digest, re-pin it in a dict). It now
    asserts the generator ran, which `make css` and `make checks-fast` both do.
    """

    def test_precache_entries_resolve_to_static_files(self):
        _version, entries = stamp_sw_version.parse_sw()

        for url_path in entries:
            # Raises SwParseError with the offending entry if it escapes
            # static/, contains '..', or does not exist on disk.
            stamp_sw_version.resolve_entry(url_path)

    @pytest.mark.skipif(
        not ci_context.staleness_is_enforceable(),
        reason="Unsatisfiable on a PR build: a restamp in each PR collides "
               "across the batch. Enforced on push to main and locally.")
    def test_sw_version_matches_precache_digest(self):
        changed, current, expected = stamp_sw_version.stamp(check_only=True)

        assert not changed, (
            f"SW_VERSION in {SW_PATH} is {current!r} but the precache digest "
            f"says {expected!r}. A precached file's contents changed (a "
            "`make css` rebuild of static/css/app.css is the usual cause), or "
            "SW_VERSION was hand-edited. Fix: run `make css`, then commit "
            "static/sw.js."
        )

    def test_stamp_is_idempotent(self, tmp_path, monkeypatch):
        """Stamping a stale sw.js twice converges — sw.js is not self-precached.

        If sw.js were ever added to PRECACHE, stamping would change the bytes
        the digest is computed from and never settle. This is the tripwire for
        that; it fails the moment someone precaches the worker itself.
        """
        sw_copy = tmp_path / "sw.js"
        sw_copy.write_text(SW_PATH.read_text().replace(
            f"SW_VERSION = '{stamp_sw_version.expected_version()}'",
            "SW_VERSION = 'vSTALE'",
        ))
        monkeypatch.setattr(stamp_sw_version, "SW_PATH", sw_copy)

        first_changed, _current, first_expected = stamp_sw_version.stamp()
        assert first_changed, "a hand-edited SW_VERSION must be detected as stale"

        second_changed, second_current, _second_expected = stamp_sw_version.stamp()
        assert not second_changed, (
            "stamping did not converge — sw.js is probably listed in its own "
            "PRECACHE, which makes SW_VERSION depend on itself."
        )
        assert second_current == first_expected

    def test_hand_edited_version_fails_the_gate(self, tmp_path, monkeypatch):
        sw_copy = tmp_path / "sw.js"
        sw_copy.write_text(SW_PATH.read_text().replace(
            f"SW_VERSION = '{stamp_sw_version.expected_version()}'",
            "SW_VERSION = 'v9'",
        ))
        monkeypatch.setattr(stamp_sw_version, "SW_PATH", sw_copy)

        changed, current, expected = stamp_sw_version.stamp(check_only=True)
        assert changed and current == "v9" and expected != "v9"
        # --check must not rewrite the file it is checking.
        assert "SW_VERSION = 'v9'" in sw_copy.read_text()

    def test_parse_failure_is_loud(self, monkeypatch):
        """A regex that stops matching must raise, not silently disarm."""
        with pytest.raises(stamp_sw_version.SwParseError):
            stamp_sw_version.parse_sw("// no SW_VERSION and no PRECACHE here")

    def test_check_is_advisory_on_pull_request_build(self, tmp_path, monkeypatch, capsys):
        """`main() --check` reports but returns 0 on a PR build with a stale
        stamp -- the CLI-level counterpart to
        tests/test_badge_stamp.py::test_pr_builds_downgrade_staleness_to_advisory,
        since `make check-sw-version` calls `main()`, not `stamp()` directly.
        """
        sw_copy = tmp_path / "sw.js"
        sw_copy.write_text(SW_PATH.read_text().replace(
            f"SW_VERSION = '{stamp_sw_version.expected_version()}'",
            "SW_VERSION = 'vSTALE'",
        ))
        monkeypatch.setattr(stamp_sw_version, "SW_PATH", sw_copy)
        monkeypatch.setattr("sys.argv", ["stamp_sw_version.py", "--check"])
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")

        assert stamp_sw_version.main() == 0
        assert "ADVISORY" in capsys.readouterr().err

    def test_check_still_fails_on_push_and_unset(self, tmp_path, monkeypatch, capsys):
        """The same stale stamp fails `--check` everywhere except a PR build."""
        sw_copy = tmp_path / "sw.js"
        sw_copy.write_text(SW_PATH.read_text().replace(
            f"SW_VERSION = '{stamp_sw_version.expected_version()}'",
            "SW_VERSION = 'vSTALE'",
        ))
        monkeypatch.setattr(stamp_sw_version, "SW_PATH", sw_copy)
        monkeypatch.setattr("sys.argv", ["stamp_sw_version.py", "--check"])

        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        assert stamp_sw_version.main() == 1
        assert "ADVISORY" not in capsys.readouterr().err

        monkeypatch.delenv("GITHUB_EVENT_NAME")
        assert stamp_sw_version.main() == 1

    def test_parse_failure_is_never_downgraded_on_pull_request(self, tmp_path, monkeypatch):
        """The SwParseError branch answers a different question than staleness
        (G68) -- an unparseable sw.js must still fail `--check` even on a
        pull_request build, where the staleness branch alone is disarmed."""
        sw_copy = tmp_path / "sw.js"
        sw_copy.write_text("// no SW_VERSION and no PRECACHE here")
        monkeypatch.setattr(stamp_sw_version, "SW_PATH", sw_copy)
        monkeypatch.setattr("sys.argv", ["stamp_sw_version.py", "--check"])
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")

        assert stamp_sw_version.main() == 1


class TestStoreData:
    def test_returns_items_with_code_expansion(self, admin_client, db):
        _insert_item(db, title="Owned Book", isbn="9780441013593")
        _insert_item(db, title="Wishlist Book", isbn="9780553283686", owned=0, wishlisted=True)
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

    def test_neither_row_omitted_owned_and_wishlisted_kept(self, admin_client, db):
        """A row that is neither owned nor wishlisted is not "in the library"
        on the device — scanning it in a shop means "I want this" (§6)."""
        _insert_item(db, title="Owned Book", isbn="9780441013593")
        _insert_item(db, title="Wishlist Book", isbn="9780062319005", owned=0, wishlisted=True)
        _insert_item(db, title="Neither Book", isbn="9780340000007", owned=0)
        db.execute("COMMIT")

        data = admin_client.get("/api/store/data").json()
        titles = {i["title"] for i in data["items"]}
        assert titles == {"Owned Book", "Wishlist Book"}
        assert "Neither Book" not in titles


class TestStoreQueue:
    def _meta(self, title="Found Book"):
        return {"title": title, "authors": "An Author"}

    def test_wishlisted_with_metadata(self, admin_client, db):
        with patch("app.routers.items_common._lookup_metadata",
                   new=AsyncMock(return_value=(self._meta(), "openlibrary", {}, False))), \
             patch("app.routers.store.covers.download_cover", new=AsyncMock(return_value=None)):
            resp = admin_client.post("/api/store/queue", json={"isbns": ["9780441013593"]})
        results = resp.json()["results"]
        assert results[0]["status"] == "wishlisted"
        assert results[0]["title"] == "Found Book"

        item = db.execute("SELECT * FROM items WHERE isbn = '9780441013593'").fetchone()
        assert item["owned"] == 0
        _assert_ownership_partition(db)

    def test_google_key_reaches_store_metadata_lookup(self, admin_client, monkeypatch):
        monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "store-google-key")
        lookup = AsyncMock(return_value=(None, None, {}, False))
        with patch("app.routers.items_common._lookup_metadata", new=lookup):
            admin_client.post("/api/store/queue", json={"isbns": ["9789000000111"]})

        assert lookup.await_args.kwargs["google_api_key"] == "store-google-key"

    def test_bare_add_when_lookup_fails(self, admin_client, db):
        with patch("app.routers.items_common._lookup_metadata",
                   new=AsyncMock(side_effect=Exception("network down"))):
            resp = admin_client.post("/api/store/queue", json={"isbns": ["9780553283686"]})
        results = resp.json()["results"]
        assert results[0]["status"] == "added_bare"

        item = db.execute("SELECT * FROM items WHERE isbn = '9780553283686'").fetchone()
        assert item["owned"] == 0
        assert item["source"] == "store_queue"
        assert "9780553283686" in item["title"]

    def test_bare_add_when_nothing_found(self, admin_client, db):
        with patch("app.routers.items_common._lookup_metadata",
                   new=AsyncMock(return_value=(None, None, {}, False))):
            resp = admin_client.post("/api/store/queue", json={"isbns": ["9789000000111"]})
        assert resp.json()["results"][0]["status"] == "added_bare"

    def test_duplicate_reported(self, admin_client, db):
        _insert_item(db, title="Already Here", isbn="9780441013593")
        db.execute("COMMIT")
        resp = admin_client.post("/api/store/queue", json={"isbns": ["9780441013593"]})
        result = resp.json()["results"][0]
        assert result["status"] == "duplicate"
        assert result["title"] == "Already Here"

    def test_wishlisted_duplicate_still_reported_as_duplicate(self, admin_client, db):
        """An already-wishlisted row is a no-op, same as an owned one."""
        item_id = _insert_item(db, title="Already Wanted", isbn="9780062319005",
                                owned=0, wishlisted=True)
        db.execute("COMMIT")

        lookup = AsyncMock(side_effect=AssertionError("must not look up an existing row"))
        with patch("app.routers.items_common._lookup_metadata", new=lookup):
            resp = admin_client.post("/api/store/queue", json={"isbns": ["9780062319005"]})
        lookup.assert_not_awaited()

        result = resp.json()["results"][0]
        assert result["status"] == "duplicate"
        assert result["title"] == "Already Wanted"

        row = db.execute("SELECT owned FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["owned"] == 0
        assert db.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"] == 1
        _assert_ownership_partition(db)

    def test_flushing_a_neither_isbn_wishlists_it(self, admin_client, db):
        """A neither row scanned in a shop means "I want this" — the flush
        adds membership rather than treating it as a no-op duplicate (§6)."""
        item_id = _insert_item(db, title="Neither Book", isbn="9780340000007", owned=0)
        db.execute("COMMIT")

        lookup = AsyncMock(side_effect=AssertionError("must not look up an existing row"))
        with patch("app.routers.items_common._lookup_metadata", new=lookup):
            resp = admin_client.post("/api/store/queue", json={"isbns": ["9780340000007"]})
        lookup.assert_not_awaited()

        result = resp.json()["results"][0]
        assert result["status"] == "wishlisted"
        assert result["title"] == "Neither Book"
        assert result["item_id"] == item_id

        row = db.execute("SELECT owned FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["owned"] == 0
        assert db.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"] == 1

        from app.services.lists import WISHLISTED_SQL
        member = db.execute(
            f"SELECT 1 FROM items i WHERE i.id = ? AND {WISHLISTED_SQL}", (item_id,)
        ).fetchone()
        assert member is not None

        scan = db.execute(
            "SELECT result, item_id, mode FROM scan_log WHERE isbn = ?", ("9780340000007",)
        ).fetchone()
        assert scan["result"] == "wishlisted"
        assert scan["item_id"] == item_id
        assert scan["mode"] == "wishlist"

        _assert_ownership_partition(db)

    def test_flush_guard_reads_under_the_write_lock(self, admin_client, db, monkeypatch):
        """G18: the existing-row guard and the wishlist write it may trigger
        share the write lock — see `_install_lock_probe` in tests/test_intake.py."""
        from app.routers import store as store_router

        _insert_item(db, title="Neither Book", isbn="9780340000007", owned=0)
        db.execute("COMMIT")

        probe_results = []

        def predicate(sql):
            return "i.media_type = 'book'" in sql

        _install_lock_probe(monkeypatch, store_router, predicate, probe_results)

        resp = admin_client.post("/api/store/queue", json={"isbns": ["9780340000007"]})

        assert resp.status_code == 200
        assert probe_results, "the guard query never ran — the probe did not fire"
        assert probe_results[-1].startswith("locked"), (
            f"a rival writer could take the write lock while the flush's existing-row "
            f"guard was being read (got {probe_results[-1]!r}) — the route is missing "
            "its BEGIN IMMEDIATE, or takes it after the guard SELECT (G18)"
        )

    def test_isbn10_input_normalized(self, admin_client, db):
        with patch("app.routers.items_common._lookup_metadata",
                   new=AsyncMock(return_value=(None, None, {}, False))):
            resp = admin_client.post("/api/store/queue", json={"isbns": ["0441013597"]})
        assert resp.json()["results"][0]["isbn"] == "9780441013593"

    def test_invalid_isbn(self, admin_client):
        resp = admin_client.post("/api/store/queue", json={"isbns": ["not-an-isbn"]})
        assert resp.json()["results"][0]["status"] == "unreadable"

    def test_bad_check_digit_is_kept_as_an_unreadable_row(self, admin_client, db):
        """A well-formed but checksum-invalid ISBN-13 never reaches `items.isbn`
        (#54) — but the scan itself survives. The client drops any flushed code
        from its queue, so refusing without writing loses the scan outright
        (test-drive Observation 1)."""
        resp = admin_client.post("/api/store/queue", json={"isbns": ["9780441172710"]})
        result = resp.json()["results"][0]
        assert result["status"] == "unreadable"

        row = db.execute(
            "SELECT title, isbn, owned, source FROM items WHERE id = ?",
            (result["item_id"],),
        ).fetchone()
        assert row["title"] == "Unreadable barcode — 9780441172710"
        assert row["isbn"] is None
        assert row["owned"] == 0
        assert row["source"] == "store_queue"

    def test_unreadable_code_is_bounded_before_it_becomes_a_title(self, admin_client, db):
        resp = admin_client.post("/api/store/queue", json={"isbns": ["9" * 500]})
        result = resp.json()["results"][0]
        assert result["status"] == "unreadable"
        title = db.execute(
            "SELECT title FROM items WHERE id = ?", (result["item_id"],)
        ).fetchone()["title"]
        assert title == "Unreadable barcode — " + "9" * 32

    def test_bare_add_stores_canonical_pair_for_isbn10_input(self, admin_client, db):
        """A valid but unrecognized ISBN-10 falls to the bare-add path; the
        funnel derives both halves of the pair from the ISBN-13 alone."""
        with patch("app.routers.items_common._lookup_metadata",
                   new=AsyncMock(return_value=(None, None, {}, False))):
            resp = admin_client.post("/api/store/queue", json={"isbns": ["054792822X"]})
        assert resp.json()["results"][0]["status"] == "added_bare"

        item = db.execute(
            "SELECT isbn, isbn10 FROM items WHERE isbn = '9780547928227'"
        ).fetchone()
        assert item is not None
        assert item["isbn"] == "9780547928227"
        assert item["isbn10"] == "054792822X"

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
