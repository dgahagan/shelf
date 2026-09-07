"""Router package composition for the Shelf Fill contribution."""

from app import nav as _nav
from app.routers import pages as _pages
from app.routers import shelf_fill as _shelf_fill

_pages.router.include_router(_shelf_fill.router)

if not any(tab.get("key") == "shelf_fill" for tab in _nav.NAV_TABS):
    insert_at = next(
        (idx + 1 for idx, tab in enumerate(_nav.NAV_TABS) if tab.get("key") == "scan"),
        len(_nav.NAV_TABS),
    )
    _nav.NAV_TABS.insert(insert_at, {
        "key": "shelf_fill",
        "label": "Shelf Fill",
        "path": "/shelf-fill",
        "roles": ("admin", "editor"),
    })
    _nav.HIDEABLE_TABS = [
        tab for tab in _nav.NAV_TABS if tab["key"] not in _nav.ALWAYS_VISIBLE
    ]
    _nav.HIDEABLE_KEYS = frozenset(tab["key"] for tab in _nav.HIDEABLE_TABS)
