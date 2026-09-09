"""Tests for first-class Shelf libraries and their permission boundary."""

from app import database
from app.auth import hash_password
from app.services import libraries
from tests.conftest import _insert_item


def _user(db, username: str, role: str = "viewer") -> dict:
    cursor = db.execute(
        "INSERT INTO users (username, password, display_name, role) VALUES (?, ?, ?, ?)",
        (username, hash_password("password123"), username.title(), role),
    )
    return {
        "id": cursor.lastrowid,
        "username": username,
        "display_name": username.title(),
        "role": role,
    }


def _migration_sql(version: int) -> str:
    return next(
        sql
        for number, _description, sql in database.MIGRATIONS
        if number == version
    )


def _run_upgrade_snapshot(db) -> None:
    for version in (36, 37, 38):
        db.execute(_migration_sql(version))


def test_library_migrations_follow_current_upstream_namespace():
    assert [
        version for version, _description, _sql in database.MIGRATIONS
        if 33 <= version <= 40
    ] == list(range(33, 41))


def test_init_creates_default_main_library(db):
    rows = libraries.list_libraries(db)

    main = next(row for row in rows if row["id"] == libraries.DEFAULT_LIBRARY_ID)
    assert main["name"] == "Main Library"
    assert main["is_archived"] == 0


def test_upgrade_snapshot_preserves_existing_single_library_access(db):
    viewer = _user(db, "legacy-viewer", "viewer")
    editor = _user(db, "legacy-editor", "editor")
    admin = _user(db, "legacy-admin", "admin")
    item_id = _insert_item(db, title="Legacy Catalogue Item")

    # Re-run only the idempotent data migrations as if these rows existed at
    # upgrade time. Existing upstream installations should wake up as one Main Library.
    _run_upgrade_snapshot(db)

    assert libraries.item_library_id(db, item_id) == libraries.DEFAULT_LIBRARY_ID
    assert libraries.membership_role(db, viewer, libraries.DEFAULT_LIBRARY_ID) == "viewer"
    assert libraries.membership_role(db, editor, libraries.DEFAULT_LIBRARY_ID) == "editor"
    # Admins need no membership row; their site role is the explicit bypass.
    assert libraries.membership_role(db, admin, libraries.DEFAULT_LIBRARY_ID) == "admin"
    assert db.execute(
        "SELECT 1 FROM library_memberships WHERE user_id = ?", (admin["id"],)
    ).fetchone() is None


def test_library_role_is_independent_of_legacy_global_viewer_role(db):
    user = _user(db, "mixed-access", "viewer")
    books = libraries.create_library(db, "Books")
    private = libraries.create_library(db, "Private Archive")

    libraries.set_membership(db, books["id"], user["id"], "editor")
    libraries.set_membership(db, private["id"], user["id"], "viewer")

    assert libraries.membership_role(db, user, books["id"]) == "editor"
    assert libraries.has_library_role(db, user, books["id"], "editor") is True
    assert libraries.has_library_role(db, user, private["id"], "viewer") is True
    assert libraries.has_library_role(db, user, private["id"], "editor") is False


def test_no_membership_means_no_library_or_item_access(db):
    user = _user(db, "no-access", "editor")
    private = libraries.create_library(db, "Restricted")
    item_id = _insert_item(db, title="Restricted Item", isbn="9780000007716")
    libraries.assign_item(db, item_id, private["id"])

    # The old global editor role must not leak access once library permissions
    # are authoritative.
    assert libraries.membership_role(db, user, private["id"]) is None
    assert libraries.has_library_role(db, user, private["id"], "viewer") is False
    assert libraries.has_item_role(db, user, item_id, "viewer") is False
    assert private["id"] not in libraries.accessible_library_ids(db, user)


def test_global_admin_can_recover_every_library_and_mapped_or_unmapped_item(db):
    admin = _user(db, "site-admin", "admin")
    first = libraries.create_library(db, "First")
    second = libraries.create_library(db, "Second")
    mapped = _insert_item(db, title="Mapped", isbn="9780000007723")
    unmapped = _insert_item(db, title="Unmapped", isbn="9780000007730")
    libraries.assign_item(db, mapped, first["id"])

    assert libraries.has_library_role(db, admin, first["id"], "editor") is True
    assert libraries.has_library_role(db, admin, second["id"], "viewer") is True
    assert libraries.has_item_role(db, admin, mapped, "editor") is True
    assert libraries.has_item_role(db, admin, unmapped, "viewer") is True
    accessible = libraries.accessible_library_ids(db, admin)
    assert first["id"] in accessible
    assert second["id"] in accessible


def test_assigning_item_to_another_library_moves_instead_of_duplicates(db):
    first = libraries.create_library(db, "Library A")
    second = libraries.create_library(db, "Library B")
    item_id = _insert_item(db, title="Movable Item", isbn="9780000007747")

    libraries.assign_item(db, item_id, first["id"])
    assert libraries.item_library_id(db, item_id) == first["id"]

    libraries.assign_item(db, item_id, second["id"])
    assert libraries.item_library_id(db, item_id) == second["id"]
    assert db.execute(
        "SELECT COUNT(*) AS c FROM library_items WHERE item_id = ?", (item_id,)
    ).fetchone()["c"] == 1


def test_removing_membership_revokes_access_without_touching_catalogue(db):
    user = _user(db, "revoked", "viewer")
    library = libraries.create_library(db, "Shared")
    item_id = _insert_item(db, title="Still Here", isbn="9780000007754")
    libraries.assign_item(db, item_id, library["id"])
    libraries.set_membership(db, library["id"], user["id"], "viewer")

    assert libraries.has_item_role(db, user, item_id) is True
    libraries.remove_membership(db, library["id"], user["id"])

    assert libraries.has_item_role(db, user, item_id) is False
    assert db.execute("SELECT title FROM items WHERE id = ?", (item_id,)).fetchone()["title"] == "Still Here"



def test_new_viewer_and_editor_users_receive_main_library_membership(admin_client, db):
    for username, role in (("new-viewer", "viewer"), ("new-editor", "editor")):
        response = admin_client.post(
            "/api/users",
            data={
                "username": username,
                "display_name": username,
                "password": "password123",
                "role": role,
            },
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True
        user_id = db.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()["id"]
        assert libraries.membership_role(
            db,
            {"id": user_id, "role": role},
            libraries.DEFAULT_LIBRARY_ID,
        ) == role


def test_global_role_change_keeps_main_membership_compatible(admin_client, db):
    created = admin_client.post(
        "/api/users",
        data={
            "username": "role-change",
            "display_name": "Role Change",
            "password": "password123",
            "role": "viewer",
        },
    )
    assert created.json()["ok"] is True
    user_id = db.execute(
        "SELECT id FROM users WHERE username = 'role-change'"
    ).fetchone()["id"]

    promoted = admin_client.post(
        f"/api/users/{user_id}/role", data={"role": "editor"}
    )
    assert promoted.json()["ok"] is True
    assert libraries.membership_role(
        db, {"id": user_id, "role": "editor"}, libraries.DEFAULT_LIBRARY_ID
    ) == "editor"

    other = libraries.create_library(db, "Role-independent Library")
    libraries.set_membership(db, other["id"], user_id, "viewer")
    db.commit()

    made_admin = admin_client.post(
        f"/api/users/{user_id}/role", data={"role": "admin"}
    )
    assert made_admin.json()["ok"] is True
    assert db.execute(
        "SELECT 1 FROM library_memberships WHERE library_id = ? AND user_id = ?",
        (libraries.DEFAULT_LIBRARY_ID, user_id),
    ).fetchone() is None
    assert db.execute(
        "SELECT role FROM library_memberships WHERE library_id = ? AND user_id = ?",
        (other["id"], user_id),
    ).fetchone()["role"] == "viewer"

    demoted = admin_client.post(
        f"/api/users/{user_id}/role", data={"role": "viewer"}
    )
    assert demoted.json()["ok"] is True
    assert libraries.membership_role(
        db, {"id": user_id, "role": "viewer"}, libraries.DEFAULT_LIBRARY_ID
    ) == "viewer"
    assert db.execute(
        "SELECT role FROM library_memberships WHERE library_id = ? AND user_id = ?",
        (other["id"], user_id),
    ).fetchone()["role"] == "viewer"


def test_normal_item_write_assigns_new_item_to_main_library(db):
    from app.services import item_write

    item_id = item_write.insert_item(
        db,
        title="Default library item",
        isbn="9780000000170",
        media_type="book",
        source="test",
    )
    assert libraries.item_library_id(db, item_id) == libraries.DEFAULT_LIBRARY_ID


def test_fixture_can_still_create_explicitly_unmapped_item(db):
    item_id = _insert_item(
        db,
        title="Deliberately unmapped",
        isbn="9780000000194",
        _library_id=None,
    )
    assert libraries.item_library_id(db, item_id) is None
