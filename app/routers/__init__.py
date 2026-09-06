"""Router package composition for self-contained integrations."""

# main.py already mounts pages.router.  Keeping the Komga hook here lets the
# integration remain one cohesive contribution instead of adding another core
# import/include pair for every optional service.
from app.routers import pages as _pages
from app.routers import komga as _komga

_pages.router.include_router(_komga.router)
