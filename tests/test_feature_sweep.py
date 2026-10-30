"""The render census: a turned-off feature leaves no use-control on a page
that stays on.

The page-level counterpart of the registry lint in tests/test_features.py.
That lint proves every route a feature owns is gated; this one proves the
pages that stay on stop *offering* those routes once the feature is off.

A feature's gated URLs are derived from the route census, never listed here:
every route whose dependencies carry its `feature_key`, plus its `inline`
routes. Its JS-only references come from the registry's `client` names,
because the URLs a script fetches never appear in the rendered HTML.

Both halves are needed (G108). The negative half alone would pass on a seed
that never rendered the control; the positive half's `SWEPT` equality fails
both on a new unguarded control and on a seed that stopped exercising one.
"""
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

import pytest

from app.features import FEATURES
from app.services import item_write
from tests.conftest import _insert_borrower, _insert_item
from tests.test_features_gate import off

_URL_ATTRS = {"href", "action", "src", "hx-get", "hx-post", "hx-put", "hx-patch", "hx-delete"}

# Features with at least one gated URL or client name in the body of a page
# that stays on (nav tab links excluded), with everything on and the seed
# below. Why the rest are absent:
# - abs_sync: its item-page links point at the ABS server (external URLs)
#   and its Settings controls start their requests from JS; T2 and T5 pin
#   them by data-testid instead.
# - store, music, periodicals, shelf_fill, intake: nothing in a page body
#   that stays on reaches their gated routes. They appear only as nav tabs,
#   which nav.visible_tabs filters and the negative half still reads. Photo
#   Intake's and Discogs' Settings cards are configuration only.
SWEPT = frozenset({
    "api",        # account modal tokens tab (client apiTokensPanel);
                  # Settings → Users card (client adminTokensPanel)
    "hardcover",  # item page push (client hardcoverPush)
    "komga",      # item page komga-item.js (client)
    "lending",    # item page check-in form, Settings borrower forms
    "romm",       # Browse card action, item page romm-item.js (client)
    "series",     # item page /series link, Home's Series card
    "share",      # Settings create/revoke forms, /share/{token} Open link
    "stats",      # Home's Statistics button and card
    "valuation",  # Settings report link
})


def _path_regex(path: str) -> re.Pattern:
    """A route path as a full-match regex. `{name:path}` spans slashes the
    way Starlette's converter does; every other parameter is one segment."""
    out, pos = [], 0
    for m in re.finditer(r"\{([^}:]+)(?::([^}]+))?\}", path):
        out.append(re.escape(path[pos:m.start()]))
        out.append(".+" if m.group(2) == "path" else "[^/?#]+")
        pos = m.end()
    out.append(re.escape(path[pos:]))
    return re.compile("".join(out))


def _gated_paths() -> dict[str, list[re.Pattern]]:
    from tests.test_features import _route_census
    gated: dict[str, set[str]] = {key: set() for key in FEATURES}
    ungated: set[tuple[str, str]] = set()
    for _module, method, path, keys, _calls in _route_census():
        for key in keys:
            gated[key].add((method, path))
        if not keys:
            ungated.add((method, path))
    inline = set()
    for key, feature in FEATURES.items():
        gated[key].update(feature.inline)
        inline.update(feature.inline)
    # A path gated under one method and ungated under another is treated as
    # ungated: the attribute carries no method to tell them apart. None
    # exists today. An inline route checks its flag in the handler, so the
    # census sees no dependency on it; it is not "ungated" for this purpose.
    ungated_paths = {p for route in ungated - inline for _m, p in [route]}
    return {
        key: [_path_regex(p) for p in sorted({p for _m, p in routes} - ungated_paths)]
        for key, routes in gated.items()
    }


class _AttrCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values: list[tuple[str, str, bool]] = []

    def handle_starttag(self, tag, attrs):
        names = {name for name, _ in attrs}
        is_nav = bool(names & {"data-nav-tab", "data-nav-menu-tab"})
        for name, value in attrs:
            if name in _URL_ATTRS and value:
                self.values.append((name, value, is_nav))


def _local_paths(html: str, *, body_only: bool) -> list[tuple[str, str]]:
    """(attribute, path) for every URL attribute pointing at this Shelf.
    External URLs (the ABS server's deep links) are skipped: a path on
    another host is not one of our routes, however it is spelled. With
    `body_only`, nav tab links are skipped too."""
    parser = _AttrCollector()
    parser.feed(html)
    out = []
    for name, value, is_nav in parser.values:
        parts = urlsplit(value)
        if parts.scheme or parts.netloc or not parts.path.startswith("/"):
            continue
        if body_only and is_nav:
            continue
        out.append((name, parts.path))
    return out


def _hits(html: str, key: str, gated: dict[str, list[re.Pattern]], *, body_only: bool = False) -> list[str]:
    found = [
        f"{name}={path}"
        for name, path in _local_paths(html, body_only=body_only)
        for rx in gated[key]
        if rx.fullmatch(path)
    ]
    found += [f"client {c}" for c in FEATURES[key].client if c in html]
    return found


def _pages(item_id: int, key: str | None) -> list[str]:
    pages = ["/", "/browse", "/browse?view=list", f"/item/{item_id}", "/scan", "/settings", "/trash"]
    if key != "stats":
        pages.insert(5, "/stats")
    return pages


@pytest.fixture
def census_seed(db):
    """Everything T2-T5 seeded, in one place: a book with an ISBN in a
    series with a gap, a Hardcover series total and id, its own ABS link,
    an open loan and an estimated value; a RomM-sourced item; a trashed
    item on an open loan; two valuation_history rows; one borrower; one
    share link; the Hardcover token and ABS URL settings."""
    borrower = _insert_borrower(db, name="Census Borrower")
    item_id = _insert_item(
        db, title="Census Book", isbn="9789000092014", media_type="book",
        series_name="Census Saga", series_position=1, owned=1,
        estimated_value=42.5, abs_id="li_census1", hardcover_book_id=4242,
    )
    _insert_item(
        db, title="Census Sibling Three", isbn="9789000092021", media_type="book",
        series_name="Census Saga", series_position=3, owned=1,
    )
    db.execute("INSERT INTO series_meta (name, hc_total) VALUES ('Census Saga', 9)")
    db.execute(
        "INSERT INTO checkouts (item_id, borrower_id, checked_out) VALUES (?, ?, datetime('now'))",
        (item_id, borrower),
    )
    _insert_item(db, title="Census RomM Game", isbn="9789000092038", source="romm")
    trashed = _insert_item(db, title="Census Trashed Loan", isbn="9789000092045")
    db.execute(
        "INSERT INTO checkouts (item_id, borrower_id, checked_out) VALUES (?, ?, datetime('now'))",
        (trashed, borrower),
    )
    item_write.trash_item(db, trashed)
    db.execute("INSERT INTO valuation_history (total_value, priced_count) VALUES (100, 5)")
    db.execute("INSERT INTO valuation_history (total_value, priced_count) VALUES (150, 6)")
    db.execute("INSERT INTO share_links (token, scope, label) VALUES ('census-tok', 'collection', 'Census Link')")
    db.execute("INSERT INTO settings (key, value) VALUES ('hardcover_token', 'hc-token')")
    db.execute("INSERT INTO settings (key, value) VALUES ('abs_url', 'https://abs.example')")
    db.commit()
    return item_id


def _render(client, url: str) -> str:
    resp = client.get(url)
    assert resp.status_code == 200, f"{url} answered {resp.status_code}"
    return resp.text


@pytest.mark.parametrize("key", list(FEATURES))
def test_a_turned_off_feature_leaves_no_use_control(key, admin_client, census_seed):
    gated = _gated_paths()
    off(key)
    problems = []
    for url in _pages(census_seed, key):
        for hit in _hits(_render(admin_client, url), key, gated):
            problems.append(f"{url}: {hit}")
    assert not problems, f"{key} is off, but pages that stay on still offer it:\n" + "\n".join(problems)


def test_everything_on_renders_each_swept_feature_and_every_client_name(admin_client, census_seed):
    gated = _gated_paths()
    html = {url: _render(admin_client, url) for url in _pages(census_seed, None)}
    # Nav tab links are left out here: every page carries them, so they would
    # satisfy this half for a feature whose page-body controls stopped
    # rendering. The negative half does read them.
    hit_features = {
        key for key in FEATURES
        if any(_hits(page, key, gated, body_only=True) for page in html.values())
    }

    # Every client name is used somewhere; a stale one would make the
    # negative half pass vacuously for that name.
    for key, feature in FEATURES.items():
        for name in feature.client:
            assert any(name in page for page in html.values()), f"client name {name!r} ({key}) renders nowhere"

    assert hit_features == SWEPT, (
        f"features with a use-control on a page that stays on: {sorted(hit_features)}; "
        f"declared: {sorted(SWEPT)} — update SWEPT and its reasons, or the seed"
    )


def test_path_regex_handles_converters():
    assert _path_regex("/api/valuate/{item_id:int}").fullmatch("/api/valuate/7")
    assert _path_regex("/api/series/{name:path}/rename").fullmatch("/api/series/A/B/rename")
    assert not _path_regex("/api/items/{item_id}/checkout").fullmatch("/api/items/1/2/checkout")
