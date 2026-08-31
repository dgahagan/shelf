"""Regression coverage for archive-import mode validation."""

import pytest


@pytest.mark.parametrize(
    "path",
    [
        "/api/import/archive",
        "/api/import/archive/plan",
        "/api/import/archive/apply",
    ],
)
def test_archive_endpoints_reject_unknown_mode_before_work(admin_client, path):
    response = admin_client.post(path, data={"mode": "garbage"})

    assert response.status_code == 200
    data = response.json()
    assert data["error"] == "Invalid import mode"
    assert data["imported"] == 0
    assert data["updated"] == 0
