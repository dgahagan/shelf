"""Routers are registered in `app/main.py`, never by importing the package.

Several contributed features arrived composing themselves from
`app/routers/__init__.py` — importing sibling routers at package-import time
and mutating `pages.router` (or a service's allow-list) as a side effect. It
works, which is what makes it worth a tripwire: `import app.routers.anything`
then silently rewires the application, the registration order stops being
readable from one place, and two such features conflict on a file neither of
them is really about.

`app/main.py` registers every router explicitly, in one visible list. These
tests hold that.
"""

from pathlib import Path

import pytest


APP = Path(__file__).parents[1] / "app"


def test_routers_package_init_is_empty():
    """`app/routers/__init__.py` carries no imports and no side effects."""
    init = APP / "routers" / "__init__.py"
    assert init.read_text().strip() == "", (
        "app/routers/__init__.py must stay empty — register routers in "
        "app/main.py instead of composing them at package-import time")


def test_no_router_includes_another_router_at_import_time():
    """Only app/main.py calls include_router."""
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        if path.name == "main.py":
            continue
        if "include_router" in path.read_text():
            offenders.append(path.relative_to(APP.parent).as_posix())
    assert offenders == [], (
        f"include_router outside app/main.py: {offenders}")


@pytest.mark.parametrize("route", [
    "/shelf-fill",
    "/api/shelf-fill/place",
    "/api/shelf-fill/scan",
])
def test_shelf_fill_routes_reachable_through_main(route):
    """Shelf Fill survives the move off the package-init composition."""
    from app.main import app

    assert route in {r.path for r in app.routes}
