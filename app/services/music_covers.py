"""Cover Art Archive trust registration for the first-class Music feature."""

from app.services import covers


def register_cover_art_archive() -> None:
    """Allow the provider-owned CAA URL through Shelf's normal cover guard.

    `_download_to_item` still validates the final URL after redirects; Internet
    Archive's rotating `*.us.archive.org` image hosts are already allow-listed
    by Shelf, so this adds only the CAA entry point rather than weakening the
    redirect boundary.
    """
    covers.ALLOWED_COVER_DOMAINS.add("coverartarchive.org")
