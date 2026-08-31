"""Regression coverage for Settings game-platform mutations."""


def _insert_platform(db, name: str, slug: str) -> int:
    cur = db.execute(
        "INSERT INTO game_platforms (slug, name) VALUES (?, ?)",
        (slug, name),
    )
    return cur.lastrowid


def test_blank_platform_name_is_rejected(admin_client, db):
    before = db.execute("SELECT COUNT(*) FROM game_platforms").fetchone()[0]

    response = admin_client.post(
        "/api/platforms",
        data={"name": "   "},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?platform_error=blank"
    after = db.execute("SELECT COUNT(*) FROM game_platforms").fetchone()[0]
    assert after == before


def test_platform_slug_collision_is_reported_instead_of_silently_ignored(admin_client, db):
    _insert_platform(db, "Play Station", "playstation")
    db.commit()

    response = admin_client.post(
        "/api/platforms",
        data={"name": "Play-Station"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?platform_error=duplicate"
    assert db.execute(
        "SELECT COUNT(*) FROM game_platforms WHERE slug = 'playstation'"
    ).fetchone()[0] == 1


def test_delete_missing_platform_is_reported(admin_client):
    response = admin_client.post(
        "/api/platforms/999999/delete",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?platform_error=missing"


def test_known_platform_error_code_renders_fixed_banner(admin_client):
    response = admin_client.get("/settings?platform_error=duplicate")

    assert response.status_code == 200
    assert 'data-testid="platform-error-banner"' in response.text
    assert "A platform with that name or identifier already exists." in response.text


def test_unknown_platform_error_code_is_not_reflected(admin_client):
    response = admin_client.get(
        "/settings?platform_error=%3Cscript%3Ealert(1)%3C%2Fscript%3E"
    )

    assert response.status_code == 200
    assert 'data-testid="platform-error-banner"' not in response.text
    assert "alert(1)" not in response.text
