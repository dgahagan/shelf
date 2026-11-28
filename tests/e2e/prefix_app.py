"""A prefix-stripping proxy in one ASGI callable, for the #165 E2E run.

Stands in for Home Assistant ingress or an nginx `location /sub/` block:
requests under PREFIX have it stripped from both `path` and `raw_path` (a real
proxy strips both, and `_cross_site_bounce` reads the raw one) and go to the
app; anything else is recorded as *escaped* and answered 404. Every in-prefix
404 is recorded too. `GET /__report` (outside the prefix, never recorded)
answers both lists as JSON, so a test sees every request the browser made —
service-worker fetches included — whatever issued it.

Boot with `uvicorn tests.e2e.prefix_app:app` and SHELF_BASE_PATH=PREFIX.
"""
import importlib
import json

PREFIX = "/ingress/test"

escaped: list[str] = []
not_found: list[str] = []
_app = None


def _real_app():
    global _app
    if _app is None:
        # Not a module-level import: G14 forbids importing app.main at the top
        # of any file under tests/.
        _app = importlib.import_module("app.main").app
    return _app


async def _send_json(send, status, payload):
    body = json.dumps(payload).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


async def app(scope, receive, send):
    if scope["type"] != "http":
        await _real_app()(scope, receive, send)
        return
    path = scope["path"]
    if path == "/__report":
        if scope["method"] == "DELETE":
            escaped.clear()
            not_found.clear()
        await _send_json(send, 200, {"escaped": list(escaped), "not_found": list(not_found)})
        return
    if path != PREFIX and not path.startswith(PREFIX + "/"):
        escaped.append(f"{scope['method']} {path}")
        await _send_json(send, 404, {"detail": "outside the prefix"})
        return
    inner = dict(scope)
    inner["path"] = path[len(PREFIX):] or "/"
    raw = scope.get("raw_path") or path.encode()
    inner["raw_path"] = raw[len(PREFIX):] or b"/"

    async def record(message):
        if message["type"] == "http.response.start" and message["status"] == 404:
            not_found.append(f"{scope['method']} {path}")
        await send(message)

    await _real_app()(inner, receive, record)
