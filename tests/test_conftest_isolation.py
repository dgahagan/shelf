"""The autouse _isolated_db fixture must sandbox the real /data directory.

Modules that did `from app.config import COVERS_DIR` froze the real path at
import time, so patching app.config alone left them writing outside the
sandbox — app.services.covers did, and a manual add carrying an ISBN tried
to mkdir /data/covers from the unit suite. These tests pin the fixture's
redirect so the gap cannot quietly reopen.
"""

import sys
from pathlib import Path

import pytest

# The three config constants modules bind at import time.
PATH_CONSTANTS = ("DATA_DIR", "DATABASE_PATH", "COVERS_DIR")

# Modules known to bind one at import time. Imported inside tests, never at
# module scope: app.main mkdirs COVERS_DIR on import, so importing it during
# collection — before the fixture patches app.config — would try to create
# the real /data/covers.
BINDING_MODULES = (
    ("app.services.covers", "COVERS_DIR"),
    ("app.database", "DATABASE_PATH"),
    ("app.database", "COVERS_DIR"),
    ("app.routers.settings", "DATABASE_PATH"),
    ("app.routers.settings", "DATA_DIR"),
    ("app.main", "COVERS_DIR"),
    ("app.main", "DATA_DIR"),
    ("app.services.isbndb", "DATA_DIR"),
)


def _load_binding_modules():
    """Import every module holding a frozen path, so the sweep isn't
    order-dependent — a module not yet in sys.modules cannot hold a stale
    binding, which would make the sweep pass vacuously."""
    for module_name, attr in BINDING_MODULES:
        __import__(module_name, fromlist=[attr])


def _app_path_constants():
    """Every (module, attr, value) path constant across loaded app modules."""
    found = []
    for name, module in list(sys.modules.items()):
        if module is None or not (name == "app" or name.startswith("app.")):
            continue
        for attr in PATH_CONSTANTS:
            value = getattr(module, attr, None)
            if isinstance(value, Path):
                found.append((name, attr, value))
    return found


class TestPathConstantsAreSandboxed:
    def test_no_app_module_points_at_the_real_data_dir(self, tmp_path):
        """Coverage net for a module nobody thought to list.

        This cannot detect staleness on its own — a module imported for the
        first time inside a test binds the already-patched value and looks
        clean. It bites in a full-suite run, where earlier tests have
        imported the graph already; TestRedirectIsPerTest below is the
        deterministic staleness check.
        """
        _load_binding_modules()
        escaped = [
            (name, attr, str(value))
            for name, attr, value in _app_path_constants()
            if tmp_path not in value.parents and value != tmp_path
        ]
        assert escaped == []

    def test_the_sweep_actually_found_something(self):
        """Guard against the assertion above passing vacuously."""
        _load_binding_modules()
        found = _app_path_constants()
        assert any(name == "app.config" for name, _, _ in found)
        # Every known binding is present, so the sweep really covers them.
        for module_name, attr in BINDING_MODULES:
            assert (module_name, attr) in [(n, a) for n, a, _ in found]

    def test_covers_module_dir_is_redirected(self, tmp_path):
        """The specific binding that leaked — app.services.covers."""
        from app.services import covers

        assert covers.COVERS_DIR != Path("/data/covers")
        assert tmp_path in covers.COVERS_DIR.parents

    def test_covers_module_agrees_with_config(self):
        """A stale binding is worse than a wrong one — it silently diverges."""
        from app import config
        from app.services import covers

        assert covers.COVERS_DIR == config.COVERS_DIR

    @pytest.mark.parametrize("module_name, attr", BINDING_MODULES)
    def test_known_import_time_bindings_are_redirected(self, module_name, attr, tmp_path):
        module = __import__(module_name, fromlist=[attr])
        value = getattr(module, attr)
        assert tmp_path in value.parents


class TestWritesLandInTheSandbox:
    def test_saving_a_cover_writes_into_tmp_not_data(self, tmp_path):
        """save_uploaded_cover() mkdirs COVERS_DIR — the call that blew up.

        No network: this is the upload path, not the download one.
        """
        from app.services import covers

        # Minimal valid JPEG: magic bytes padded past MIN_COVER_SIZE.
        content = b"\xff\xd8\xff" + b"\x00" * 200
        rel = covers.save_uploaded_cover(4242, content)

        assert rel == "covers/4242.jpg"
        written = covers.COVERS_DIR / "4242.jpg"
        assert written.exists()
        assert tmp_path in written.parents
        assert not Path("/data/covers/4242.jpg").exists()

    def test_manual_add_with_an_isbn_does_not_touch_real_data(self, editor_client, db):
        """The exact scenario that exposed the gap.

        download_cover() is stubbed because it reaches the network, not
        because of the path — the point here is that nothing under /data is
        created on the way there.
        """
        from unittest.mock import AsyncMock, patch

        with patch("app.routers.items.covers.download_cover", new=AsyncMock(return_value=None)):
            resp = editor_client.post(
                "/api/items/manual",
                data={"title": "Sandbox Book", "isbn": "9780306406157", "media_type": "book"},
            )

        assert resp.status_code == 200
        assert db.execute(
            "SELECT COUNT(*) c FROM items WHERE title = ?", ("Sandbox Book",)
        ).fetchone()["c"] == 1


class TestRedirectIsPerTest:
    """Each test gets its own tmp dir; the rebind must follow, not stick."""

    _seen = []

    def test_first_test_records_its_covers_dir(self, tmp_path):
        from app.services import covers

        type(self)._seen.append(covers.COVERS_DIR)
        assert tmp_path in covers.COVERS_DIR.parents

    def test_second_test_gets_a_different_covers_dir(self, tmp_path):
        from app.services import covers

        assert tmp_path in covers.COVERS_DIR.parents
        assert covers.COVERS_DIR not in type(self)._seen
