import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import COVERS_DIR, DATA_DIR, MEDIA_TYPES
from app.database import init_db, get_db
from app.routers import pages, items, locations, settings, sync, checkouts, valuation, hardcover


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

                abs_url = db.execute("SELECT value FROM settings WHERE key = 'abs_url'").fetchone()
                abs_token = db.execute("SELECT value FROM settings WHERE key = 'abs_token'").fetchone()

            if abs_url and abs_token and abs_url["value"] and abs_token["value"]:
                await audiobookshelf.sync(abs_url["value"], abs_token["value"])
                with get_db() as db:
                    db.execute(
                        "INSERT INTO settings (key, value) VALUES ('abs_last_sync', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = ?",
                        (str(now), str(now)),
                    )
        except Exception:
            pass


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

                token_row = db.execute("SELECT value FROM settings WHERE key = 'hardcover_token'").fetchone()

            token = token_row["value"] if token_row and token_row["value"] else None
            if token:
                await hc_svc.sync_reading_statuses(token)
                with get_db() as db:
                    db.execute(
                        "INSERT INTO settings (key, value) VALUES ('hc_last_sync', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = ?",
                        (str(now), str(now)),
                    )
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(_periodic_abs_sync())
    hc_task = asyncio.create_task(_periodic_hardcover_sync())
    yield
    task.cancel()
    hc_task.cancel()


app = FastAPI(title="Shelf", lifespan=lifespan)

# Templates
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

# Custom filter to strip HTML tags from descriptions
import re
def strip_html(value: str) -> str:
    if not value:
        return ""
    return re.sub(r"<[^>]+>", "", value)

templates.env.filters["strip_html"] = strip_html
app.state.templates = templates

# Static files
static_dir = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Serve cached covers from data volume
COVERS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/covers", StaticFiles(directory=str(COVERS_DIR)), name="covers")

# Routers
app.include_router(pages.router)
app.include_router(items.router)
app.include_router(locations.router)
app.include_router(settings.router)
app.include_router(sync.router)
app.include_router(checkouts.router)
app.include_router(valuation.router)
app.include_router(hardcover.router)
