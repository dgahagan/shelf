"""Regression coverage for Hardcover schedule mutation boundaries."""


def test_hardcover_schedule_rejects_invalid_interval_without_disabling_sync(
    admin_client, db
):
    db.execute(
        "INSERT INTO settings (key, value) VALUES ('hc_sync_interval', 'daily')"
    )
    db.commit()

    response = admin_client.post(
        "/api/hardcover/schedule",
        data={"interval": "hourly"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json() == {"ok": False, "message": "Invalid sync interval"}
    stored = db.execute(
        "SELECT value FROM settings WHERE key = 'hc_sync_interval'"
    ).fetchone()["value"]
    assert stored == "daily"
