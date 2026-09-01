"""Regression coverage for user-creation request boundaries."""


def test_create_user_rejects_invalid_role_without_creating_account(admin_client, db):
    response = admin_client.post(
        "/api/users",
        data={
            "username": "invalid-role-user",
            "display_name": "Invalid Role",
            "password": "correct-horse-battery-staple",
            "role": "owner",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": False, "message": "Invalid role"}
    assert db.execute(
        "SELECT id FROM users WHERE username = ?", ("invalid-role-user",)
    ).fetchone() is None
