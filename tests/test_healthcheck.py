"""app/healthcheck.py: the stdlib probe the container HEALTHCHECK runs."""

import ast
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app import healthcheck

SOURCE = Path(healthcheck.__file__)


@pytest.mark.parametrize("tls, scheme", [(None, "https"), ("", "https"), ("on", "https"), ("off", "http")])
@pytest.mark.parametrize("port, expected_port", [(None, "18888"), ("18889", "18889")])
def test_probe_url_matrix(tls, scheme, port, expected_port):
    env = {}
    if tls is not None:
        env["SHELF_TLS"] = tls
    if port is not None:
        env["SHELF_PORT"] = port
    assert healthcheck.probe_url(env) == f"{scheme}://127.0.0.1:{expected_port}/health"


def test_probe_url_empty_port_falls_back():
    assert healthcheck.probe_url({"SHELF_PORT": ""}) == "https://127.0.0.1:18888/health"


@pytest.fixture
def serve():
    servers = []

    def start(status=200, delay=0.0):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if delay:
                    time.sleep(delay)
                try:
                    self.send_response(status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return {"SHELF_TLS": "off", "SHELF_PORT": str(server.server_address[1])}

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_main_returns_0_on_200(serve):
    assert healthcheck.main(serve(200)) == 0


def test_main_returns_1_on_503(serve, capsys):
    assert healthcheck.main(serve(503)) == 1
    assert "healthcheck:" in capsys.readouterr().err


def test_main_returns_1_on_timeout(serve, monkeypatch):
    monkeypatch.setattr(healthcheck, "TIMEOUT", 0.3)
    assert healthcheck.main(serve(200, delay=1.0)) == 1


def test_main_returns_1_on_refused_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert healthcheck.main({"SHELF_TLS": "off", "SHELF_PORT": str(port)}) == 1


def test_imports_are_standard_library_only():
    tree = ast.parse(SOURCE.read_text())
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import"
            names.add(node.module.split(".")[0])
    assert names
    assert names <= set(sys.stdlib_module_names), names - set(sys.stdlib_module_names)


def test_main_ignores_proxy_environment(serve, monkeypatch):
    """A loopback probe never goes through HTTP(S)_PROXY (diff-review codex B1)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, f"http://127.0.0.1:{dead}")
    assert healthcheck.main(serve(200)) == 0


def test_main_does_not_follow_a_redirect():
    """A 3xx is unhealthy even when its target answers 200 (diff-review codex M1)."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            if self.path == "/health":
                self.send_response(302)
                self.send_header("Location", "/ok")
            else:
                self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {"SHELF_TLS": "off", "SHELF_PORT": str(server.server_address[1])}
        assert healthcheck.main(env) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert seen == ["/health"]
