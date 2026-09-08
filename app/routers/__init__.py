"""Router package composition for the self-contained Music contribution."""

from app.routers import music as _music
from app.routers import pages as _pages
from app.services import music_covers as _music_covers

_music_covers.register_cover_art_archive()
_pages.router.include_router(_music.router)
