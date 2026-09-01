"""Regression coverage for Audiobookshelf settings mutation boundaries."""


def test_abs_library_selection_rejects_non_list_without_mutating_setting(
    admin_client, db
):
    db.execute(
        "INSERT INTO settings (key, value) VALUES ('abs_excluded_libraries', ?)",
        ('[\"keep-me\"]',),
    )
    db.commit()

    for excluded in ("lib_junk", 123, {"id": "lib_junk"}):
        response = admin_client.post(
            "/api/sync/audiobookshelf/libraries",
            json={"excluded": excluded},
        )

        assert response.json() == {"ok": False, "message": "Invalid request body"}
        stored = db.execute(
            "SELECT value FROM settings WHERE key = 'abs_excluded_libraries'"
        ).fetchone()["value"]
        assert stored == '[\"keep-me\"]'


def test_abs_schedule_rejects_invalid_interval_without_disabling_sync(admin_client, db):
    db.execute(
        "INSERT INTO settings (key, value) VALUES ('abs_sync_interval', 'daily')"
    )
    db.commit()

    response = admin_client.post(
        "/api/sync/audiobookshelf/schedule",
        data={"interval": "hourly"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json() == {"ok": False, "message": "Invalid sync interval"}
    stored = db.execute(
        "SELECT value FROM settings WHERE key = 'abs_sync_interval'"
    ).fetchone()["value"]
    assert stored == "daily"
