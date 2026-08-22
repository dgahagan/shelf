"""Tests for the portable library archive (app/services/archive.py,
app/routers/archive.py) — exporter, and the reader's security rail."""
import io
import json
import os
import re
import shutil
import time
import zipfile

import pytest

from app import config
from app.database import get_db
from app.services import archive as archive_svc
from app.services.archive import (
    ArchiveError,
    apply_plan,
    build_archive,
    merge_archive,
    plan_archive,
    read_archive,
)
from tests.conftest import _insert_borrower, _insert_item, _insert_location

# Hostile fixtures are built in-test with zipfile — no binary blobs checked in.
_MANIFEST = json.dumps({"format": "shelf-archive", "version": 1,
                        "exported_at": "2026-08-18T00:00:00Z",
                        "app_version": None, "counts": {}})
_LIBRARY = json.dumps({"items": []})
_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200


def _write_zip(path, entries, *, manifest=_MANIFEST, library=_LIBRARY):
    """Build a zip at `path`. `entries` is a list of (name, bytes); manifest
    and library are added unless explicitly passed as None."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        if manifest is not None:
            zf.writestr("manifest.json", manifest)
        if library is not None:
            zf.writestr("library.json", library)
        for name, data in entries:
            zf.writestr(name, data)
    return path


def _seed_full_library(db):
    """Seed one item exercising every derived/joined field the exporter
    touches: location, tags, cover, series, reading_log, checkouts,
    valuation_history."""
    loc_id = _insert_location(db, name="Living Room")
    borrower_id = _insert_borrower(db, name="Alex")

    item_id = _insert_item(
        db,
        title="Dune",
        subtitle="",
        authors="Frank Herbert",
        isbn="9780441013593",
        media_type="book",
        publisher="Ace",
        series_name="Dune Saga",
        series_position=1,
        location_id=loc_id,
        abs_id="abs-123",
        abs_library_id="lib-456",
        notes="A note",
    )

    db.execute("INSERT INTO tags (name) VALUES ('sci-fi')")
    tag_id = db.execute("SELECT id FROM tags WHERE name = 'sci-fi'").fetchone()["id"]
    db.execute("INSERT INTO item_tags (item_id, tag_id) VALUES (?, ?)", (item_id, tag_id))

    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
    cover_path = config.COVERS_DIR / f"{item_id}.jpg"
    # Must clear covers.MIN_COVER_SIZE — the reader validates cover bytes the
    # same way the upload path does, so a toy 20-byte "jpeg" would be dropped.
    cover_path.write_bytes(_JPEG)
    db.execute("UPDATE items SET cover_path = ? WHERE id = ?", (f"covers/{item_id}.jpg", item_id))

    db.execute(
        "INSERT INTO series_meta (name, description, source, complete) VALUES (?, ?, ?, ?)",
        ("Dune Saga", "Epic sci-fi series", "manual", 0),
    )

    db.execute(
        "INSERT INTO reading_log (item_id, status, date_started, notes) VALUES (?, ?, ?, ?)",
        (item_id, "reading", "2026-01-01", "started it"),
    )

    db.execute(
        "INSERT INTO checkouts (item_id, borrower_id, due_date, notes) VALUES (?, ?, ?, ?)",
        (item_id, borrower_id, "2026-02-01", "lent to Alex"),
    )

    db.execute(
        "INSERT INTO valuation_history (total_value, priced_count) VALUES (?, ?)",
        (42.50, 1),
    )
    db.execute("COMMIT")
    return item_id


def _load_zip(content: bytes):
    zf = zipfile.ZipFile(io.BytesIO(content))
    manifest = json.loads(zf.read("manifest.json"))
    library = json.loads(zf.read("library.json"))
    return zf, manifest, library


class TestBuildArchive:
    def test_contract_shape(self, db):
        item_id = _seed_full_library(db)
        path = build_archive(db)
        assert path.exists()
        assert path.name == "shelf_archive_tmp.zip"

        with zipfile.ZipFile(path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            library = json.loads(zf.read("library.json"))
            names = zf.namelist()

        # Manifest shape
        assert manifest["format"] == "shelf-archive"
        assert manifest["version"] == 1
        assert manifest["app_version"] is None
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", manifest["exported_at"])
        assert manifest["counts"]["items"] == 1
        assert manifest["counts"]["covers"] == 1
        assert manifest["counts"]["series"] == 1

        # Items: name-refs, archive-local id, dropped columns
        assert len(library["items"]) == 1
        item = library["items"][0]
        assert item["id"] == 1  # archive-local, not the real db id
        assert item["title"] == "Dune"
        assert item["location"] == "Living Room"
        assert item["tags"] == ["sci-fi"]
        assert item["cover"] == "covers/1.jpg"
        for forbidden in ("abs_id", "abs_library_id", "location_id", "cover_path"):
            assert forbidden not in item

        # Cover file present under covers/<archive-local id>.jpg
        assert "covers/1.jpg" in names

        # locations/tags/borrowers by name
        assert library["locations"] == [{"name": "Living Room", "sort_order": 0}]
        assert library["tags"] == [{"name": "sci-fi"}]
        assert library["borrowers"] == [{"name": "Alex"}]

        # series
        assert library["series"][0]["name"] == "Dune Saga"
        assert library["series"][0]["description"] == "Epic sci-fi series"

        # reading_log / checkouts reference the archive-local item id
        assert library["reading_log"][0]["item_id"] == item["id"]
        assert library["reading_log"][0]["status"] == "reading"
        assert library["checkouts"][0]["item_id"] == item["id"]
        assert library["checkouts"][0]["borrower"] == "Alex"

        # valuation_history
        assert library["valuation_history"][0]["total_value"] == 42.50

        # No excluded tables anywhere in the archive
        combined = json.dumps(manifest) + json.dumps(library)
        for excluded in ("share_links", "scan_log", "log_entries"):
            assert excluded not in combined
        assert "users" not in library
        assert "settings" not in library
        # "username"/"password" would only appear if users data leaked in
        assert "password" not in combined

    def test_cover_missing_file_exports_cleanly(self, db):
        item_id = _insert_item(db, title="Ghost Cover", isbn="9780000000099")
        db.execute(
            "UPDATE items SET cover_path = ? WHERE id = ?",
            (f"covers/{item_id}.jpg", item_id),
        )
        db.execute("COMMIT")
        # Deliberately do NOT write the file to disk.

        path = build_archive(db)
        with zipfile.ZipFile(path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            library = json.loads(zf.read("library.json"))
            names = zf.namelist()

        assert manifest["counts"]["covers"] == 0
        assert "cover" not in library["items"][0]
        assert not any(n.startswith("covers/") for n in names)

    def test_empty_library(self, db):
        path = build_archive(db)
        with zipfile.ZipFile(path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            library = json.loads(zf.read("library.json"))
        assert manifest["counts"]["items"] == 0
        assert manifest["counts"]["covers"] == 0
        assert library["items"] == []

    def test_overwrites_previous_tmp_archive(self, db):
        _insert_item(db, title="First", isbn="9780000000001")
        db.execute("COMMIT")
        path1 = build_archive(db)
        size1 = path1.stat().st_size

        _insert_item(db, title="Second", isbn="9780000000002")
        db.execute("COMMIT")
        path2 = build_archive(db)

        assert path1 == path2
        with zipfile.ZipFile(path2) as zf:
            library = json.loads(zf.read("library.json"))
        assert len(library["items"]) == 2
        # sanity: the file actually changed (not stale content)
        assert path2.stat().st_size != size1 or len(library["items"]) == 2


class TestExportArchiveEndpoint:
    def test_admin_gets_zip(self, admin_client, db):
        _seed_full_library(db)
        resp = admin_client.get("/api/export/archive")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert re.search(r'filename="?shelf_archive_\d{8}\.zip"?', resp.headers["content-disposition"])

        zf, manifest, library = _load_zip(resp.content)
        assert manifest["format"] == "shelf-archive"
        assert len(library["items"]) == 1

    def test_editor_forbidden(self, editor_client, db):
        _seed_full_library(db)
        resp = editor_client.get("/api/export/archive")
        assert resp.status_code == 403

    def test_viewer_forbidden(self, viewer_client, db):
        _seed_full_library(db)
        resp = viewer_client.get("/api/export/archive")
        assert resp.status_code == 403


class TestReaderLayoutGuards:
    """Zip-slip and layout: the reader allowlists an exact layout, so every
    traversal shape fails without being enumerated."""

    @pytest.mark.parametrize("bad_name", [
        "../escape.json",
        "covers/../../etc/passwd",
        "/etc/passwd",
        "covers//nested.jpg",
        "covers/sub/dir.jpg",
        "covers/../x.jpg",
        "C:/Windows/system.ini",
        "notes.txt",
        "covers/.hidden",
    ])
    def test_rejects_unexpected_entry_names(self, tmp_path, bad_name):
        p = _write_zip(tmp_path / "a.zip", [(bad_name, _JPEG)])
        with pytest.raises(ArchiveError):
            read_archive(p)

    def test_rejects_backslash_separator(self, tmp_path):
        # zipfile normalizes "\" in writestr names, so forge the entry.
        p = tmp_path / "a.zip"
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", _MANIFEST)
            zf.writestr("library.json", _LIBRARY)
            info = zipfile.ZipInfo("covers/x.jpg")
            zf.writestr(info, _JPEG)
            zf.filelist[-1].filename = "covers\\..\\..\\x.jpg"
        with pytest.raises(ArchiveError):
            read_archive(p)

    def test_rejects_symlink_entry(self, tmp_path):
        p = tmp_path / "a.zip"
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", _MANIFEST)
            zf.writestr("library.json", _LIBRARY)
            info = zipfile.ZipInfo("covers/link.jpg")
            info.external_attr = (0o120777 << 16)  # S_IFLNK
            zf.writestr(info, "/etc/passwd")
        with pytest.raises(ArchiveError, match="symlink"):
            read_archive(p)

    def test_rejects_duplicate_entries(self, tmp_path):
        p = tmp_path / "a.zip"
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", _MANIFEST)
            zf.writestr("library.json", _LIBRARY)
            zf.writestr("covers/1.jpg", _JPEG)
            zf.writestr("covers/1.jpg", b"\xff\xd8\xffdifferent")
        with pytest.raises(ArchiveError, match="duplicate"):
            read_archive(p)

    def test_rejects_entry_count_bomb(self, tmp_path, monkeypatch):
        monkeypatch.setattr(archive_svc, "MAX_ENTRIES", 3)
        p = _write_zip(tmp_path / "a.zip",
                       [(f"covers/{i}.jpg", _JPEG) for i in range(5)])
        with pytest.raises(ArchiveError, match="too many entries"):
            read_archive(p)

    def test_rejects_missing_manifest(self, tmp_path):
        p = _write_zip(tmp_path / "a.zip", [], manifest=None)
        with pytest.raises(ArchiveError, match="manifest.json"):
            read_archive(p)

    def test_rejects_missing_library(self, tmp_path):
        p = _write_zip(tmp_path / "a.zip", [], library=None)
        with pytest.raises(ArchiveError, match="library.json"):
            read_archive(p)

    def test_rejects_non_zip_bytes(self, tmp_path):
        p = tmp_path / "a.zip"
        p.write_bytes(b"this is definitely not a zip file")
        with pytest.raises(ArchiveError, match="not a zip"):
            read_archive(p)

    def test_rejects_missing_file(self, tmp_path):
        with pytest.raises(ArchiveError):
            read_archive(tmp_path / "nope.zip")


class TestReaderManifestGate:
    def test_rejects_foreign_format(self, tmp_path):
        p = _write_zip(tmp_path / "a.zip", [],
                       manifest=json.dumps({"format": "other-app", "version": 1}))
        with pytest.raises(ArchiveError, match="not a Shelf portable archive"):
            read_archive(p)

    def test_rejects_future_version(self, tmp_path):
        p = _write_zip(tmp_path / "a.zip", [],
                       manifest=json.dumps({"format": "shelf-archive", "version": 2}))
        with pytest.raises(ArchiveError, match="newer version of Shelf"):
            read_archive(p)

    @pytest.mark.parametrize("version", [0, -1, "1", 1.0, None, True])
    def test_rejects_bogus_version(self, tmp_path, version):
        p = _write_zip(tmp_path / "a.zip", [],
                       manifest=json.dumps({"format": "shelf-archive", "version": version}))
        with pytest.raises(ArchiveError, match="unrecognized format version"):
            read_archive(p)

    def test_rejects_unparseable_manifest(self, tmp_path):
        p = _write_zip(tmp_path / "a.zip", [], manifest="{not json")
        with pytest.raises(ArchiveError, match="not valid JSON"):
            read_archive(p)

    def test_rejects_non_object_manifest(self, tmp_path):
        p = _write_zip(tmp_path / "a.zip", [], manifest="[1, 2, 3]")
        with pytest.raises(ArchiveError, match="wrong shape"):
            read_archive(p)


class TestReaderSizeGuards:
    def test_rejects_oversized_library_json(self, tmp_path, monkeypatch):
        p = _write_zip(tmp_path / "a.zip", [],
                       library=json.dumps({"items": [{"title": "x" * 5000}]}))
        with read_archive(p) as reader:   # manifest read under the real cap
            monkeypatch.setattr(archive_svc, "MAX_JSON_SIZE", 1024)
            with pytest.raises(ArchiveError, match="too large"):
                reader.library

    def test_rejects_declared_total_over_budget(self, tmp_path, monkeypatch):
        # A header claiming a huge uncompressed size is refused up front,
        # before a single byte is decompressed.
        monkeypatch.setattr(archive_svc, "MAX_TOTAL_UNCOMPRESSED", 10_000)
        p = tmp_path / "a.zip"
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", _MANIFEST)
            zf.writestr("library.json", _LIBRARY)
            zf.writestr("covers/1.jpg", _JPEG)
            zf.filelist[-1].file_size = 5 * 1024 * 1024 * 1024  # the lie
        with pytest.raises(ArchiveError, match="too large when uncompressed"):
            read_archive(p)

    def test_oversized_cover_rejected_on_actual_read(self, tmp_path):
        # Honest header, ruinous ratio: 10 MB of zeros deflates to nothing.
        # The cap has to bite on bytes pulled out of the decompressor.
        bomb = b"\xff\xd8\xff\xe0" + b"\0" * (archive_svc.MAX_COVER_SIZE + 1024)
        p = _write_zip(tmp_path / "a.zip", [("covers/1.jpg", bomb)])
        assert p.stat().st_size < 100_000  # it really is a compression bomb
        with read_archive(p) as reader:
            assert reader.read_cover("covers/1.jpg") is None

    def test_read_budget_spans_entries(self, tmp_path):
        # White-box: the cumulative read budget sits *behind* the declared-size
        # check, so an honest zip can never reach it. Drive it directly to
        # prove the backstop works if a header ever understates a size.
        blob = _JPEG + b"\0" * 900
        p = _write_zip(tmp_path / "a.zip",
                       [(f"covers/{i}.jpg", blob) for i in range(4)])
        with read_archive(p) as reader:
            reader._budget = 2 * len(blob)
            results = [reader.read_cover(f"covers/{i}.jpg") for i in range(4)]
        assert results[0] is not None
        assert results[-1] is None


class TestReaderCoverAccessor:
    def test_valid_cover_returns_bytes(self, tmp_path):
        p = _write_zip(tmp_path / "a.zip", [("covers/1.jpg", _JPEG)])
        with read_archive(p) as reader:
            assert reader.cover_names == {"covers/1.jpg"}
            assert reader.read_cover("covers/1.jpg") == _JPEG

    def test_non_image_cover_rejected(self, tmp_path):
        p = _write_zip(tmp_path / "a.zip",
                       [("covers/1.jpg", b"<?php system($_GET['c']); ?>" + b"x" * 200)])
        with read_archive(p) as reader:
            assert reader.read_cover("covers/1.jpg") is None

    def test_undersized_cover_rejected(self, tmp_path):
        p = _write_zip(tmp_path / "a.zip", [("covers/1.jpg", b"\xff\xd8\xff")])
        with read_archive(p) as reader:
            assert reader.read_cover("covers/1.jpg") is None

    def test_absent_cover_returns_none(self, tmp_path):
        p = _write_zip(tmp_path / "a.zip", [("covers/1.jpg", _JPEG)])
        with read_archive(p) as reader:
            assert reader.read_cover("covers/999.jpg") is None
            # A traversal string never resolves to an entry either.
            assert reader.read_cover("../../etc/passwd") is None


class TestReaderLibraryShape:
    def test_missing_keys_default_to_empty_lists(self, tmp_path):
        p = _write_zip(tmp_path / "a.zip", [], library=json.dumps({"items": []}))
        with read_archive(p) as reader:
            assert reader.library["checkouts"] == []
            assert reader.library["series"] == []

    def test_rejects_wrong_typed_key(self, tmp_path):
        p = _write_zip(tmp_path / "a.zip", [], library=json.dumps({"items": {"a": 1}}))
        with read_archive(p) as reader:
            with pytest.raises(ArchiveError, match="wrong shape"):
                reader.library

    def test_unknown_keys_are_preserved(self, tmp_path):
        p = _write_zip(tmp_path / "a.zip", [],
                       library=json.dumps({"items": [], "item_links": [{"a": 1}]}))
        with read_archive(p) as reader:
            assert reader.library["item_links"] == [{"a": 1}]


class TestReaderSelfCompatibility:
    def test_reader_accepts_exporter_output(self, db):
        item_id = _seed_full_library(db)
        path = build_archive(db)
        with read_archive(path) as reader:
            assert reader.manifest["format"] == "shelf-archive"
            assert reader.manifest["version"] == 1
            lib = reader.library
            assert len(lib["items"]) == 1
            cover_name = lib["items"][0]["cover"]
            assert cover_name in reader.cover_names
            assert reader.read_cover(cover_name) == (
                config.COVERS_DIR / f"{item_id}.jpg"
            ).read_bytes()


# ---------------------------------------------------------------------------
# T3 — merge importer
# ---------------------------------------------------------------------------

def _wipe_library(db):
    """Simulate moving to a fresh instance: clear every archive-covered
    table and the covers dir. users/settings/scan_log/etc. are out of scope
    for the archive format and are left alone."""
    for table in ("item_tags", "checkouts", "reading_log", "valuation_history",
                  "items", "tags", "locations", "borrowers", "series_meta"):
        db.execute(f"DELETE FROM {table}")
    db.execute("COMMIT")
    if config.COVERS_DIR.exists():
        shutil.rmtree(config.COVERS_DIR)
    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)


def _item_key(item: dict):
    """Dedupe key mirroring the importer's own: (isbn, media_type), or
    casefolded (title, authors, media_type) when ISBN-less."""
    isbn = (item.get("isbn") or "").strip()
    if isbn:
        return ("isbn", isbn, item.get("media_type"))
    return (
        "ta",
        (item.get("title") or "").casefold(),
        (item.get("authors") or "").casefold(),
        item.get("media_type"),
    )


def _normalize_library(library: dict) -> dict:
    """Make two library.json payloads comparable across an export -> import
    -> export round trip: archive-local ids are reassigned per run, so items
    are keyed by their dedupe identity instead, and reading_log/checkouts
    reference that same key rather than a raw id. `cover` collapses to a
    presence flag — the round-trip test checks cover *bytes* separately.

    Items compare as a sorted LIST, not a dict keyed by identity: two items
    can legitimately share a dedupe key (same title + author, no ISBN), and
    keying them into a dict would silently collapse both sides — hiding
    exactly the item-loss this round trip exists to catch."""
    id_to_key = {it["id"]: _item_key(it) for it in library["items"]}
    items_norm = []
    for it in library["items"]:
        d = dict(it)
        d.pop("id", None)
        d["cover"] = bool(d.get("cover"))
        items_norm.append(d)
    items_norm.sort(key=lambda d: json.dumps(d, sort_keys=True, default=str))

    def remap(rows):
        out = []
        for r in rows:
            d = dict(r)
            d["item_id"] = id_to_key.get(d["item_id"])
            out.append(d)
        out.sort(key=lambda d: json.dumps(d, sort_keys=True, default=str))
        return out

    return {
        "items": items_norm,
        "locations": sorted(library["locations"], key=lambda x: x["name"].casefold()),
        "tags": sorted(library["tags"], key=lambda x: x["name"].casefold()),
        "borrowers": sorted(library["borrowers"], key=lambda x: x["name"].casefold()),
        "series": sorted(library["series"], key=lambda x: x["name"].casefold()),
        "reading_log": remap(library["reading_log"]),
        "checkouts": remap(library["checkouts"]),
        "valuation_history": sorted(
            library["valuation_history"], key=lambda x: json.dumps(x, sort_keys=True, default=str)
        ),
    }


class TestRoundTripFreshInstance:
    """The feature's definition of done: export a seeded library, wipe every
    library table + the covers dir (a fresh instance), import the archive,
    export again — the two library.json payloads must be semantically
    identical (ids normalized) and the covers byte-identical."""

    def test_round_trip(self, db):
        _seed_full_library(db)
        original_path = build_archive(db)
        with zipfile.ZipFile(original_path) as zf:
            original_library = json.loads(zf.read("library.json"))
            original_covers = {n: zf.read(n) for n in zf.namelist() if n.startswith("covers/")}

        _wipe_library(db)

        with read_archive(original_path) as reader:
            report = merge_archive(db, reader, mode="skip")
        db.execute("COMMIT")

        assert report["imported"] == len(original_library["items"])
        assert report["errors"] == []
        assert report["covers_installed"] == len(original_covers)

        reimport_path = build_archive(db)
        with zipfile.ZipFile(reimport_path) as zf:
            new_library = json.loads(zf.read("library.json"))
            new_covers = {n: zf.read(n) for n in zf.namelist() if n.startswith("covers/")}

        assert len(new_library["items"]) == len(original_library["items"])
        assert _normalize_library(original_library) == _normalize_library(new_library)
        assert sorted(original_covers.values()) == sorted(new_covers.values())

    def test_round_trip_with_language(self, db):
        """Language column survives export→import→export round trip."""
        loc_id = _insert_location(db, name="Shelf")
        item_id = _insert_item(
            db,
            title="Dune",
            authors="Frank Herbert",
            isbn="9780441013593",
            media_type="book",
            location_id=loc_id,
        )
        # Seed the item with language='de' (German)
        db.execute("UPDATE items SET language = ? WHERE id = ?", ("de", item_id))
        db.execute("COMMIT")

        original_path = build_archive(db)
        with zipfile.ZipFile(original_path) as zf:
            original_library = json.loads(zf.read("library.json"))

        # Verify language is in the export
        assert original_library["items"][0]["language"] == "de"

        _wipe_library(db)

        with read_archive(original_path) as reader:
            report = merge_archive(db, reader, mode="skip")
        db.execute("COMMIT")

        assert report["imported"] == 1
        assert report["errors"] == []

        # Verify the imported item has language='de' (query by title, not id, since id changed)
        imported_item = db.execute("SELECT language FROM items WHERE title = 'Dune'").fetchone()
        assert imported_item is not None
        assert imported_item["language"] == "de"

        # Export again and verify language survived the round trip
        reimport_path = build_archive(db)
        with zipfile.ZipFile(reimport_path) as zf:
            new_library = json.loads(zf.read("library.json"))

        assert new_library["items"][0]["language"] == "de"


class TestRoundTripDuplicateDedupeKeys:
    """Regression: a library holding two genuinely distinct items under one
    dedupe key must survive a fresh-instance restore intact.

    Found against a real 665-item library, 74% of it ISBN-less — where
    (title, authors) is the primary dedupe path and repeats are ordinary.
    Before the fix the importer matched an archive item against a row it had
    itself inserted moments earlier in the same run, so the second copy was
    skipped and silently lost.
    """

    def _seed_pair(self, db):
        # Same title + authors + media_type, no ISBN: one dedupe key, two rows.
        _insert_item(db, title="Automate the Boring Stuff",
                     authors="Al Sweigart", media_type="ebook", isbn=None)
        _insert_item(db, title="automate the boring stuff",  # casefold collision too
                     authors="al sweigart", media_type="ebook", isbn=None)
        # The ISBN path can't collide: items has UNIQUE(isbn, media_type), so
        # a duplicate pair is unrepresentable there. This is the only path
        # where one dedupe key can cover two rows.
        db.execute("COMMIT")

    def test_duplicates_survive_fresh_instance_restore(self, db):
        self._seed_pair(db)
        original_path = build_archive(db)
        with zipfile.ZipFile(original_path) as zf:
            original_library = json.loads(zf.read("library.json"))
        assert len(original_library["items"]) == 2

        _wipe_library(db)

        with read_archive(original_path) as reader:
            report = merge_archive(db, reader, mode="skip")
        db.execute("COMMIT")

        assert report["imported"] == 2, report
        assert report["skipped"] == 0, report
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 2

        reimport_path = build_archive(db)
        with zipfile.ZipFile(reimport_path) as zf:
            new_library = json.loads(zf.read("library.json"))
        assert len(new_library["items"]) == 2
        assert _normalize_library(original_library) == _normalize_library(new_library)

    def test_still_dedupes_against_rows_that_predate_the_import(self, db):
        """The bound is 'created by this import', not 'dedupe is off' — a
        second import of the same archive must still skip everything."""
        self._seed_pair(db)
        path = build_archive(db)

        with read_archive(path) as reader:
            report = merge_archive(db, reader, mode="skip")
        # No COMMIT: everything was skipped, so no write transaction is open.

        assert report["imported"] == 0, report
        assert report["skipped"] == 2, report
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 2


class TestArchiveBackwardsCompatibilityLanguage:
    """Archives created before language support must import cleanly with
    language ending up NULL."""

    def test_import_archive_without_language_key(self, db, tmp_path):
        """An archive missing the language key on items imports clean,
        resulting in NULL language values."""
        library = {
            "items": [{
                "id": 1, "title": "Dune", "authors": "Frank Herbert",
                "isbn": "9780441013593", "media_type": "book",
                # Note: no "language" key here
            }],
        }
        p = _write_zip(tmp_path / "no_language.zip", [], library=json.dumps(library))

        with read_archive(p) as reader:
            report = merge_archive(db, reader, mode="skip")

        assert report["imported"] == 1
        assert report["errors"] == []

        # Verify the imported item has language=NULL
        item = db.execute("SELECT language FROM items WHERE title = 'Dune'").fetchone()
        assert item["language"] is None


class TestMergeSkipModeNonEmptyLibrary:
    def test_dupe_skipped_history_and_cover_untouched(self, db, tmp_path):
        existing_id = _insert_item(db, title="Dune", authors="Frank Herbert", isbn="9780441013593")
        db.execute("INSERT INTO reading_log (item_id, status) VALUES (?, ?)", (existing_id, "read"))
        borrower_id = _insert_borrower(db, name="Alex")
        db.execute("INSERT INTO checkouts (item_id, borrower_id) VALUES (?, ?)", (existing_id, borrower_id))
        config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
        cover_path = config.COVERS_DIR / f"{existing_id}.jpg"
        cover_path.write_bytes(_JPEG)
        db.execute("UPDATE items SET cover_path = ? WHERE id = ?", (f"covers/{existing_id}.jpg", existing_id))
        db.execute("COMMIT")

        reading_log_count = db.execute("SELECT COUNT(*) c FROM reading_log").fetchone()["c"]
        checkouts_count = db.execute("SELECT COUNT(*) c FROM checkouts").fetchone()["c"]
        original_cover_bytes = cover_path.read_bytes()

        library = {
            "items": [{
                "id": 1, "title": "Dune (would-be update)", "authors": "Frank Herbert",
                "isbn": "9780441013593", "media_type": "book", "cover": "covers/1.jpg",
                "notes": "should never land",
            }],
        }
        p = _write_zip(tmp_path / "a.zip", [("covers/1.jpg", b"\xff\xd8\xff\xe0" + b"1" * 300)],
                       library=json.dumps(library))

        with read_archive(p) as reader:
            report = merge_archive(db, reader, mode="skip")

        assert report == {
            "imported": 0, "updated": 0, "skipped": 1, "errors": [],
            "covers_installed": 0, "format": "shelf-archive",
        }
        row = db.execute("SELECT title, notes, cover_path FROM items WHERE id = ?", (existing_id,)).fetchone()
        assert row["title"] == "Dune"
        assert row["notes"] is None
        assert row["cover_path"] == f"covers/{existing_id}.jpg"
        assert cover_path.read_bytes() == original_cover_bytes
        assert db.execute("SELECT COUNT(*) c FROM reading_log").fetchone()["c"] == reading_log_count
        assert db.execute("SELECT COUNT(*) c FROM checkouts").fetchone()["c"] == checkouts_count
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 1


class TestMergeUpdateModeNonEmptyLibrary:
    def test_metadata_refreshed_and_cover_installed_when_local_absent(self, db, tmp_path):
        existing_id = _insert_item(db, title="Dune", authors="Frank Herbert", isbn="9780441013593")
        db.execute("COMMIT")

        library = {
            "items": [{
                "id": 1, "title": "Dune", "authors": "Frank Herbert", "isbn": "9780441013593",
                "media_type": "book", "publisher": "Ace", "page_count": 412,
                "cover": "covers/1.jpg", "location": "Living Room", "tags": ["sci-fi"],
            }],
        }
        p = _write_zip(tmp_path / "a.zip", [("covers/1.jpg", _JPEG)], library=json.dumps(library))

        with read_archive(p) as reader:
            report = merge_archive(db, reader, mode="update")

        assert report["imported"] == 0
        assert report["updated"] == 1
        assert report["covers_installed"] == 1
        row = db.execute(
            "SELECT items.publisher, items.page_count, items.cover_path, locations.name AS location_name "
            "FROM items LEFT JOIN locations ON locations.id = items.location_id WHERE items.id = ?",
            (existing_id,),
        ).fetchone()
        assert row["publisher"] == "Ace"
        assert row["page_count"] == 412
        assert row["cover_path"] == f"covers/{existing_id}.jpg"
        assert row["location_name"] == "Living Room"
        assert (config.COVERS_DIR / f"{existing_id}.jpg").read_bytes() == _JPEG
        tags = [r["name"] for r in db.execute(
            "SELECT tags.name FROM item_tags JOIN tags ON tags.id = item_tags.tag_id WHERE item_id = ?",
            (existing_id,),
        ).fetchall()]
        assert tags == ["sci-fi"]

    def _seed_local_cover(self, db, tmp_path):
        """An updated item that already has a hand-picked cover on disk, plus
        an archive offering a different one for it."""
        existing_id = _insert_item(db, title="Dune", isbn="9780441013593")
        config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
        local_cover = config.COVERS_DIR / f"{existing_id}.jpg"
        old_bytes = b"\xff\xd8\xff\xe0" + b"OLD" * 100
        local_cover.write_bytes(old_bytes)
        db.execute("UPDATE items SET cover_path = ? WHERE id = ?",
                   (f"covers/{existing_id}.jpg", existing_id))
        db.execute("COMMIT")

        new_bytes = b"\xff\xd8\xff\xe0" + b"NEW" * 100
        library = {"items": [{"id": 1, "title": "Dune", "isbn": "9780441013593",
                              "media_type": "book", "cover": "covers/1.jpg"}]}
        p = _write_zip(tmp_path / "a.zip", [("covers/1.jpg", new_bytes)],
                       library=json.dumps(library))
        return local_cover, old_bytes, new_bytes, p

    def test_existing_cover_is_preserved_by_default(self, db, tmp_path):
        """0.7.0 behavior change: update mode used to overwrite the local
        cover file unconditionally, which destroyed hand-picked covers with
        no way back. Filling a gap is safe; overwriting is not, so it moved
        behind an opt-in."""
        local_cover, old_bytes, _new, p = self._seed_local_cover(db, tmp_path)

        with read_archive(p) as reader:
            report = merge_archive(db, reader, mode="update")

        assert report["updated"] == 1
        assert report["covers_installed"] == 0
        assert local_cover.read_bytes() == old_bytes

    def test_existing_cover_replaced_only_with_replace_covers(self, db, tmp_path):
        local_cover, _old, new_bytes, p = self._seed_local_cover(db, tmp_path)

        with read_archive(p) as reader:
            report = merge_archive(db, reader, mode="update", replace_covers=True)

        assert report["updated"] == 1
        # merge_archive folds replacements into covers_installed to keep the
        # v1 report shape; apply_plan reports them separately.
        assert report["covers_installed"] == 1
        assert local_cover.read_bytes() == new_bytes


class TestMergeIsbnLessDedupe:
    def test_title_authors_fallback_dedupe(self, db, tmp_path):
        _insert_item(db, title="Local Zine", authors="Some Author", isbn=None, media_type="comic")
        db.execute("COMMIT")

        library = {"items": [{"id": 1, "title": "Local Zine", "authors": "Some Author",
                               "media_type": "comic", "notes": "matched by title+authors"}]}
        p = _write_zip(tmp_path / "a.zip", [], library=json.dumps(library))

        with read_archive(p) as reader:
            report = merge_archive(db, reader, mode="skip")

        assert report["skipped"] == 1
        assert report["imported"] == 0
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 1
        # Confirms the match, not a coincidence: local row is untouched.
        assert db.execute("SELECT notes FROM items").fetchone()["notes"] is None

    def test_title_authors_mismatch_creates_new_item(self, db, tmp_path):
        _insert_item(db, title="Local Zine", authors="Some Author", isbn=None, media_type="comic")
        db.execute("COMMIT")

        library = {"items": [{"id": 1, "title": "Local Zine", "authors": "Different Author",
                               "media_type": "comic"}]}
        p = _write_zip(tmp_path / "a.zip", [], library=json.dumps(library))

        with read_archive(p) as reader:
            report = merge_archive(db, reader, mode="skip")

        assert report["imported"] == 1
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 2


class TestMergeCaseInsensitiveNameReuse:
    def test_reuses_existing_location_tag_series_borrower(self, db, tmp_path):
        loc_id = _insert_location(db, name="Living Room")
        db.execute("INSERT INTO tags (name) VALUES ('Sci-Fi')")
        tag_id = db.execute("SELECT id FROM tags WHERE name = 'Sci-Fi'").fetchone()["id"]
        borrower_id = _insert_borrower(db, name="Alex")
        db.execute(
            "INSERT INTO series_meta (name, description) VALUES (?, ?)",
            ("Dune Saga", "Local synopsis, must not be overwritten"),
        )
        db.execute("COMMIT")

        library = {
            "items": [{
                "id": 1, "title": "Dune Messiah", "isbn": "9780441172696", "media_type": "book",
                "location": "living room", "tags": ["sci-fi"], "series_name": "dune saga",
            }],
            "locations": [{"name": "LIVING ROOM", "sort_order": 0}],
            "tags": [{"name": "SCI-FI"}],
            "borrowers": [{"name": "ALEX"}],
            "series": [{"name": "DUNE SAGA", "description": "Should be ignored"}],
            "checkouts": [{"item_id": 1, "borrower": "alex", "notes": "lent it out"}],
        }
        p = _write_zip(tmp_path / "a.zip", [], library=json.dumps(library))

        with read_archive(p) as reader:
            report = merge_archive(db, reader, mode="skip")

        assert report["imported"] == 1
        assert db.execute("SELECT COUNT(*) c FROM locations").fetchone()["c"] == 1
        assert db.execute("SELECT COUNT(*) c FROM tags").fetchone()["c"] == 1
        assert db.execute("SELECT COUNT(*) c FROM borrowers").fetchone()["c"] == 1
        assert db.execute("SELECT COUNT(*) c FROM series_meta").fetchone()["c"] == 1
        assert db.execute(
            "SELECT description FROM series_meta WHERE name = 'Dune Saga'"
        ).fetchone()["description"] == "Local synopsis, must not be overwritten"

        new_item = db.execute("SELECT id, location_id FROM items WHERE title = 'Dune Messiah'").fetchone()
        assert new_item["location_id"] == loc_id
        item_tag = db.execute(
            "SELECT tag_id FROM item_tags WHERE item_id = ?", (new_item["id"],)
        ).fetchone()
        assert item_tag["tag_id"] == tag_id
        checkout = db.execute(
            "SELECT borrower_id FROM checkouts WHERE item_id = ?", (new_item["id"],)
        ).fetchone()
        assert checkout["borrower_id"] == borrower_id


class TestMergeReportCounts:
    def test_counts_match_actions(self, db, tmp_path):
        _insert_item(db, title="Skip Me", isbn="9780000000010")
        db.execute("COMMIT")

        library = {
            "items": [
                {"id": 1, "title": "Skip Me", "isbn": "9780000000010", "media_type": "book"},
                {"id": 2, "title": "Brand New", "isbn": "9780000000030", "media_type": "book"},
                {"id": 3, "title": "", "isbn": "9780000000040", "media_type": "book"},
            ],
        }
        p = _write_zip(tmp_path / "a.zip", [], library=json.dumps(library))

        with read_archive(p) as reader:
            report = merge_archive(db, reader, mode="skip")

        assert report == {
            "imported": 1, "updated": 0, "skipped": 1,
            "errors": ["Archive item 3: missing title"],
            "covers_installed": 0, "format": "shelf-archive",
        }

    def test_valuation_history_skipped_into_non_empty_table_and_noted(self, db, tmp_path):
        db.execute("INSERT INTO valuation_history (total_value, priced_count) VALUES (10, 1)")
        db.execute("COMMIT")

        library = {"items": [], "valuation_history": [{"total_value": 99, "priced_count": 5}]}
        p = _write_zip(tmp_path / "a.zip", [], library=json.dumps(library))

        with read_archive(p) as reader:
            report = merge_archive(db, reader, mode="skip")

        assert db.execute("SELECT COUNT(*) c FROM valuation_history").fetchone()["c"] == 1
        assert any("valuation_history" in e for e in report["errors"])


class TestNoNetworkOnImport:
    """Unit tests are offline by construction — the importer must never open
    an HTTP client, since covers come from the zip only."""

    def test_merge_never_constructs_http_client(self, db, monkeypatch):
        import httpx

        def _boom(*a, **k):
            raise AssertionError("an HTTP client was constructed during import")

        monkeypatch.setattr(httpx, "AsyncClient", _boom)
        monkeypatch.setattr(httpx, "Client", _boom)

        _seed_full_library(db)
        path = build_archive(db)
        _wipe_library(db)

        with read_archive(path) as reader:
            report = merge_archive(db, reader, mode="skip")

        assert report["imported"] == 1
        assert report["covers_installed"] == 1


class TestImportArchiveEndpoint:
    def test_admin_can_import(self, admin_client, db):
        _seed_full_library(db)
        export_resp = admin_client.get("/api/export/archive")
        assert export_resp.status_code == 200

        _wipe_library(db)  # simulate a fresh instance

        import_resp = admin_client.post(
            "/api/import/archive",
            files={"file": ("shelf_archive.zip", io.BytesIO(export_resp.content), "application/zip")},
            data={"mode": "skip"},
        )
        assert import_resp.status_code == 200
        report = import_resp.json()
        assert report["imported"] == 1
        assert report["covers_installed"] == 1
        assert report["format"] == "shelf-archive"

    def test_editor_forbidden(self, editor_client):
        resp = editor_client.post(
            "/api/import/archive",
            files={"file": ("a.zip", io.BytesIO(b"not a zip"), "application/zip")},
            data={"mode": "skip"},
        )
        assert resp.status_code == 403

    def test_viewer_forbidden(self, viewer_client):
        resp = viewer_client.post(
            "/api/import/archive",
            files={"file": ("a.zip", io.BytesIO(b"not a zip"), "application/zip")},
            data={"mode": "skip"},
        )
        assert resp.status_code == 403

    def test_non_zip_upload_returns_archive_error_message_not_500(self, admin_client):
        resp = admin_client.post(
            "/api/import/archive",
            files={"file": ("a.zip", io.BytesIO(b"this is definitely not a zip file"), "application/zip")},
            data={"mode": "skip"},
        )
        assert resp.status_code != 500
        body = resp.json()
        assert "error" in body
        assert "not a zip" in body["error"]

    def test_no_file_uploaded(self, admin_client):
        resp = admin_client.post("/api/import/archive", data={"mode": "skip"})
        assert resp.status_code != 500
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# Import planner — pure classification (.devdocs/archive/completed/plan-import-preview-plan-apply.md)
# ---------------------------------------------------------------------------

_LIBRARY_TABLES = (
    "items", "item_tags", "locations", "tags", "borrowers", "series_meta",
    "reading_log", "checkouts", "valuation_history",
)


def _library_snapshot(db) -> dict:
    """Every row of every archive-covered table, plus the covers dir listing —
    the evidence that planning changed nothing at all."""
    snap = {}
    for table in _LIBRARY_TABLES:
        rows = db.execute(f"SELECT * FROM {table}").fetchall()
        snap[table] = sorted(tuple(r) for r in rows)
    covers = []
    if config.COVERS_DIR.exists():
        covers = sorted((p.name, p.read_bytes()) for p in config.COVERS_DIR.iterdir() if p.is_file())
    snap["__covers__"] = covers
    return snap


def _plan(db, tmp_path, library, entries=(), *, mode="skip", name="a.zip"):
    p = _write_zip(tmp_path / name, list(entries), library=json.dumps(library))
    with read_archive(p) as reader:
        return plan_archive(db, reader, mode=mode)


def _by_ref(plan) -> dict:
    return {rec["ref"]: rec for rec in plan["items"]}


class TestPlanPurity:
    """plan_archive writes nothing — no rows, no get-or-create side effects,
    no cover files. This is the property the whole preview flow rests on."""

    def test_planning_a_populated_db_changes_nothing(self, db, tmp_path):
        _seed_full_library(db)
        before = _library_snapshot(db)

        library = {
            "items": [
                # a match (skipped/updated), a create, and an errored item
                {"id": 1, "title": "Dune", "authors": "Frank Herbert",
                 "isbn": "9780441013593", "media_type": "book", "cover": "covers/1.jpg",
                 "location": "Brand New Room", "tags": ["brand-new-tag"]},
                {"id": 2, "title": "Brand New", "isbn": "9780000000030",
                 "media_type": "book", "cover": "covers/2.jpg",
                 "location": "Another New Room", "tags": ["another-tag"]},
                {"id": 3, "title": "  ", "isbn": "9780000000040", "media_type": "book"},
            ],
            "locations": [{"name": "Unreferenced Room", "sort_order": 3}],
            "tags": [{"name": "unreferenced-tag"}],
            "borrowers": [{"name": "Brand New Borrower"}],
            "series": [{"name": "Brand New Series", "description": "x"}],
            "reading_log": [{"item_id": 2, "status": "read"}],
            "checkouts": [{"item_id": 2, "borrower": "Brand New Borrower"}],
            "valuation_history": [{"total_value": 99, "priced_count": 5}],
        }
        entries = [("covers/1.jpg", _JPEG), ("covers/2.jpg", _JPEG)]

        for mode in ("skip", "update"):
            _plan(db, tmp_path, library, entries, mode=mode, name=f"{mode}.zip")
            assert _library_snapshot(db) == before, f"plan_archive wrote something in {mode} mode"

    def test_plan_never_constructs_http_client(self, db, tmp_path, monkeypatch):
        import httpx

        def _boom(*a, **k):
            raise AssertionError("an HTTP client was constructed during planning")

        monkeypatch.setattr(httpx, "AsyncClient", _boom)
        monkeypatch.setattr(httpx, "Client", _boom)

        _seed_full_library(db)
        path = build_archive(db)
        with read_archive(path) as reader:
            plan = plan_archive(db, reader, mode="update")
        assert plan["summary"]["items_total"] == 1


class TestPlanVerdicts:
    def test_isbn_match_skip_mode(self, db, tmp_path):
        _insert_item(db, title="Dune", isbn="9780441013593")
        db.execute("COMMIT")
        library = {"items": [{"id": 1, "title": "Dune", "isbn": "9780441013593",
                              "media_type": "book"}]}
        plan = _plan(db, tmp_path, library, mode="skip")
        assert plan["mode"] == "skip"
        assert plan["items"] == [{"ref": 1, "title": "Dune", "verdict": "skip",
                                  "basis": "isbn", "cover": "none"}]
        assert plan["summary"]["by_basis"] == {"isbn": 1, "title_authors": 0}

    def test_isbn_match_update_mode(self, db, tmp_path):
        _insert_item(db, title="Dune", isbn="9780441013593")
        db.execute("COMMIT")
        library = {"items": [{"id": 1, "title": "Dune", "isbn": "9780441013593",
                              "media_type": "book"}]}
        plan = _plan(db, tmp_path, library, mode="update")
        assert plan["mode"] == "update"
        assert _by_ref(plan)[1]["verdict"] == "update"
        assert _by_ref(plan)[1]["basis"] == "isbn"

    def test_title_authors_match_both_modes(self, db, tmp_path):
        _insert_item(db, title="Local Zine", authors="Some Author", isbn=None,
                     media_type="comic")
        db.execute("COMMIT")
        # Casefold collision: the fuzzy path is NOCASE, like the merge's.
        library = {"items": [{"id": 1, "title": "local zine", "authors": "SOME AUTHOR",
                              "media_type": "comic"}]}

        skip_plan = _plan(db, tmp_path, library, mode="skip", name="s.zip")
        assert _by_ref(skip_plan)[1]["verdict"] == "skip"
        assert _by_ref(skip_plan)[1]["basis"] == "title_authors"

        update_plan = _plan(db, tmp_path, library, mode="update", name="u.zip")
        assert _by_ref(update_plan)[1]["verdict"] == "update"
        assert _by_ref(update_plan)[1]["basis"] == "title_authors"

    def test_no_match_is_a_create_with_no_basis(self, db, tmp_path):
        _insert_item(db, title="Local Zine", authors="Some Author", isbn=None,
                     media_type="comic")
        db.execute("COMMIT")
        library = {"items": [
            {"id": 1, "title": "Local Zine", "authors": "Different Author",
             "media_type": "comic"},                                  # fuzzy miss
            {"id": 2, "title": "Local Zine", "authors": "Some Author",
             "media_type": "ebook"},                                  # media_type differs
            {"id": 3, "title": "Elsewhere", "isbn": "9780000000030",
             "media_type": "book"},                                   # isbn miss
        ]}
        plan = _plan(db, tmp_path, library, mode="update")
        assert [r["verdict"] for r in plan["items"]] == ["create"] * 3
        assert [r["basis"] for r in plan["items"]] == [None] * 3
        assert plan["summary"]["by_basis"] == {"isbn": 0, "title_authors": 0}

    def test_mixed_matrix_counts(self, db, tmp_path):
        _insert_item(db, title="By Isbn", isbn="9780000000010")
        _insert_item(db, title="By Title", authors="A", isbn=None, media_type="book")
        db.execute("COMMIT")
        library = {"items": [
            {"id": 1, "title": "By Isbn", "isbn": "9780000000010", "media_type": "book"},
            {"id": 2, "title": "By Title", "authors": "A", "media_type": "book"},
            {"id": 3, "title": "New One", "isbn": "9780000000099", "media_type": "book"},
        ]}
        for mode, verdict in (("skip", "skip"), ("update", "update")):
            plan = _plan(db, tmp_path, library, mode=mode, name=f"{mode}.zip")
            s = plan["summary"]
            assert s["create"] == 1
            assert s[verdict] == 2
            assert s["by_basis"] == {"isbn": 1, "title_authors": 1}


class TestPlanCoverActions:
    def test_create_with_cover_installs(self, db, tmp_path):
        library = {"items": [{"id": 1, "title": "New", "isbn": "9780000000010",
                              "media_type": "book", "cover": "covers/1.jpg"}]}
        plan = _plan(db, tmp_path, library, [("covers/1.jpg", _JPEG)])
        assert _by_ref(plan)[1]["cover"] == "install"
        assert plan["summary"]["covers_install"] == 1
        assert plan["summary"]["covers_replace"] == 0

    def test_no_cover_entry_is_none(self, db, tmp_path):
        library = {"items": [{"id": 1, "title": "New", "isbn": "9780000000010",
                              "media_type": "book"}]}
        plan = _plan(db, tmp_path, library)
        assert _by_ref(plan)[1]["cover"] == "none"
        assert plan["summary"]["covers_install"] == 0

    def test_cover_declared_but_absent_from_zip_is_none(self, db, tmp_path):
        library = {"items": [{"id": 1, "title": "New", "isbn": "9780000000010",
                              "media_type": "book", "cover": "covers/1.jpg"}]}
        plan = _plan(db, tmp_path, library)  # no cover entries written
        assert _by_ref(plan)[1]["cover"] == "none"

    def test_matched_item_without_local_cover_installs(self, db, tmp_path):
        _insert_item(db, title="Dune", isbn="9780441013593")
        db.execute("COMMIT")
        library = {"items": [{"id": 1, "title": "Dune", "isbn": "9780441013593",
                              "media_type": "book", "cover": "covers/1.jpg"}]}
        plan = _plan(db, tmp_path, library, [("covers/1.jpg", _JPEG)], mode="update")
        assert _by_ref(plan)[1]["cover"] == "install"
        assert plan["summary"]["covers_install"] == 1
        assert plan["summary"]["covers_replace"] == 0

    def test_matched_item_with_local_cover_is_a_replace(self, db, tmp_path):
        existing_id = _insert_item(db, title="Dune", isbn="9780441013593")
        config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
        (config.COVERS_DIR / f"{existing_id}.jpg").write_bytes(_JPEG)
        db.execute("UPDATE items SET cover_path = ? WHERE id = ?",
                   (f"covers/{existing_id}.jpg", existing_id))
        db.execute("COMMIT")
        library = {"items": [{"id": 1, "title": "Dune", "isbn": "9780441013593",
                              "media_type": "book", "cover": "covers/1.jpg"}]}
        plan = _plan(db, tmp_path, library, [("covers/1.jpg", _JPEG)], mode="update")
        assert _by_ref(plan)[1]["cover"] == "replace"
        assert plan["summary"]["covers_install"] == 0
        assert plan["summary"]["covers_replace"] == 1

    def test_skipped_item_does_no_cover_work(self, db, tmp_path):
        """A skip is a skip: the merge touches no cover on a skipped item,
        whether or not one is already on disk."""
        _insert_item(db, title="Dune", isbn="9780441013593")
        db.execute("COMMIT")
        library = {"items": [{"id": 1, "title": "Dune", "isbn": "9780441013593",
                              "media_type": "book", "cover": "covers/1.jpg"}]}
        plan = _plan(db, tmp_path, library, [("covers/1.jpg", _JPEG)], mode="skip")
        assert _by_ref(plan)[1]["cover"] == "none"
        assert plan["summary"]["covers_install"] == 0
        assert plan["summary"]["covers_replace"] == 0


class TestPlanWouldCreate:
    def test_existing_names_are_not_listed_case_insensitively(self, db, tmp_path):
        _insert_location(db, name="Living Room")
        db.execute("INSERT INTO tags (name) VALUES ('Sci-Fi')")
        _insert_borrower(db, name="Alex")
        db.execute("INSERT INTO series_meta (name) VALUES ('Dune Saga')")
        db.execute("COMMIT")

        library = {
            "items": [{"id": 1, "title": "Dune Messiah", "isbn": "9780441172696",
                       "media_type": "book", "location": "living room",
                       "tags": ["sci-fi", "Owned"]}],
            "locations": [{"name": "LIVING ROOM", "sort_order": 0}, {"name": "Attic"}],
            "tags": [{"name": "SCI-FI"}],
            "borrowers": [{"name": "ALEX"}, {"name": "Sam"}],
            "series": [{"name": "DUNE SAGA"}, {"name": "Foundation"}],
        }
        plan = _plan(db, tmp_path, library)
        wc = plan["summary"]["would_create"]
        assert wc["locations"] == ["Attic"]
        assert wc["tags"] == ["Owned"]
        assert wc["borrowers"] == ["Sam"]
        assert wc["series"] == ["Foundation"]

    def test_skipped_items_contribute_no_names(self, db, tmp_path):
        """Names ride in on items the import actually creates or updates —
        a skipped item's location/tags are never touched by the merge."""
        _insert_item(db, title="Dune", isbn="9780441013593")
        db.execute("COMMIT")
        library = {"items": [{"id": 1, "title": "Dune", "isbn": "9780441013593",
                              "media_type": "book", "location": "Skip Room",
                              "tags": ["skip-tag"]}]}
        plan = _plan(db, tmp_path, library, mode="skip")
        assert plan["summary"]["would_create"]["locations"] == []
        assert plan["summary"]["would_create"]["tags"] == []

    def test_names_are_deduped_across_items(self, db, tmp_path):
        library = {"items": [
            {"id": 1, "title": "One", "isbn": "9780000000010", "media_type": "book",
             "location": "Attic", "tags": ["shared"]},
            {"id": 2, "title": "Two", "isbn": "9780000000020", "media_type": "book",
             "location": "ATTIC", "tags": ["SHARED"]},
        ]}
        plan = _plan(db, tmp_path, library)
        assert plan["summary"]["would_create"]["locations"] == ["Attic"]
        assert plan["summary"]["would_create"]["tags"] == ["shared"]


class TestPlanDuplicateDedupeKeys:
    """The a92b8e7 invariant, at plan time: two archive items sharing one
    dedupe key are two independent creates, not a create plus a skip."""

    def test_duplicate_key_items_are_two_creates(self, db, tmp_path):
        library = {"items": [
            {"id": 1, "title": "Automate the Boring Stuff", "authors": "Al Sweigart",
             "media_type": "ebook"},
            {"id": 2, "title": "automate the boring stuff", "authors": "al sweigart",
             "media_type": "ebook"},
        ]}
        plan = _plan(db, tmp_path, library)
        assert [r["verdict"] for r in plan["items"]] == ["create", "create"]
        assert plan["summary"]["create"] == 2
        assert plan["summary"]["skip"] == 0

    def test_duplicate_key_items_both_match_a_pre_existing_row(self, db, tmp_path):
        """The bound is 'created by this import', not 'dedupe is off'."""
        _insert_item(db, title="Automate the Boring Stuff", authors="Al Sweigart",
                     isbn=None, media_type="ebook")
        db.execute("COMMIT")
        library = {"items": [
            {"id": 1, "title": "Automate the Boring Stuff", "authors": "Al Sweigart",
             "media_type": "ebook"},
            {"id": 2, "title": "Automate the Boring Stuff", "authors": "Al Sweigart",
             "media_type": "ebook"},
        ]}
        plan = _plan(db, tmp_path, library)
        assert [r["verdict"] for r in plan["items"]] == ["skip", "skip"]


class TestPlanSummary:
    def test_counts_reconcile_with_item_records(self, db, tmp_path):
        _insert_item(db, title="Skip Me", isbn="9780000000010")
        db.execute("COMMIT")
        library = {"items": [
            {"id": 1, "title": "Skip Me", "isbn": "9780000000010", "media_type": "book"},
            {"id": 2, "title": "Brand New", "isbn": "9780000000030", "media_type": "book"},
            {"id": 3, "title": "", "isbn": "9780000000040", "media_type": "book"},
        ]}
        plan = _plan(db, tmp_path, library)
        s = plan["summary"]
        assert s["items_total"] == len(plan["items"]) == 2
        assert s["create"] + s["skip"] + s["update"] == s["items_total"]
        assert s["create"] == 1 and s["skip"] == 1 and s["update"] == 0
        assert s["errors"] == ["Archive item 3: missing title"]
        assert sum(s["by_basis"].values()) == s["skip"] + s["update"]

    def test_reading_log_and_checkouts_count_created_items_only(self, db, tmp_path):
        _insert_item(db, title="Skip Me", isbn="9780000000010")
        db.execute("COMMIT")
        library = {
            "items": [
                {"id": 1, "title": "Skip Me", "isbn": "9780000000010", "media_type": "book"},
                {"id": 2, "title": "Brand New", "isbn": "9780000000030", "media_type": "book"},
            ],
            "reading_log": [
                {"item_id": 1, "status": "read"},      # attaches to a skip — lands nowhere
                {"item_id": 2, "status": "read"},
                {"item_id": 2, "status": "reading"},
                {"item_id": 99, "status": "read"},     # dangling ref
            ],
            "checkouts": [
                {"item_id": 1, "borrower": "Alex"},
                {"item_id": 2, "borrower": "Sam"},
            ],
        }
        plan = _plan(db, tmp_path, library)
        assert plan["summary"]["reading_log"] == 2
        assert plan["summary"]["checkouts"] == 1
        # Only the borrower on a checkout that actually lands is a would-create.
        assert plan["summary"]["would_create"]["borrowers"] == ["Sam"]

    def test_valuation_history_mergeability(self, db, tmp_path):
        library = {"items": [], "valuation_history": [{"total_value": 99, "priced_count": 5},
                                                      {"total_value": 12, "priced_count": 1}]}
        plan = _plan(db, tmp_path, library, name="empty.zip")
        assert plan["summary"]["valuation_history"] == {"rows": 2, "mergeable": True}

        db.execute("INSERT INTO valuation_history (total_value, priced_count) VALUES (10, 1)")
        db.execute("COMMIT")
        plan = _plan(db, tmp_path, library, name="full.zip")
        assert plan["summary"]["valuation_history"] == {"rows": 2, "mergeable": False}

    def test_errors_are_capped(self, db, tmp_path):
        library = {"items": [{"id": i, "title": "", "media_type": "book"} for i in range(30)]}
        plan = _plan(db, tmp_path, library)
        assert len(plan["summary"]["errors"]) == 20
        assert plan["items"] == []
        assert plan["summary"]["items_total"] == 0

    def test_unknown_mode_falls_back_to_skip(self, db, tmp_path):
        _insert_item(db, title="Dune", isbn="9780441013593")
        db.execute("COMMIT")
        library = {"items": [{"id": 1, "title": "Dune", "isbn": "9780441013593",
                              "media_type": "book"}]}
        plan = _plan(db, tmp_path, library, mode="destroy-everything")
        assert plan["mode"] == "skip"
        assert _by_ref(plan)[1]["verdict"] == "skip"

    def test_plan_matches_what_the_merge_then_does(self, db, tmp_path):
        """The plan is only worth showing if apply agrees with it. Plan a
        mixed archive, run the merge, and check the verdict counts line up."""
        _insert_item(db, title="Skip Me", isbn="9780000000010")
        db.execute("COMMIT")
        library = {
            "items": [
                {"id": 1, "title": "Skip Me", "isbn": "9780000000010", "media_type": "book"},
                {"id": 2, "title": "Brand New", "isbn": "9780000000030",
                 "media_type": "book", "cover": "covers/2.jpg"},
                {"id": 3, "title": "Also New", "isbn": "9780000000050", "media_type": "book"},
            ],
        }
        p = _write_zip(tmp_path / "a.zip", [("covers/2.jpg", _JPEG)],
                       library=json.dumps(library))
        with read_archive(p) as reader:
            plan = plan_archive(db, reader, mode="skip")
        with read_archive(p) as reader:
            report = merge_archive(db, reader, mode="skip")
        db.execute("COMMIT")

        assert report["imported"] == plan["summary"]["create"]
        assert report["skipped"] == plan["summary"]["skip"]
        assert report["updated"] == plan["summary"]["update"]
        assert report["covers_installed"] == plan["summary"]["covers_install"]


# ---------------------------------------------------------------------------
# Selective apply — plan/apply with selection and drift handling
# ---------------------------------------------------------------------------

def _plan_and_apply(db, path, *, mode="skip", selection=None, mutate=None):
    """Plan an archive, optionally mutate the DB in between (drift), then
    apply the plan. Two reader instances, as the real two-request flow uses."""
    with read_archive(path) as reader:
        plan = plan_archive(db, reader, mode=mode)
    if mutate is not None:
        mutate(db)
    with read_archive(path) as reader:
        report = apply_plan(db, reader, plan, selection)
    return plan, report


def _zip_for(tmp_path, library, entries=(), name="a.zip"):
    return _write_zip(tmp_path / name, list(entries), library=json.dumps(library))


class TestApplyPlanReportShape:
    def test_report_is_the_v1_report_plus_the_new_keys(self, db, tmp_path):
        library = {"items": [{"id": 1, "title": "New", "isbn": "9780000000010",
                              "media_type": "book"}]}
        _plan, report = _plan_and_apply(db, _zip_for(tmp_path, library))
        assert report == {
            "imported": 1, "updated": 0, "skipped": 0, "errors": [],
            "covers_installed": 0, "format": "shelf-archive",
            "covers_replaced": 0, "drifted": 0,
            "deselected": {"creates": 0, "updates": 0, "covers": 0,
                           "reading_log": 0, "checkouts": 0, "valuation_history": 0},
        }

    def test_apply_uses_the_plans_mode_not_a_caller_supplied_one(self, db, tmp_path):
        """You apply what you reviewed: an update-mode plan updates even
        though apply_plan is never told a mode."""
        existing_id = _insert_item(db, title="Dune", isbn="9780441013593")
        db.execute("COMMIT")
        library = {"items": [{"id": 1, "title": "Dune", "isbn": "9780441013593",
                              "media_type": "book", "publisher": "Ace"}]}
        _plan, report = _plan_and_apply(db, _zip_for(tmp_path, library), mode="update")
        assert report["updated"] == 1
        assert db.execute("SELECT publisher FROM items WHERE id = ?",
                          (existing_id,)).fetchone()["publisher"] == "Ace"


class TestApplyPlanSelection:
    def _mixed_library(self):
        return {
            "items": [
                {"id": 1, "title": "Already Here", "isbn": "9780000000010",
                 "media_type": "book", "publisher": "Updated Press"},
                {"id": 2, "title": "Brand New", "isbn": "9780000000030",
                 "media_type": "book", "cover": "covers/2.jpg"},
            ],
            "reading_log": [{"item_id": 2, "status": "read"},
                            {"item_id": 2, "status": "reading"}],
            "checkouts": [{"item_id": 2, "borrower": "Sam"}],
            "valuation_history": [{"total_value": 99, "priced_count": 5}],
        }

    def _seed_match(self, db):
        item_id = _insert_item(db, title="Already Here", isbn="9780000000010")
        db.execute("COMMIT")
        return item_id

    def test_everything_selected_is_the_full_merge(self, db, tmp_path):
        self._seed_match(db)
        path = _zip_for(tmp_path, self._mixed_library(), [("covers/2.jpg", _JPEG)])
        _plan, report = _plan_and_apply(db, path, mode="update")
        db.execute("COMMIT")

        assert report["imported"] == 1 and report["updated"] == 1
        assert report["covers_installed"] == 1
        assert report["deselected"] == {"creates": 0, "updates": 0, "covers": 0,
                                        "reading_log": 0, "checkouts": 0,
                                        "valuation_history": 0}
        assert db.execute("SELECT COUNT(*) c FROM reading_log").fetchone()["c"] == 2
        assert db.execute("SELECT COUNT(*) c FROM checkouts").fetchone()["c"] == 1
        assert db.execute("SELECT COUNT(*) c FROM valuation_history").fetchone()["c"] == 1

    def test_reading_log_deselected_leaves_items_but_no_rows(self, db, tmp_path):
        self._seed_match(db)
        path = _zip_for(tmp_path, self._mixed_library(), [("covers/2.jpg", _JPEG)])
        _plan, report = _plan_and_apply(db, path, mode="update",
                                        selection={"reading_log": False})
        db.execute("COMMIT")

        assert report["imported"] == 1
        assert report["deselected"]["reading_log"] == 2
        assert db.execute("SELECT COUNT(*) c FROM reading_log").fetchone()["c"] == 0
        assert db.execute(
            "SELECT COUNT(*) c FROM items WHERE title = 'Brand New'"
        ).fetchone()["c"] == 1

    def test_creates_deselected_applies_updates_only(self, db, tmp_path):
        existing_id = self._seed_match(db)
        path = _zip_for(tmp_path, self._mixed_library(), [("covers/2.jpg", _JPEG)])
        _plan, report = _plan_and_apply(db, path, mode="update",
                                        selection={"include_creates": False})
        db.execute("COMMIT")

        assert report["imported"] == 0
        assert report["updated"] == 1
        assert report["deselected"]["creates"] == 1
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 1
        assert db.execute("SELECT publisher FROM items WHERE id = ?",
                          (existing_id,)).fetchone()["publisher"] == "Updated Press"
        # reading_log/checkouts hang off created items, so nothing lands and
        # nothing is blamed on their own toggles.
        assert report["deselected"]["reading_log"] == 0
        assert db.execute("SELECT COUNT(*) c FROM reading_log").fetchone()["c"] == 0

    def test_updates_deselected_applies_creates_only(self, db, tmp_path):
        existing_id = self._seed_match(db)
        path = _zip_for(tmp_path, self._mixed_library(), [("covers/2.jpg", _JPEG)])
        _plan, report = _plan_and_apply(db, path, mode="update",
                                        selection={"include_updates": False})
        db.execute("COMMIT")

        assert report["imported"] == 1
        assert report["updated"] == 0
        assert report["deselected"]["updates"] == 1
        assert db.execute("SELECT publisher FROM items WHERE id = ?",
                          (existing_id,)).fetchone()["publisher"] is None

    def test_covers_deselected_installs_none(self, db, tmp_path):
        self._seed_match(db)
        path = _zip_for(tmp_path, self._mixed_library(), [("covers/2.jpg", _JPEG)])
        _plan, report = _plan_and_apply(db, path, mode="update",
                                        selection={"covers": False})
        db.execute("COMMIT")

        assert report["imported"] == 1
        assert report["covers_installed"] == 0
        assert report["deselected"]["covers"] == 1
        new_id = db.execute("SELECT id FROM items WHERE title = 'Brand New'").fetchone()["id"]
        assert not (config.COVERS_DIR / f"{new_id}.jpg").exists()
        assert db.execute("SELECT cover_path FROM items WHERE id = ?",
                          (new_id,)).fetchone()["cover_path"] is None

    def test_checkouts_deselected(self, db, tmp_path):
        self._seed_match(db)
        path = _zip_for(tmp_path, self._mixed_library(), [("covers/2.jpg", _JPEG)])
        _plan, report = _plan_and_apply(db, path, mode="update",
                                        selection={"checkouts": False})
        db.execute("COMMIT")
        assert report["deselected"]["checkouts"] == 1
        assert db.execute("SELECT COUNT(*) c FROM checkouts").fetchone()["c"] == 0

    def test_valuation_history_deselected(self, db, tmp_path):
        self._seed_match(db)
        path = _zip_for(tmp_path, self._mixed_library(), [("covers/2.jpg", _JPEG)])
        _plan, report = _plan_and_apply(db, path, mode="update",
                                        selection={"valuation_history": False})
        db.execute("COMMIT")
        assert report["deselected"]["valuation_history"] == 1
        assert db.execute("SELECT COUNT(*) c FROM valuation_history").fetchone()["c"] == 0

    def test_nothing_selected_writes_nothing_at_all(self, db, tmp_path):
        """The strongest selection case: with every toggle off, apply must
        leave the database exactly as it found it — including the
        get-or-create side effects for locations/tags/borrowers/series."""
        self._seed_match(db)
        library = self._mixed_library()
        library["locations"] = [{"name": "Attic", "sort_order": 0}]
        library["tags"] = [{"name": "unloved"}]
        library["borrowers"] = [{"name": "Sam"}]
        library["series"] = [{"name": "Foundation"}]
        path = _zip_for(tmp_path, library, [("covers/2.jpg", _JPEG)])
        before = _library_snapshot(db)

        _plan, report = _plan_and_apply(db, path, mode="update", selection={
            "include_creates": False, "include_updates": False, "covers": False,
            "reading_log": False, "checkouts": False, "valuation_history": False,
        })

        assert _library_snapshot(db) == before
        assert report["deselected"]["creates"] == 1
        assert report["deselected"]["updates"] == 1
        assert report["deselected"]["valuation_history"] == 1


class TestApplyPlanCoverSemantics:
    def test_gap_is_filled_but_existing_cover_is_kept(self, db, tmp_path):
        gap_id = _insert_item(db, title="No Cover", isbn="9780000000010")
        kept_id = _insert_item(db, title="Has Cover", isbn="9780000000020")
        config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
        old_bytes = b"\xff\xd8\xff\xe0" + b"OLD" * 100
        (config.COVERS_DIR / f"{kept_id}.jpg").write_bytes(old_bytes)
        db.execute("UPDATE items SET cover_path = ? WHERE id = ?",
                   (f"covers/{kept_id}.jpg", kept_id))
        db.execute("COMMIT")

        library = {"items": [
            {"id": 1, "title": "No Cover", "isbn": "9780000000010",
             "media_type": "book", "cover": "covers/1.jpg"},
            {"id": 2, "title": "Has Cover", "isbn": "9780000000020",
             "media_type": "book", "cover": "covers/2.jpg"},
        ]}
        new_bytes = b"\xff\xd8\xff\xe0" + b"NEW" * 100
        path = _zip_for(tmp_path, library, [("covers/1.jpg", _JPEG),
                                            ("covers/2.jpg", new_bytes)])
        _plan, report = _plan_and_apply(db, path, mode="update")
        db.execute("COMMIT")

        assert report["covers_installed"] == 1
        assert report["covers_replaced"] == 0
        assert (config.COVERS_DIR / f"{gap_id}.jpg").read_bytes() == _JPEG
        assert (config.COVERS_DIR / f"{kept_id}.jpg").read_bytes() == old_bytes

    def test_replace_covers_opt_in_counts_separately(self, db, tmp_path):
        kept_id = _insert_item(db, title="Has Cover", isbn="9780000000020")
        config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
        (config.COVERS_DIR / f"{kept_id}.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"OLD" * 100)
        db.execute("UPDATE items SET cover_path = ? WHERE id = ?",
                   (f"covers/{kept_id}.jpg", kept_id))
        db.execute("COMMIT")

        new_bytes = b"\xff\xd8\xff\xe0" + b"NEW" * 100
        library = {"items": [{"id": 2, "title": "Has Cover", "isbn": "9780000000020",
                              "media_type": "book", "cover": "covers/2.jpg"}]}
        path = _zip_for(tmp_path, library, [("covers/2.jpg", new_bytes)])
        _plan, report = _plan_and_apply(db, path, mode="update",
                                        selection={"replace_covers": True})
        db.execute("COMMIT")

        assert report["covers_installed"] == 0
        assert report["covers_replaced"] == 1
        assert (config.COVERS_DIR / f"{kept_id}.jpg").read_bytes() == new_bytes

    def test_plan_cover_counts_predict_the_apply(self, db, tmp_path):
        kept_id = _insert_item(db, title="Has Cover", isbn="9780000000020")
        config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
        (config.COVERS_DIR / f"{kept_id}.jpg").write_bytes(_JPEG)
        db.execute("UPDATE items SET cover_path = ? WHERE id = ?",
                   (f"covers/{kept_id}.jpg", kept_id))
        db.execute("COMMIT")

        library = {"items": [
            {"id": 1, "title": "Brand New", "isbn": "9780000000010",
             "media_type": "book", "cover": "covers/1.jpg"},
            {"id": 2, "title": "Has Cover", "isbn": "9780000000020",
             "media_type": "book", "cover": "covers/2.jpg"},
        ]}
        path = _zip_for(tmp_path, library, [("covers/1.jpg", _JPEG),
                                            ("covers/2.jpg", _JPEG)])
        plan, report = _plan_and_apply(db, path, mode="update",
                                       selection={"replace_covers": True})
        db.execute("COMMIT")
        assert report["covers_installed"] == plan["summary"]["covers_install"] == 1
        assert report["covers_replaced"] == plan["summary"]["covers_replace"] == 1


class TestApplyPlanDrift:
    def test_item_that_appeared_between_plan_and_apply_is_skipped(self, db, tmp_path):
        library = {"items": [
            {"id": 1, "title": "Raced", "isbn": "9780000000010", "media_type": "book"},
            {"id": 2, "title": "Untouched", "isbn": "9780000000020", "media_type": "book"},
        ]}
        path = _zip_for(tmp_path, library)

        def race(db):
            _insert_item(db, title="Raced", isbn="9780000000010")
            db.execute("COMMIT")

        _plan, report = _plan_and_apply(db, path, mode="skip", mutate=race)
        db.execute("COMMIT")

        # Item 1 was planned "create" but now matches a row — refuse to act
        # on the stale verdict rather than quietly doing something else.
        assert report["drifted"] == 1
        assert report["imported"] == 1
        assert report["skipped"] == 0
        titles = {r["title"] for r in db.execute("SELECT title FROM items").fetchall()}
        assert titles == {"Raced", "Untouched"}
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 2

    def test_item_that_disappeared_between_plan_and_apply_is_drift(self, db, tmp_path):
        existing_id = _insert_item(db, title="Vanishing", isbn="9780000000010")
        db.execute("COMMIT")
        library = {"items": [{"id": 1, "title": "Vanishing", "isbn": "9780000000010",
                              "media_type": "book"}]}
        path = _zip_for(tmp_path, library)

        def vanish(db):
            db.execute("DELETE FROM items WHERE id = ?", (existing_id,))
            db.execute("COMMIT")

        _plan, report = _plan_and_apply(db, path, mode="skip", mutate=vanish)

        # Planned "skip", but the row it would have skipped is gone. Creating
        # it now would exceed what the user reviewed.
        assert report["drifted"] == 1
        assert report["imported"] == 0
        assert report["skipped"] == 0
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 0

    def test_drift_is_not_counted_as_deselection(self, db, tmp_path):
        library = {"items": [{"id": 1, "title": "Raced", "isbn": "9780000000010",
                              "media_type": "book"}]}
        path = _zip_for(tmp_path, library)

        def race(db):
            _insert_item(db, title="Raced", isbn="9780000000010")
            db.execute("COMMIT")

        _plan, report = _plan_and_apply(db, path, mode="skip", mutate=race)
        assert report["drifted"] == 1
        assert all(v == 0 for v in report["deselected"].values())

    def test_duplicate_dedupe_keys_survive_plan_and_apply(self, db, tmp_path):
        """The a92b8e7 invariant through the split: an archive holding two
        rows under one dedupe key restores as two rows, and neither is
        mistaken for drift when the first insert lands."""
        _insert_item(db, title="Automate the Boring Stuff", authors="Al Sweigart",
                     isbn=None, media_type="ebook")
        _insert_item(db, title="automate the boring stuff", authors="al sweigart",
                     isbn=None, media_type="ebook")
        db.execute("COMMIT")
        path = build_archive(db)
        _wipe_library(db)

        with read_archive(path) as reader:
            plan = plan_archive(db, reader, mode="skip")
        with read_archive(path) as reader:
            report = apply_plan(db, reader, plan)
        db.execute("COMMIT")

        assert plan["summary"]["create"] == 2
        assert report["imported"] == 2, report
        assert report["drifted"] == 0, report
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 2

    def test_full_library_round_trips_through_plan_and_apply(self, db, tmp_path):
        _seed_full_library(db)
        original_path = build_archive(db)
        with zipfile.ZipFile(original_path) as zf:
            original_library = json.loads(zf.read("library.json"))
            original_covers = {n: zf.read(n) for n in zf.namelist() if n.startswith("covers/")}

        _wipe_library(db)

        with read_archive(original_path) as reader:
            plan = plan_archive(db, reader, mode="skip")
        with read_archive(original_path) as reader:
            report = apply_plan(db, reader, plan)
        db.execute("COMMIT")

        assert report["imported"] == len(original_library["items"])
        assert report["errors"] == []
        assert report["covers_installed"] == len(original_covers)
        assert report["drifted"] == 0

        reimport_path = build_archive(db)
        with zipfile.ZipFile(reimport_path) as zf:
            new_library = json.loads(zf.read("library.json"))
        assert _normalize_library(original_library) == _normalize_library(new_library)


# ---------------------------------------------------------------------------
# T4 — /plan and /apply endpoints; legacy endpoint recomposed
# ---------------------------------------------------------------------------

def _staging_dir():
    """DATA_DIR / import_staging, resolved at call time — same trap the
    module docstring in app/services/import_staging.py documents."""
    return config.DATA_DIR / "import_staging"


def _staged_files():
    d = _staging_dir()
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file())


class TestPlanApplyEndpointAuth:
    def test_editor_forbidden_on_plan(self, editor_client):
        resp = editor_client.post(
            "/api/import/archive/plan",
            files={"file": ("a.zip", io.BytesIO(b"not a zip"), "application/zip")},
            data={"mode": "skip"},
        )
        assert resp.status_code == 403

    def test_viewer_forbidden_on_plan(self, viewer_client):
        resp = viewer_client.post(
            "/api/import/archive/plan",
            files={"file": ("a.zip", io.BytesIO(b"not a zip"), "application/zip")},
            data={"mode": "skip"},
        )
        assert resp.status_code == 403

    def test_editor_forbidden_on_apply(self, editor_client):
        resp = editor_client.post(
            "/api/import/archive/apply",
            data={"upload_id": "whatever", "mode": "skip"},
        )
        assert resp.status_code == 403

    def test_viewer_forbidden_on_apply(self, viewer_client):
        resp = viewer_client.post(
            "/api/import/archive/apply",
            data={"upload_id": "whatever", "mode": "skip"},
        )
        assert resp.status_code == 403


class TestPlanEndpoint:
    def test_plan_writes_nothing_but_stages_zip_and_plan(self, admin_client, db):
        _seed_full_library(db)
        export_resp = admin_client.get("/api/export/archive")
        before = _library_snapshot(db)

        resp = admin_client.post(
            "/api/import/archive/plan",
            files={"file": ("a.zip", io.BytesIO(export_resp.content), "application/zip")},
            data={"mode": "skip"},
        )
        assert resp.status_code == 200
        body = resp.json()
        upload_id = body["upload_id"]
        assert isinstance(upload_id, str) and upload_id
        assert body["plan"]["summary"]["items_total"] == 1

        # Purity holds through the endpoint too — the plan call is a read.
        assert _library_snapshot(db) == before

        assert _staged_files() == sorted([f"{upload_id}.zip", f"{upload_id}.plan.json"])

    def test_invalid_upload_stages_nothing_and_refuses(self, admin_client):
        resp = admin_client.post(
            "/api/import/archive/plan",
            files={"file": ("a.zip", io.BytesIO(b"this is definitely not a zip file"), "application/zip")},
            data={"mode": "skip"},
        )
        assert resp.status_code != 500
        body = resp.json()
        assert "error" in body
        assert "not a zip" in body["error"]
        # The refusal shape is the extended one, zeroed.
        assert body["covers_replaced"] == 0
        assert body["drifted"] == 0
        assert body["deselected"] == {"creates": 0, "updates": 0, "covers": 0,
                                      "reading_log": 0, "checkouts": 0,
                                      "valuation_history": 0}
        assert _staged_files() == []

    def test_no_file_uploaded_stages_nothing(self, admin_client):
        resp = admin_client.post("/api/import/archive/plan", data={"mode": "skip"})
        assert resp.status_code != 500
        assert "error" in resp.json()
        assert _staged_files() == []

    def test_second_plan_evicts_the_first_staged_upload(self, admin_client, db):
        _seed_full_library(db)
        export_resp = admin_client.get("/api/export/archive")

        resp1 = admin_client.post(
            "/api/import/archive/plan",
            files={"file": ("a.zip", io.BytesIO(export_resp.content), "application/zip")},
            data={"mode": "skip"},
        )
        upload_id_1 = resp1.json()["upload_id"]

        resp2 = admin_client.post(
            "/api/import/archive/plan",
            files={"file": ("a.zip", io.BytesIO(export_resp.content), "application/zip")},
            data={"mode": "skip"},
        )
        upload_id_2 = resp2.json()["upload_id"]
        assert upload_id_1 != upload_id_2
        assert _staged_files() == sorted([f"{upload_id_2}.zip", f"{upload_id_2}.plan.json"])

        # The first upload_id no longer applies — its files are gone.
        apply_resp = admin_client.post(
            "/api/import/archive/apply",
            data={"upload_id": upload_id_1, "mode": "skip"},
        )
        assert apply_resp.json()["error"] == "Preview expired — upload the archive again."


class TestApplyEndpoint:
    def _plan_via_endpoint(self, admin_client, zip_bytes, mode="skip"):
        resp = admin_client.post(
            "/api/import/archive/plan",
            files={"file": ("a.zip", io.BytesIO(zip_bytes), "application/zip")},
            data={"mode": mode},
        )
        assert resp.status_code == 200
        return resp.json()["upload_id"]

    def test_apply_honors_selection_reading_log_deselected(self, admin_client, db):
        _seed_full_library(db)
        export_resp = admin_client.get("/api/export/archive")
        _wipe_library(db)

        upload_id = self._plan_via_endpoint(admin_client, export_resp.content)

        apply_resp = admin_client.post(
            "/api/import/archive/apply",
            data={"upload_id": upload_id, "mode": "skip", "reading_log": "false"},
        )
        assert apply_resp.status_code == 200
        report = apply_resp.json()
        assert report["imported"] == 1
        assert report["deselected"]["reading_log"] == 1

        with get_db() as check_db:
            assert check_db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 1
            assert check_db.execute("SELECT COUNT(*) c FROM reading_log").fetchone()["c"] == 0

        # The staged pair is consumed on a successful apply too.
        assert _staged_files() == []

    def test_unknown_upload_id_refuses_and_applies_nothing(self, admin_client, db):
        before = _library_snapshot(db)
        resp = admin_client.post(
            "/api/import/archive/apply",
            data={"upload_id": "does-not-exist-at-all", "mode": "skip"},
        )
        assert resp.json()["error"] == "Preview expired — upload the archive again."
        assert _library_snapshot(db) == before

    def test_malformed_upload_id_refuses(self, admin_client):
        resp = admin_client.post(
            "/api/import/archive/apply",
            data={"upload_id": "../../etc/passwd", "mode": "skip"},
        )
        assert resp.json()["error"] == "Preview expired — upload the archive again."

    def test_missing_upload_id_refuses(self, admin_client):
        resp = admin_client.post("/api/import/archive/apply", data={"mode": "skip"})
        assert resp.json()["error"] == "Preview expired — upload the archive again."

    def test_expired_upload_id_refuses(self, admin_client, db):
        _seed_full_library(db)
        export_resp = admin_client.get("/api/export/archive")
        upload_id = self._plan_via_endpoint(admin_client, export_resp.content)

        old_time = time.time() - (31 * 60)  # past the 30-minute TTL
        for f in _staging_dir().iterdir():
            os.utime(f, (old_time, old_time))

        resp = admin_client.post(
            "/api/import/archive/apply",
            data={"upload_id": upload_id, "mode": "skip"},
        )
        assert resp.json()["error"] == "Preview expired — upload the archive again."

    def test_apply_consumes_staged_files_even_when_it_errors(self, admin_client, db, monkeypatch):
        import app.routers.archive as archive_router

        _seed_full_library(db)
        export_resp = admin_client.get("/api/export/archive")
        upload_id = self._plan_via_endpoint(admin_client, export_resp.content)

        def _boom(*a, **k):
            raise ArchiveError("synthetic apply failure")

        monkeypatch.setattr(archive_router, "apply_plan", _boom)

        resp = admin_client.post(
            "/api/import/archive/apply",
            data={"upload_id": upload_id, "mode": "skip"},
        )
        assert resp.json()["error"] == "synthetic apply failure"
        assert _staged_files() == []

    def test_stale_mode_disagreeing_with_plan_is_refused(self, admin_client, db):
        _seed_full_library(db)
        export_resp = admin_client.get("/api/export/archive")
        # Plan under "skip"...
        upload_id = self._plan_via_endpoint(admin_client, export_resp.content, mode="skip")

        # ...then submit apply claiming "update" — a stale/tampered form.
        resp = admin_client.post(
            "/api/import/archive/apply",
            data={"upload_id": upload_id, "mode": "update"},
        )
        body = resp.json()
        assert "error" in body
        assert body["imported"] == 0 and body["updated"] == 0
        # Nothing was consumed — the same upload_id still works once the
        # form's mode is corrected.
        assert _staged_files() == sorted([f"{upload_id}.zip", f"{upload_id}.plan.json"])

        retry = admin_client.post(
            "/api/import/archive/apply",
            data={"upload_id": upload_id, "mode": "skip"},
        )
        assert retry.status_code == 200
        assert "error" not in retry.json()


class TestLegacyEndpointReplaceCovers:
    """0.7.0 behavior change rides the legacy one-shot endpoint too: covers
    on matched items are preserved by default, replaced only with the new
    opt-in — matches TestMergeUpdateModeNonEmptyLibrary's service-level
    coverage, exercised here through the router."""

    def _seed_matched_item_with_cover(self, db, tmp_path):
        existing_id = _insert_item(db, title="Dune", isbn="9780441013593")
        config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
        old_bytes = b"\xff\xd8\xff\xe0" + b"OLD" * 100
        (config.COVERS_DIR / f"{existing_id}.jpg").write_bytes(old_bytes)
        db.execute("UPDATE items SET cover_path = ? WHERE id = ?",
                   (f"covers/{existing_id}.jpg", existing_id))
        db.execute("COMMIT")

        new_bytes = b"\xff\xd8\xff\xe0" + b"NEW" * 100
        library = {"items": [{"id": 1, "title": "Dune", "isbn": "9780441013593",
                              "media_type": "book", "cover": "covers/1.jpg"}]}
        p = _write_zip(tmp_path / "a.zip", [("covers/1.jpg", new_bytes)],
                       library=json.dumps(library))
        return existing_id, old_bytes, new_bytes, p

    def test_existing_cover_kept_by_default(self, admin_client, db, tmp_path):
        existing_id, old_bytes, _new, p = self._seed_matched_item_with_cover(db, tmp_path)

        resp = admin_client.post(
            "/api/import/archive",
            files={"file": ("a.zip", io.BytesIO(p.read_bytes()), "application/zip")},
            data={"mode": "update"},
        )
        assert resp.status_code == 200
        report = resp.json()
        assert report["updated"] == 1
        assert report["covers_installed"] == 0

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT cover_path FROM items WHERE id = ?", (existing_id,)
            ).fetchone()
            assert row["cover_path"] == f"covers/{existing_id}.jpg"
        assert (config.COVERS_DIR / f"{existing_id}.jpg").read_bytes() == old_bytes

    def test_existing_cover_replaced_when_opted_in(self, admin_client, db, tmp_path):
        existing_id, _old, new_bytes, p = self._seed_matched_item_with_cover(db, tmp_path)

        resp = admin_client.post(
            "/api/import/archive",
            files={"file": ("a.zip", io.BytesIO(p.read_bytes()), "application/zip")},
            data={"mode": "update", "replace_covers": "true"},
        )
        assert resp.status_code == 200
        report = resp.json()
        assert report["updated"] == 1
        assert report["covers_installed"] == 1
        assert (config.COVERS_DIR / f"{existing_id}.jpg").read_bytes() == new_bytes
