"""The Browse list-view column set, declared once.

The list view's `<thead>` (`fragments/item_grid.html`) and its `<td>` cells
(`fragments/item_row.html`) each spell out the column set independently, and
two load-more sentinels hard-code `colspan="7"` to match — a column added to
one and not the other breaks silently. `app/browse_filters.py` fixed the same
kind of drift for the filter set; read its module docstring for why that
mattered enough to become a registry.

This module is the single declaration. Later work makes the templates and
`static/js/browse.js` derive from it; for now it exists on its own —
`column_count()` is the sentinel colspan, `client_config()` is what a future
column-picker control will serialise into the page as JSON.
"""

import re
from dataclasses import dataclass
from typing import Mapping

#: Column names become CSS selectors, JSON keys and localStorage keys, so they
#: are held to identifier syntax — the same discipline as `browse_filters`'s
#: `_NAME_RE`, and for the same reason: without it a name could inject markup.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class BrowseColumn:
    """One Browse list-view column, in every form the app needs it.

    `name` is the CSS selector suffix, the JSON key and the localStorage key
    all at once — they were always required to match, and now they cannot
    diverge.
    """

    name: str
    #: Header text. Empty for the locked columns, whose header cell carries no
    #: label (a checkbox, a bare cover thumbnail, and the row's own title).
    label: str
    #: Locked columns are always rendered and cannot be hidden by the user.
    locked: bool = False
    #: Whether a pickable column starts visible. Meaningless for a locked
    #: column (it is always on) — set consistently to True there rather than
    #: leaving it ambiguous.
    default_on: bool = True

    def __post_init__(self):
        if not _NAME_RE.match(self.name):
            raise ValueError(
                f"Browse column name {self.name!r} is not a plain identifier. "
                "Names are interpolated into CSS attribute selectors and "
                "used as JSON/localStorage keys, so they must match "
                f"{_NAME_RE.pattern}."
            )


# Order is render order — left to right in the list view's <thead> and <tr>.
COLUMNS: tuple[BrowseColumn, ...] = (
    BrowseColumn("select", "", locked=True, default_on=True),
    BrowseColumn("cover", "", locked=True, default_on=True),
    BrowseColumn("title", "Title", locked=True, default_on=True),
    BrowseColumn("author", "Author", default_on=True),
    BrowseColumn("media_type", "Type", default_on=True),
    BrowseColumn("location", "Location", default_on=True),
    BrowseColumn("status", "Status", default_on=True),
    BrowseColumn("value", "Value", default_on=False),
    BrowseColumn("series", "Series", default_on=False),
    BrowseColumn("publisher", "Publisher", default_on=False),
    BrowseColumn("year", "Year", default_on=False),
    BrowseColumn("pages", "Pages", default_on=False),
    BrowseColumn("language", "Language", default_on=False),
    BrowseColumn("added", "Added", default_on=False),
    BrowseColumn("platform", "Platform", default_on=False),
    BrowseColumn("identifier", "ISBN/UPC", default_on=False),
)

BY_NAME: Mapping[str, BrowseColumn] = {c.name: c for c in COLUMNS}

COLUMN_NAMES: tuple[str, ...] = tuple(c.name for c in COLUMNS)

#: The columns a user can show/hide, in COLUMNS order. The locked columns
#: (select, cover, title) are always rendered and never appear here.
PICKABLE: tuple[BrowseColumn, ...] = tuple(c for c in COLUMNS if not c.locked)


def column_count() -> int:
    """Total column count — the sentinel `colspan` for the load-more rows."""
    return len(COLUMNS)


def client_config() -> list[dict]:
    """What a Browse column-picker needs, serialised into the page as JSON.

    CSP forbids inline executable script, so this ships in a
    `<script type="application/json">` block rather than as a JS literal.
    """
    return [
        {
            "name": c.name,
            "label": c.label,
            "locked": c.locked,
            "defaultOn": c.default_on,
        }
        for c in COLUMNS
    ]
