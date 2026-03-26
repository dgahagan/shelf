import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

# Configure logging for the app
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Add SQLite handler so logs are viewable in the web UI
from app.log_handler import SQLiteHandler
_db_handler = SQLiteHandler()
_db_handler.setLevel(logging.INFO)
_db_handler.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger("app").addHandler(_db_handler)

logger = logging.getLogger(__name__)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, RedirectResponse

from app.config import COVERS_DIR, DATA_DIR, MEDIA_TYPES, get_client_ip
from app.database import init_db, get_db
from app.routers import pages, items, locations, platforms, settings, sync, checkouts, valuation, hardcover
from app.routers import auth_routes


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        return response


_SKIP_AUTH_PATHS = frozenset({"/login", "/setup"})
_SKIP_AUTH_PREFIXES = ("/static/", "/covers/")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        from app.auth import get_current_user, should_refresh_token, set_auth_cookie, get_user_count

        path = request.url.path

        # Skip auth for static assets
        if path.startswith(_SKIP_AUTH_PREFIXES):
            return await call_next(request)

        # Inject user into request state
        user = get_current_user(request)
        request.state.user = user

        # Setup wizard: if no users exist, redirect everything to /setup
        if path not in _SKIP_AUTH_PATHS:
            if get_user_count() == 0:
                return RedirectResponse(url="/setup", status_code=303)

        # Login redirect: if users exist but no session, redirect to /login
        # (skip for POST /login, POST /setup to avoid blocking form submissions)
        if path not in _SKIP_AUTH_PATHS and not user:
            if get_user_count() > 0:
                return RedirectResponse(url="/login", status_code=303)

        response = await call_next(request)

        # Sliding expiry: refresh token if past half-life
        if user:
            fresh_token = should_refresh_token(request)
            if fresh_token:
                set_auth_cookie(response, fresh_token)

        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory per-IP rate limiter for API endpoints."""

    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.rpm = requests_per_minute
        self._hits: dict[str, list[float]] = {}

    async def dispatch(self, request: Request, call_next) -> Response:
        import time

        # Disable rate limiting in tests
        if os.environ.get("TESTING"):
            return await call_next(request)

        # Only rate-limit API and auth endpoints
        path = request.url.path
        if not (path.startswith("/api/") or path in ("/login", "/setup")):
            return await call_next(request)

        ip = get_client_ip(request)
        now = time.time()
        window = now - 60

        # Clean old entries and check count
        hits = self._hits.get(ip, [])
        hits = [t for t in hits if t > window]
        if len(hits) >= self.rpm:
            return Response("Rate limit exceeded", status_code=429)

        hits.append(now)
        self._hits[ip] = hits

        # Periodic cleanup of stale IPs (every ~100 requests)
        if len(self._hits) > 1000:
            self._hits = {
                k: [t for t in v if t > window]
                for k, v in self._hits.items()
                if any(t > window for t in v)
            }

        return await call_next(request)


async def _periodic_abs_sync():
    """Background task: run ABS sync on schedule if configured."""
    from app.services import audiobookshelf

    intervals = {"daily": 86400, "weekly": 604800}

    while True:
        await asyncio.sleep(300)  # check every 5 minutes
        try:
            with get_db() as db:
                row = db.execute("SELECT value FROM settings WHERE key = 'abs_sync_interval'").fetchone()
                interval = row["value"] if row else "off"
                if interval == "off":
                    continue

                # Check last sync time
                last = db.execute("SELECT value FROM settings WHERE key = 'abs_last_sync'").fetchone()
                import time
                now = time.time()
                if last and last["value"]:
                    elapsed = now - float(last["value"])
                    if elapsed < intervals.get(interval, 86400):
                        continue

                from app.database import get_setting
                abs_url_val = get_setting(db, "abs_url")
                abs_token_val = get_setting(db, "abs_token")

            if abs_url_val and abs_token_val:
                await audiobookshelf.sync(abs_url_val, abs_token_val)
                with get_db() as db:
                    db.execute(
                        "INSERT INTO settings (key, value) VALUES ('abs_last_sync', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = ?",
                        (str(now), str(now)),
                    )
                logger.info("Periodic Audiobookshelf sync completed")
        except Exception:
            logger.exception("Periodic Audiobookshelf sync failed")


async def _periodic_hardcover_sync():
    """Background task: pull reading status changes from Hardcover on schedule."""
    from app.services import hardcover as hc_svc

    intervals = {"daily": 86400, "weekly": 604800}

    while True:
        await asyncio.sleep(300)  # check every 5 minutes
        try:
            with get_db() as db:
                row = db.execute("SELECT value FROM settings WHERE key = 'hc_sync_interval'").fetchone()
                interval = row["value"] if row else "off"
                if interval == "off":
                    continue

                last = db.execute("SELECT value FROM settings WHERE key = 'hc_last_sync'").fetchone()
                import time
                now = time.time()
                if last and last["value"]:
                    elapsed = now - float(last["value"])
                    if elapsed < intervals.get(interval, 86400):
                        continue

                from app.database import get_setting
                token = get_setting(db, "hardcover_token")

            token = token or None
            if token:
                await hc_svc.sync_reading_statuses(token)
                with get_db() as db:
                    db.execute(
                        "INSERT INTO settings (key, value) VALUES ('hc_last_sync', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = ?",
                        (str(now), str(now)),
                    )
                logger.info("Periodic Hardcover sync completed")
        except Exception:
            logger.exception("Periodic Hardcover sync failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Initialize secret key on startup
    from app.auth import get_secret_key
    get_secret_key()
    task = asyncio.create_task(_periodic_abs_sync())
    hc_task = asyncio.create_task(_periodic_hardcover_sync())
    yield
    task.cancel()
    hc_task.cancel()


app = FastAPI(title="Shelf", lifespan=lifespan)
app.add_middleware(AuthMiddleware)
app.add_middleware(RateLimitMiddleware, requests_per_minute=60)
app.add_middleware(SecurityHeadersMiddleware)

# Exception handler for auth dependency responses
from app.auth import _ResponseException

@app.exception_handler(_ResponseException)
async def auth_exception_handler(request: Request, exc: _ResponseException):
    return exc.response

# Templates
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

def strip_html(value: str) -> str:
    if not value:
        return ""
    return re.sub(r"<[^>]+>", "", value)

templates.env.filters["strip_html"] = strip_html

# Wrap TemplateResponse to auto-inject 'user' from request.state
_original_template_response = templates.TemplateResponse

def _template_response_with_user(request_or_self, *args, **kwargs):
    # Handle both templates.TemplateResponse(request, name, ctx) patterns
    if hasattr(request_or_self, 'state'):
        request = request_or_self
    elif args and hasattr(args[0], 'state'):
        request = args[0]
    else:
        return _original_template_response(request_or_self, *args, **kwargs)

    # Find the context dict and inject user
    context = kwargs.get('context', None)
    if context is None:
        # Context is a positional arg (3rd after request, name)
        for i, a in enumerate(args):
            if isinstance(a, dict):
                a.setdefault("user", getattr(request.state, "user", None))
                break
    else:
        context.setdefault("user", getattr(request.state, "user", None))

    return _original_template_response(request_or_self, *args, **kwargs)

templates.TemplateResponse = _template_response_with_user
app.state.templates = templates

# Static files
static_dir = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Serve cached covers from data volume
COVERS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/covers", StaticFiles(directory=str(COVERS_DIR)), name="covers")

# Routers
app.include_router(auth_routes.router)
app.include_router(pages.router)
app.include_router(items.router)
app.include_router(locations.router)
app.include_router(platforms.router)
app.include_router(settings.router)
app.include_router(sync.router)
app.include_router(checkouts.router)
app.include_router(valuation.router)
app.include_router(hardcover.router)
