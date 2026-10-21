"""Offline guards for scan-page fetch error handling."""

from pathlib import Path


def test_scan_fetch_paths_reject_http_errors_before_consuming_bodies():
    js = Path(__file__).resolve().parent.parent.joinpath("static/js/scan.js").read_text()

    assert "if (!r.ok) throw new Error('HTTP ' + r.status);" in js
    # The camera scan's fetch. (Show Missing Items' fetch, the second guard
    # this used to count, went with the button when audits moved server-side.)
    assert js.count("if (!resp.ok) throw new Error('HTTP ' + resp.status);") >= 1
    assert "Could not load recent scans" in js
    assert "title: 'Scan failed'" in js
