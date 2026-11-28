"""SHELF_BASE_PATH: normalisation and refusal at startup (#165)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import base_path

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("raw", [
    "/a/../b", "/a/./b", '/a"b', "/a%2f", "/a\nb", "/a b", "/a//b", "/a\\b",
    "/a<b", "/a'b", "/a?b", "/a#b", "..",
])
def test_bad_value_is_refused(raw):
    with pytest.raises(ValueError, match="SHELF_BASE_PATH"):
        base_path(raw)


@pytest.mark.parametrize("raw", [None, "", "/", "  ", "///"])
def test_unset_forms_mean_no_prefix(raw):
    assert base_path(raw) == ""


@pytest.mark.parametrize("raw,expected", [
    ("ingress/x/", "/ingress/x"),
    ("/shelf", "/shelf"),
    ("/shelf/", "/shelf"),
    (" /shelf ", "/shelf"),
    ("/api/hassio_ingress/AbC_d-9", "/api/hassio_ingress/AbC_d-9"),
])
def test_value_is_normalised(raw, expected):
    assert base_path(raw) == expected


def test_bad_value_stops_the_import():
    env = {**os.environ, "SHELF_BASE_PATH": '/a"b'}
    proc = subprocess.run(
        [sys.executable, "-c", "import app.config"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode != 0
    assert "SHELF_BASE_PATH" in proc.stderr


def test_default_renders_no_prefix(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert 'href="/static/css/app.css' in resp.text
