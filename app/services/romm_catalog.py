"""Read-only catalogue queries for RomM-backed Shelf items."""

from __future__ import annotations


def _access_clause(access_sql: str) -> str:
    clean = str(access_sql or "").strip()
    return clean or "1 = 1"


def platform_summaries(
    db,
    *,
    access_sql: str = "1 = 1",
    access_params: list | None = None,
) -> list[dict]:
    """Return visible RomM platform counts keyed by stable provider id."""
    access = _access_clause(access_sql)
    rows = db.execute(
        "SELECT rr.platform_id, i.platform AS shelf_platform, "
        "COALESCE(gp.name, i.platform, rr.platform_id) AS platform_name, "
        "COUNT(*) AS game_count "
        "FROM romm_records rr JOIN items i ON i.id = rr.item_id "
        "LEFT JOIN game_platforms gp ON gp.slug = i.platform "
        f"WHERE ({access}) "
        "GROUP BY rr.platform_id, i.platform, gp.name "
        "ORDER BY platform_name COLLATE NOCASE, rr.platform_id",
        list(access_params or []),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_page(
    db,
    *,
    platform_id: str = "",
    query: str = "",
    limit: int = 60,
    offset: int = 0,
    access_sql: str = "1 = 1",
    access_params: list | None = None,
) -> tuple[list[dict], int]:
    """Fetch one stable, ACL-scoped page without weakening provider identity."""
    where = [f"({_access_clause(access_sql)})"]
    params: list[object] = list(access_params or [])
    clean_platform = str(platform_id or "").strip()
    clean_query = str(query or "").strip()[:200]
    if clean_platform:
        where.append("rr.platform_id = ?")
        params.append(clean_platform)
    if clean_query:
        where.append(
            "(i.title LIKE ? COLLATE NOCASE OR "
            "COALESCE(i.publisher, '') LIKE ? COLLATE NOCASE)"
        )
        needle = f"%{clean_query}%"
        params.extend([needle, needle])
    clause = " WHERE " + " AND ".join(where)

    total = db.execute(
        "SELECT COUNT(*) AS c FROM romm_records rr "
        "JOIN items i ON i.id = rr.item_id" + clause,
        params,
    ).fetchone()["c"]
    rows = db.execute(
        "SELECT rr.romm_id, rr.platform_id, i.id AS item_id, i.title, "
        "i.publisher, i.publish_year, i.platform, i.cover_path, i.description, "
        "COALESCE(gp.name, i.platform, rr.platform_id) AS platform_name "
        "FROM romm_records rr JOIN items i ON i.id = rr.item_id "
        "LEFT JOIN game_platforms gp ON gp.slug = i.platform"
        + clause
        + " ORDER BY i.title COLLATE NOCASE, i.publish_year, rr.romm_id "
        "LIMIT ? OFFSET ?",
        [*params, max(1, int(limit)), max(0, int(offset))],
    ).fetchall()
    return [dict(row) for row in rows], total
