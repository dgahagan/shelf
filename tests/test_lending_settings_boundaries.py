"""Mutation-boundary regressions for lending reminder settings."""


def test_invalid_notify_format_rejected_without_mutating_lending_settings(admin_client, db):
    db.execute("INSERT INTO settings (key, value) VALUES ('lending_overdue_days', '21')")
    db.execute("INSERT INTO settings (key, value) VALUES ('notify_format', 'webhook')")
    db.commit()

    response = admin_client.post(
        "/api/settings/lending",
        data={
            "lending_overdue_days": "7",
            "notify_format": "carrier-pigeon",
            "notify_url": "",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": False, "message": "Unknown notification format"}
    rows = dict(
        db.execute(
            "SELECT key, value FROM settings WHERE key IN ('lending_overdue_days', 'notify_format')"
        ).fetchall()
    )
    assert rows == {"lending_overdue_days": "21", "notify_format": "webhook"}
