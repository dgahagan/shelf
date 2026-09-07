"""Router package composition for self-contained integrations."""

from app.routers import pages as _pages
from app.routers import romm as _romm

_pages.router.include_router(_romm.router)
