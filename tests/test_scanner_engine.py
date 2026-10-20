"""Source pins for the shared scanner engine's ZXing format list."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_zxing_hint_list_includes_code_128():
    source = (ROOT / "static/js/scanner-engine.js").read_text(encoding="utf-8")
    match = re.search(
        r"hints\.set\(HINT_POSSIBLE_FORMATS,\s*\[(.*?)\]\)", source, re.S
    )
    assert match, "HINT_POSSIBLE_FORMATS list not found"
    names = re.findall(r"formats\.([A-Z0-9_]+)", match.group(1))
    assert names == ["EAN_13", "UPC_A", "EAN_8", "UPC_E", "CODE_128"]


def test_no_html5_config_narrows_formats():
    for path in (ROOT / "static/js").rglob("*.js"):
        assert "formatsToSupport" not in path.read_text(encoding="utf-8"), path.name
