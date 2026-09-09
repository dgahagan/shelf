"""First-class Shelf libraries and per-library catalogue permissions.

Schema ownership lives in :mod:`app.database`; this service is deliberately
query/policy-only so permission checks never execute DDL on request paths.
"""

from __future__ import annotations


LIBRARY_ROLE_LEVELS = {"viewer": 1, "editor": 2, "admin": 3}
DEFAULT_LIBRARY_ID = 1
DEFAULT_LIBRARY_NAME = "Main Library"


def list_libraries(db, *, include_archived: bool = False) -> list[dict]:
    where = "" if include_archived else "WHERE is_archived = 0"
    rows = db.execute(
        f"SELECT id, name, description, is_archived, created_at, updated_at "
        f"FROM libraries {where} ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return [dict(row) for row in rows]


def create_library(db, name: str, description: str | None = None) -> dict:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("Library name is required")
    if len(clean_name) > 200:
        raise ValueError("Library name is too long")
    clean_description = str(description).strip() if description is not None else None
    if clean_description == "":
        clean_description = None
    if clean_description and len(clean_description) > 2000:
        raise ValueError("Library description is too long")

    cursor = db.execute(
        "INSERT INTO libraries (name, description) VALUES (?, ?)",
        (clean_name, clean_description),
    )
    row = db.execute(
        "SELECT id, name, description, is_archived, created_at, updated_at "
        "FROM libraries WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return dict(row)


def set_membership(db, library_id: int, user_id: int, role: str) -> dict:
    """Grant or change a non-admin library membership."""
    if role not in ("viewer", "editor"):
        raise ValueError("Library role must be viewer or editor")
    if not db.execute("SELECT 1 FROM libraries WHERE id = ?", (library_id,)).fetchone():
        raise LookupError("Library not found")
    if not db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
        raise LookupError("User not found")

    db.execute(
        """INSERT INTO library_memberships (library_id, user_id, role)
           VALUES (?, ?, ?)
           ON CONFLICT(library_id, user_id) DO UPDATE SET
               role = excluded.role,
               updated_at = datetime('now')""",
        (library_id, user_id, role),
    )
    row = db.execute(
        "SELECT library_id, user_id, role, created_at, updated_at "
        "FROM library_memberships WHERE library_id = ? AND user_id = ?",
        (library_id, user_id),
    ).fetchone()
    return dict(row)


def remove_membership(db, library_id: int, user_id: int) -> None:
    db.execute(
        "DELETE FROM library_memberships WHERE library_id = ? AND user_id = ?",
        (library_id, user_id),
    )


def membership_role(db, user: dict, library_id: int) -> str | None:
    """Return the effective role for one library.

    ``admin`` is the only global catalogue privilege. Non-admin global roles
    are intentionally ignored here once memberships exist; they are migration
    input, not a permanent permission ceiling.
    """
    if user.get("role") == "admin":
        return "admin"
    row = db.execute(
        "SELECT role FROM library_memberships WHERE library_id = ? AND user_id = ?",
        (library_id, int(user["id"])),
    ).fetchone()
    return row["role"] if row else None


def has_library_role(db, user: dict, library_id: int, minimum_role: str = "viewer") -> bool:
    if minimum_role not in LIBRARY_ROLE_LEVELS:
        raise ValueError("Unknown library role")
    effective = membership_role(db, user, library_id)
    return LIBRARY_ROLE_LEVELS.get(effective, 0) >= LIBRARY_ROLE_LEVELS[minimum_role]


def accessible_library_ids(db, user: dict, *, include_archived: bool = False) -> list[int]:
    """Return libraries visible to a user in stable name order."""
    archived_clause = "" if include_archived else "AND l.is_archived = 0"
    if user.get("role") == "admin":
        rows = db.execute(
            "SELECT l.id FROM libraries l WHERE 1=1 "
            f"{archived_clause} ORDER BY l.name COLLATE NOCASE"
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT l.id FROM libraries l "
            "JOIN library_memberships lm ON lm.library_id = l.id "
            "WHERE lm.user_id = ? "
            f"{archived_clause} ORDER BY l.name COLLATE NOCASE",
            (int(user["id"]),),
        ).fetchall()
    return [int(row["id"]) for row in rows]


def assign_item(db, item_id: int, library_id: int) -> None:
    """Assign or move one catalogue item to exactly one Shelf library."""
    if not db.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone():
        raise LookupError("Item not found")
    if not db.execute("SELECT 1 FROM libraries WHERE id = ?", (library_id,)).fetchone():
        raise LookupError("Library not found")
    db.execute(
        """INSERT INTO library_items (item_id, library_id) VALUES (?, ?)
           ON CONFLICT(item_id) DO UPDATE SET library_id = excluded.library_id""",
        (item_id, library_id),
    )


def item_library_id(db, item_id: int) -> int | None:
    row = db.execute(
        "SELECT library_id FROM library_items WHERE item_id = ?",
        (item_id,),
    ).fetchone()
    return int(row["library_id"]) if row else None


def has_item_role(db, user: dict, item_id: int, minimum_role: str = "viewer") -> bool:
    """Check item access, denying unmapped items to non-admins by default."""
    if user.get("role") == "admin":
        return bool(db.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone())
    library_id = item_library_id(db, item_id)
    if library_id is None:
        return False
    return has_library_role(db, user, library_id, minimum_role)
