"""Container health probe: GET /health on loopback, exit 0 if healthy, 1 if not.

Run by path (``python app/healthcheck.py``), so ``app/__init__.py`` never runs.
Standard library only: no ``app.*`` and no third-party imports.

Invariant: the defaults here (SHELF_TLS unset or empty means ``on``, SHELF_PORT
falling back to 18888) mirror ``entrypoint.sh``. A change to either must change
both. ``tests/test_entrypoint.py`` holds them in step.
"""

import os
import ssl
import sys
import urllib.request
from collections.abc import Mapping

TIMEOUT = 4


def probe_url(env: Mapping[str, str]) -> str:
    scheme = "http" if env.get("SHELF_TLS") == "off" else "https"
    port = env.get("SHELF_PORT") or "18888"
    return f"{scheme}://127.0.0.1:{port}/health"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface a 3xx as an HTTPError: only a 2xx on the first response is healthy."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener() -> urllib.request.OpenerDirector:
    # An explicit opener, not urlopen's default: ProxyHandler({}) ignores
    # HTTP(S)_PROXY, which would otherwise route this loopback probe through a
    # proxy whenever NO_PROXY does not name 127.0.0.1. Verification is off on
    # purpose: the target is loopback and the cert is self-signed.
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl._create_unverified_context()),
        _NoRedirect(),
    )


def main(env: Mapping[str, str] = os.environ) -> int:
    url = probe_url(env)
    try:
        # GET, never HEAD: /health is @app.get, so HEAD gets 405.
        with _opener().open(url, timeout=TIMEOUT) as resp:
            if 200 <= resp.status < 300:
                return 0
            print(f"healthcheck: {url} returned HTTP {resp.status}", file=sys.stderr)
            return 1
    except Exception as exc:
        print(f"healthcheck: {url} failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
