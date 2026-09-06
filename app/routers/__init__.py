"""Router package composition for self-contained integrations."""

# main.py already mounts pages.router. Keeping the Komga hook here lets the
# integration remain one cohesive contribution instead of adding another core
# import/include pair for every optional service.
from app.config import BOOK_MEDIA_TYPES as _book_media_types
from app.routers import pages as _pages
from app.routers import series as _series
from app.routers import komga as _komga

# series.UNASSIGNED_MEDIA_TYPES is one of Shelf's deferred local book-family
# literals. Komga adds Manga to the canonical config family, so keep that
# legacy declaration aligned until it is repointed to config upstream.
_series.UNASSIGNED_MEDIA_TYPES = tuple(_book_media_types)

_pages.router.include_router(_komga.router)
