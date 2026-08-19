"""Tests for the portable library archive (app/services/archive.py,
app/routers/archive.py) — exporter, and the reader's security rail."""
import io
import json
import re
import shutil
import zipfile

import pytest

from app import config
from app.services import archive as archive_svc
from app.services.archive import ArchiveError, build_archive, merge_archive, read_archive
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

    def test_cover_overwritten_in_update_mode_even_when_local_present(self, db, tmp_path):
        existing_id = _insert_item(db, title="Dune", isbn="9780441013593")
        config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
        local_cover = config.COVERS_DIR / f"{existing_id}.jpg"
        local_cover.write_bytes(b"\xff\xd8\xff\xe0" + b"OLD" * 100)
        db.execute("UPDATE items SET cover_path = ? WHERE id = ?", (f"covers/{existing_id}.jpg", existing_id))
        db.execute("COMMIT")

        new_cover_bytes = b"\xff\xd8\xff\xe0" + b"NEW" * 100
        library = {"items": [{"id": 1, "title": "Dune", "isbn": "9780441013593",
                               "media_type": "book", "cover": "covers/1.jpg"}]}
        p = _write_zip(tmp_path / "a.zip", [("covers/1.jpg", new_cover_bytes)], library=json.dumps(library))

        with read_archive(p) as reader:
            report = merge_archive(db, reader, mode="update")

        assert report["covers_installed"] == 1
        assert local_cover.read_bytes() == new_cover_bytes


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
