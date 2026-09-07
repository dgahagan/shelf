"""Router package composition for self-contained integrations."""

# main.py already mounts pages.router. Keeping the Komga hook here lets the
# integration remain one cohesive contribution instead of adding another core
# import/include pair for every optional service.
from app.config import BOOK_MEDIA_TYPES as _book_media_types
from app.routers import items_catalog as _items_catalog
from app.routers import pages as _pages
from app.routers import series as _series
from app.routers import komga as _komga

# These two local book-family literals are existing deferred repoints. Komga
# adds Manga to the canonical config family, so keep both legacy declarations
# aligned until upstream moves them to config directly.
_series.UNASSIGNED_MEDIA_TYPES = tuple(_book_media_types)
_items_catalog.BOOK_MEDIA_TYPES = _book_media_types

_pages.router.include_router(_komga.router)
