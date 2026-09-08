"""Router package composition for the Shelf Fill contribution."""

from app.routers import pages as _pages
from app.routers import shelf_fill as _shelf_fill

# Keep Shelf Fill self-contained. The page is intentionally not injected into
# the global navigation registry here: upstream pins that registry exactly, and
# the feature remains reachable directly at /shelf-fill without mutating shared
# presentation state during package import.
_pages.router.include_router(_shelf_fill.router)
