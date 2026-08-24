"""Size guards for the modules split in Lever 5.

`app/routers/items.py` reached 2,481 lines and 30 routes — 38% of all router
code. It was flagged for splitting in `CODE_REVIEW_2026-03-28.md` and again in
`MULTI_USER_HOUSEHOLDS.md`, and it *grew* +135 lines between the two reviews.
`app/templates/settings.html` reached 1,517 lines with 15 `x-data` blocks,
5.3× the next-largest template, and was repeatedly described in plan docs as
"the largest template with the thinnest E2E coverage."

Nothing stopped either from growing, so these caps do. They are deliberately
loose — headroom for ordinary work, tight enough that a module drifting back
toward god-object size fails here and prompts a split instead of a review
comment nobody acts on.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# path -> (cap, what to do when it trips)
LIMITS = {
    "app/routers/items.py": (1600, "split by feature area, as items_covers/csv/catalog were"),
    "app/routers/items_common.py": (900, "move domain logic to app/services/"),
    "app/routers/items_covers.py": (600, "split the bulk-sweep routes out"),
    "app/routers/items_csv.py": (600, "move the row-mapping logic to app/services/"),
    "app/routers/items_catalog.py": (900, "split per provider (games / books / dvds)"),
    "app/templates/settings.html": (100, "it is a tab shell — new markup belongs in a fragment"),
    "app/templates/fragments/settings/integrations.html": (1000, "split per integration"),
    "app/templates/fragments/settings/data.html": (700, "split per panel"),
    "app/templates/fragments/settings/library.html": (500, "split per panel"),
    "app/templates/fragments/settings/users.html": (400, "split per panel"),
}


@pytest.mark.parametrize("rel_path,cap,advice", [(p, c, a) for p, (c, a) in LIMITS.items()])
def test_module_stays_under_its_cap(rel_path, cap, advice):
    path = REPO_ROOT / rel_path
    assert path.exists(), f"{rel_path} is missing — update LIMITS if it moved."
    lines = len(path.read_text().splitlines())
    assert lines <= cap, (
        f"{rel_path} is {lines} lines, over its {cap}-line cap. "
        f"This is the god-object guard: {advice}. Raise the cap only with a "
        "reason — it was set after a split that took items.py from 2,481 "
        "lines to 1,237 and settings.html from 1,517 to 31."
    )


# One route from each split module. If its module is not included, the path
# is simply absent from the app and every route beside it 404s too.
_ROUTE_PER_MODULE = {
    "items": "/api/search",
    "items_covers": "/api/items/{item_id}/cover-status",
    "items_csv": "/api/export/csv",
    "items_catalog": "/api/games/search",
}


def test_every_item_router_is_registered():
    """A split module nobody includes serves no routes, and a green suite
    would not notice — every route in it simply 404s.

    Asserted against the *built app*, not against main.py's source text. The
    earlier version grepped for `app.include_router(items_covers.router)`,
    which a commented-out line still contains — so the check passed while all
    six cover routes were gone. Verified by mutation.
    """
    from app.main import app

    registered = {route.path for route in app.routes}
    missing = {
        module: path
        for module, path in _ROUTE_PER_MODULE.items()
        if path not in registered
    }
    assert not missing, (
        "These item routers are not registered in app/main.py, so every route "
        f"they define returns 404: {missing}"
    )


def test_settings_shell_only_includes_tabs():
    """settings.html is a tab bar plus includes; panel markup goes in the
    fragment for its tab."""
    src = (REPO_ROOT / "app" / "templates" / "settings.html").read_text()
    for tab in ("library", "integrations", "data", "users"):
        assert f'fragments/settings/{tab}.html' in src, f"{tab} tab is not included"
