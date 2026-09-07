"""Router-package composition for the Home-page contribution."""

from app.routers import home as _home
from app.routers import pages as _pages

# Upstream's root route is only a redirect to Browse. Replace that one route
# while leaving the whole Browse router, its ordering and every other page
# untouched.
_pages.router.routes[:] = [
    route
    for route in _pages.router.routes
    if not (
        getattr(route, "path", None) == "/"
        and "GET" in (getattr(route, "methods", None) or set())
    )
]
_pages.router.include_router(_home.router)
