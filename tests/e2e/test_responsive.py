"""E2E: responsive-geometry gates for every top-level page.

The unit suite renders HTML and the lints read class strings, so neither can
see a layout that is the wrong shape — only rendered geometry can (G41). Two
failure modes, and a row can only ever hit one of them, so both are gated:

- `test_no_horizontal_overflow` — the row does not fit, so the layout viewport
  widens and the page scrolls sideways. #14 (List View button clipped), #33
  (intake title field 9 characters wide on a phone), #35 (Settings at 519/640px
  against a 390px viewport) and 0.8.0's nav-bar overflow are all this shape,
  and every one of them passed the whole suite before being reported by hand.
- `test_no_collapsed_text_controls` — the row fits *too well*: a `min-w-0`
  column has a hypothetical main size of 0, so it never triggers a wrap and
  shrinks to nothing while its fixed-width siblings keep every pixel (G43).
  The page measures perfectly and the control renders no characters, which is
  why an overflow gate alone is not enough. Found on its first run: the restore
  passphrase at 53px of content, and `item_edit.html`'s three-up row at 43px
  per field.

Both report the *innermost* offender and its geometry, because "the page is
640px wide" is not actionable and "passphrase input right edge 550px" is.
"""
import json
from contextlib import contextmanager

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import assert_page_clean, attach_page_guard, insert_item

pytestmark = pytest.mark.e2e

# 320 = smallest phone still in use (iPhone SE 1st gen / Galaxy Fold closed).
# 390 = iPhone 12–15 logical width, the modal phone and the width every bug in
#       this class was reported at. 430 = iPhone Pro Max.
#
# 640 and 768 are not devices: they are the `sm:` and `md:` breakpoints, and a
# breakpoint's own width is the worst case for the layout it turns on -- the
# wide row has just started rendering and has the least room it will ever have.
# G43 requires a measurement in that band, because it is where a `min-w-0`
# column silently shrinks to nothing rather than wrapping (its hypothetical
# main size is 0, so it never triggers a wrap) while its fixed-width siblings
# keep every pixel. `grep -rn basis-full app/templates/` lists the rows this
# covers: settings.html's three at `sm:`, intake.html's two at `lg:`.
#
# Add the breakpoint width here whenever a new stacking seam is introduced, or
# the gate straddles it exactly as the old 390/1280 floors did.
#
# 1024 joined the list when intake's review row moved its seam `md` -> `lg`:
# a seam's own width is the worst case for the layout it turns on, and the
# `md` seam had been passing locally by 4px while failing on CI by 8px.
VIEWPORTS = (320, 390, 430, 640, 768, 1024)

# (label, path template, data-testid to click after load or None)
#
# Settings is enumerated per tab: each pane is a separate `x-show` subtree, so
# measuring the page once only ever measures the default tab. #35 was reported
# against two different tabs with two different widths.
PAGES = (
    ("browse", "/browse", None),
    ("scan", "/scan", None),
    ("intake", "/intake", None),
    ("series", "/series", None),
    ("stats", "/stats", None),
    ("logs", "/logs", None),
    ("discover", "/discover", None),
    ("item detail", "/item/{item_id}", None),
    ("item edit", "/item/{item_id}/edit", None),
    ("settings:library", "/settings", "tab-library"),
    ("settings:integrations", "/settings", "tab-integrations"),
    ("settings:data", "/settings", "tab-data"),
    ("settings:users", "/settings", "tab-users"),
    # Last: /store registers a service worker, and nothing should navigate
    # through this context after that.
    ("store", "/store", None),
)

# Innermost overflowing elements + the document's own geometry. Returned as one
# object so a failure needs a single round trip.
#
# String expression, not a Python callable: Playwright serialises callables
# through a path the app's CSP can refuse (G21). Offenders are computed only
# when the document actually overflows, so an `overflow-x: auto` container
# scrolling its own wide table never shows up here.
_MEASURE = r"""
(function () {
    var docEl = document.documentElement;
    // getBoundingClientRect() is viewport-relative, so on a page that is
    // already scrolled sideways every offender's right edge reads as if it
    // fits. Clicking a control that sits past the fold -- which is exactly
    // what the Settings tab buttons do at 320px -- makes Chromium scroll it
    // into view, so this is not hypothetical: without the reset, the tab whose
    // own tab bar overflows reports no offender at all.
    window.scrollTo(0, 0);
    var limit = docEl.clientWidth;
    var result = {
        scrollWidth: docEl.scrollWidth,
        clientWidth: limit,
        offenders: []
    };
    if (docEl.scrollWidth <= limit + 1) return result;

    var over = [];
    var all = document.querySelectorAll('body *');
    for (var i = 0; i < all.length; i++) {
        var el = all[i];
        var r = el.getBoundingClientRect();
        // Zero-area covers display:none (Alpine's x-show) and x-cloak.
        if (r.width === 0 || r.height === 0) continue;
        if (r.right <= limit + 1) continue;
        over.push(el);
    }
    // An ancestor overflows whenever a descendant does; only the innermost
    // offender names the element whose width is actually wrong.
    var innermost = over.filter(function (el) {
        return !over.some(function (o) { return o !== el && el.contains(o); });
    });

    result.offenders = innermost.map(function (el) {
        var desc = el.tagName.toLowerCase();
        if (el.id) desc += '#' + el.id;
        var tid = el.getAttribute('data-testid');
        if (tid) desc += '[data-testid=' + tid + ']';
        var cls = (el.getAttribute('class') || '').trim();
        if (cls) desc += '.' + cls.split(/\s+/).slice(0, 5).join('.');
        var r = el.getBoundingClientRect();
        return {
            desc: desc,
            right: Math.round(r.right),
            width: Math.round(r.width),
            text: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 48)
        };
    }).sort(function (a, b) { return b.right - a.right; }).slice(0, 6);

    // A block element does not grow to fit an unwrappable inline run, so a
    // wide text node widens the document while every element rect stays put --
    // the element probe above then finds nothing. Range measures the painted
    // text box itself, which is the only thing that names such an overflow.
    if (result.offenders.length === 0) {
        var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        var texts = [];
        var node;
        while ((node = walker.nextNode())) {
            if (!node.nodeValue || !node.nodeValue.trim()) continue;
            var range = document.createRange();
            range.selectNodeContents(node);
            var tr = range.getBoundingClientRect();
            range.detach();
            if (tr.width === 0 || tr.right <= limit + 1) continue;
            var owner = node.parentElement;
            var odesc = owner ? owner.tagName.toLowerCase() : '(detached)';
            if (owner && owner.id) odesc += '#' + owner.id;
            var ocls = owner ? (owner.getAttribute('class') || '').trim() : '';
            if (ocls) odesc += '.' + ocls.split(/\s+/).slice(0, 5).join('.');
            texts.push({
                desc: 'text node in ' + odesc,
                right: Math.round(tr.right),
                width: Math.round(tr.width),
                text: node.nodeValue.replace(/\s+/g, ' ').trim().slice(0, 48)
            });
        }
        result.offenders = texts.sort(function (a, b) {
            return b.right - a.right;
        }).slice(0, 6);
    }

    return result;
})()
"""

# Below this many pixels of *content* box, a text field renders so few
# characters that it is functionally broken. 80px is set just under the
# narrowest control that survives the current layout (87px, the add-user row at
# 320px) and comfortably above G43's 26px evidence — tighten it as layouts
# improve, but never loosen it to make a regression pass.
_MIN_CONTENT_PX = 80

# Text-entry controls only. A <select> is sized by its widest option and is
# legitimately narrow; a file input carries its own button; checkboxes and
# radios have no content box to speak of.
_COLLAPSE = r"""
(function () {
    window.scrollTo(0, 0);
    var TEXTY = ['text', 'password', 'number', 'search', 'email', 'url', 'tel'];
    var out = [];
    var els = document.querySelectorAll('input, textarea');
    for (var i = 0; i < els.length; i++) {
        var el = els[i];
        if (el.tagName.toLowerCase() === 'input' && TEXTY.indexOf(el.type) === -1) continue;
        // Opt-out for controls that are deliberately pill-sized rather than
        // squeezed -- their width is identical at 320px and 1280px, so there is
        // no seam to fix. Kept as an attribute so every exemption is greppable
        // (`grep -rn data-narrow-ok app/templates/`) instead of being hidden in
        // a lowered threshold.
        if (el.hasAttribute('data-narrow-ok')) continue;
        var r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        var cs = getComputedStyle(el);
        var content = r.width
            - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)
            - parseFloat(cs.borderLeftWidth) - parseFloat(cs.borderRightWidth);
        if (content >= __MIN__) continue;
        var desc = (el.tagName.toLowerCase()) + '[type=' + (el.type || '') + ']';
        if (el.id) desc += '#' + el.id;
        else if (el.name) desc += '[name=' + el.name + ']';
        if (el.placeholder) desc += ' placeholder=' + JSON.stringify(el.placeholder);
        out.push({desc: desc, content: Math.round(content), box: Math.round(r.width)});
    }
    return out;
})()
""".replace("__MIN__", str(_MIN_CONTENT_PX))


_CLOAK_CLEAR = "document.querySelectorAll('[x-cloak]').length === 0"


def _settle(page, timeout_ms: int = 5_000) -> None:
    """Wait for Alpine to have painted, then let layout settle.

    Polled from Python over `page.evaluate`, never `page.wait_for_function` —
    the app's CSP refuses the `eval()` that one runs through (G21). Measuring
    before `x-cloak` clears reads the pre-Alpine layout, which is a different
    (and usually narrower) page than the one the user sees (G42).
    """
    page.wait_for_load_state("networkidle")
    deadline = timeout_ms
    while deadline > 0:
        if page.evaluate(_CLOAK_CLEAR):
            break
        page.wait_for_timeout(100)
        deadline -= 100
    else:
        raise AssertionError(
            f"[x-cloak] never cleared on {page.url} within {timeout_ms}ms — "
            "Alpine did not finish initialising, so any measurement here "
            "would describe the pre-Alpine layout (G42)."
        )
    # Transitions and font swap: both move a right edge after networkidle.
    page.wait_for_timeout(150)


def _format(width: int, failures: list[dict]) -> str:
    lines = [
        f"{len(failures)} page(s) scroll horizontally at a {width}px viewport. "
        "The layout viewport widens, so the browser zooms out and the page "
        "scrolls sideways."
    ]
    for f in failures:
        lines.append(
            f"\n  {f['label']} ({f['path']}): "
            f"scrollWidth {f['scrollWidth']}px > clientWidth {f['clientWidth']}px "
            f"(+{f['scrollWidth'] - f['clientWidth']}px)"
        )
        if not f["offenders"]:
            lines.append(
                "      no offending element or text node resolved — suspect "
                "a pseudo-element (::before/::after) or a replaced element's "
                "intrinsic size; measure by hand."
            )
        for o in f["offenders"]:
            text = f" — {o['text']!r}" if o["text"] else ""
            lines.append(
                f"      right {o['right']}px  w {o['width']}px  {o['desc']}{text}"
            )
    lines.append(
        "\nFix the element, not the test: give the row a wrap seam wider than "
        "the sum of its own fixed-width children (G43), and prefer basis-full "
        "over a fixed width (G41)."
    )
    return "\n".join(lines)


@pytest.fixture(scope="module")
def wide_item(live_server):
    """One item with long-but-wrappable text in every field, seeded once.

    Module-scoped on purpose: `live_server` (and so the DB) is session-scoped,
    so inserting per-parametrisation collides on `UNIQUE(isbn, media_type)` the
    second time round.

    Every field is long because a detail/edit page only reveals its narrow-
    viewport seams when the fields actually have something to overflow with.
    The text wraps, though — an unbreakable run has nothing to wrap at, which
    is a separate robustness question and would mask the seams measured here.

    The filler rows exist because /browse's header is `whitespace-nowrap` and
    contains the item count, so the toolbar's width grows with the number of
    digits in it. With a single item the row fitted 320px and the gate passed;
    in a full-suite run, where earlier tests had left four digits behind, the
    same row overflowed by 5px. Seeding two digits here makes the measurement
    independent of test order — the shared session DB means the real count is
    only ever higher, and the row now wraps either way.
    """
    for i in range(12):
        insert_item(
            live_server["data_dir"],
            title=f"Filler Volume {i}",
            isbn=f"97800000009{i:02d}",
        )
    return insert_item(
        live_server["data_dir"],
        title="A Deliberately Long Title For Measuring Layout Overflow",
        isbn="9780000000401",
        authors="Ann Author, Bee Coauthor",
        series_name="The Very Long Series Name For Measurement",
        series_position=1,
        publisher="A Publisher With A Long Name",
        notes=(
            "A deliberately long note, written as real prose so it wraps "
            "the way user text does."
        ),
    )


@contextmanager
def _walk_pages(live_server, browser, setup_admin, item_id, width):
    """Log in once at `width`, then yield (label, path, page) for every page.

    One context and one login per viewport rather than per page: the login is a
    real form POST plus a navigation, and this suite runs it once per fixture
    use otherwise.
    """
    ctx = browser.new_context(viewport={"width": width, "height": 800})
    page = attach_page_guard(ctx.new_page())

    def _pages():
        page.goto(f"{live_server['url']}/login")
        page.fill("input[name=username]", setup_admin["username"])
        page.fill("input[name=password]", setup_admin["password"])
        page.click("button[type=submit]")
        page.wait_for_url(f"{live_server['url']}/browse", timeout=10_000)

        for label, template, testid in PAGES:
            path = template.format(item_id=item_id)
            page.goto(f"{live_server['url']}{path}")
            _settle(page)
            if testid:
                page.click(f"[data-testid={testid}]")
                _settle(page)
            yield label, path, page

    try:
        yield _pages()
        assert_page_clean(page)
    finally:
        ctx.close()


@pytest.mark.parametrize("width", VIEWPORTS)
def test_no_horizontal_overflow(live_server, browser, setup_admin, wide_item, width):
    """Every top-level page must fit `width` without a horizontal scrollbar."""
    failures = []
    with _walk_pages(live_server, browser, setup_admin, wide_item, width) as pages:
        for label, path, page in pages:
            m = page.evaluate(_MEASURE)
            if m["scrollWidth"] > m["clientWidth"] + 1:
                failures.append({"label": label, "path": path, **m})
    assert not failures, _format(width, failures)


@pytest.mark.parametrize("width", VIEWPORTS)
def test_no_collapsed_text_controls(live_server, browser, setup_admin, wide_item, width):
    """No text-entry control may be squeezed below a usable width.

    The other half of the G43 class, and the half an overflow gate cannot see:
    a flex column with `min-w-0` has a hypothetical main size of 0, so it never
    triggers a wrap — it shrinks to nothing while its fixed-width siblings keep
    every pixel. The page fits its viewport perfectly and the control renders
    no characters. G43's own evidence was a title field measuring 26px, and
    this gate found the restore passphrase at 53px content (~4 characters) at
    320px, on a row that passed the overflow gate.
    """
    failures = []
    with _walk_pages(live_server, browser, setup_admin, wide_item, width) as pages:
        for label, path, page in pages:
            for c in page.evaluate(_COLLAPSE):
                failures.append({"label": label, "path": path, **c})

    assert not failures, "\n".join(
        [
            f"{len(failures)} text control(s) squeezed below {_MIN_CONTENT_PX}px "
            f"of content box at a {width}px viewport. The page fits, so the "
            f"overflow gate passes — but these render almost no characters."
        ]
        + [
            f"  {f['label']} ({f['path']}): content {f['content']}px "
            f"(box {f['box']}px)  {f['desc']}"
            for f in failures
        ]
        + [
            "",
            "Give the control `basis-full` so it takes its own line below the "
            "seam (G43). If it already has `sm:flex-1`, reset with that alone "
            "and never add `sm:basis-auto` beside it (G41).",
        ]
    )


# ---------------------------------------------------------------------------
# Browse list view — the table this gate could not previously see
# ---------------------------------------------------------------------------
#
# `PAGES` walks /browse in its DEFAULT view, which is grid (`browse.js`'s
# `shelf-view` fallback), so the list-view table was unmeasured at every
# viewport in this file. Issue #30 dropped the per-cell `hidden md:table-cell`
# classes — the user's column selection is authoritative at every width now —
# which widens the default phone table and makes measuring it the point.
#
# It cannot join `PAGES`: that fixture's page walk shares one context and one
# login across every entry, so it has nowhere to seed per-entry localStorage,
# and the view mode plus the column selection both live there.
#
# The contract being asserted is deliberately the *document*'s, not the
# table's: a wide selection is expected to scroll sideways INSIDE the table's
# own `.overflow-x-auto` wrapper (design plan §5). That is what keeps the page
# itself from scrolling, and it is why an all-columns-on selection is a passing
# case rather than a failing one.

_LIST_SELECTIONS = ("default", "all_on")

#: Rendered width of the Title cell's link. Title is locked — it is the row's
#: only link to the item — so it is the one column that must stay readable at
#: every width and under every selection. Same floor as the text-control gate:
#: G43's evidence is a title crushed to 26px, which nothing flagged.
_TITLE_MEASURE = r"""
(function () {
    window.scrollTo(0, 0);
    var a = document.querySelector('td[data-col="title"] a');
    if (!a) return null;
    var r = a.getBoundingClientRect();
    return {width: Math.round(r.width), text: (a.textContent || '').trim().slice(0, 40)};
})()
"""


@contextmanager
def _browse_list_at(live_server, browser, setup_admin, width, selection):
    """A logged-in /browse in LIST view at `width`, with `selection` applied.

    The login is not optional and is the whole reason this is a helper: an
    `add_init_script` has to run before the first navigation, so the context
    must be built here rather than taken from `authed_page` — and a fresh
    context has no session, so `/browse` would just redirect to `/login` and
    the first wait below would time out on a page that never rendered a table.
    """
    from app import browse_columns

    if selection == "all_on":
        cols = {c.name: True for c in browse_columns.COLUMNS if not c.locked}
    else:
        cols = None

    seed = "localStorage.setItem('shelf-view', 'list');"
    if cols is not None:
        seed += f"localStorage.setItem('shelf-columns', {json.dumps(json.dumps(cols))});"

    ctx = browser.new_context(viewport={"width": width, "height": 800})
    ctx.add_init_script(seed)
    page = attach_page_guard(ctx.new_page())
    try:
        page.goto(f"{live_server['url']}/login")
        page.fill("input[name=username]", setup_admin["username"])
        page.fill("input[name=password]", setup_admin["password"])
        page.click("button[type=submit]")
        page.wait_for_url(f"{live_server['url']}/browse", timeout=10_000)
        page.goto(f"{live_server['url']}/browse")
        # G42: the list view lives inside <template x-if="viewMode === 'list'">,
        # so its cells are ATTACHED a tick before they are painted and every
        # rect in that window reads zero. Wait for a positive painted state on
        # a locked, always-on column before measuring anything.
        expect(page.locator('td[data-col="title"]').first).to_be_visible()
        _settle(page)
        yield page
        assert_page_clean(page)
    finally:
        ctx.close()


@pytest.mark.parametrize("selection", _LIST_SELECTIONS)
@pytest.mark.parametrize("width", VIEWPORTS)
def test_browse_list_view_has_no_horizontal_overflow(
    live_server, browser, setup_admin, wide_item, width, selection
):
    """The list-view table must not make the PAGE scroll sideways.

    It is allowed — expected — to scroll inside its own `.overflow-x-auto`
    wrapper with a wide selection. That containment is the design decision
    that let the responsive breakpoints be dropped from the cells.
    """
    with _browse_list_at(live_server, browser, setup_admin, width, selection) as page:
        m = page.evaluate(_MEASURE)
        assert m["scrollWidth"] <= m["clientWidth"] + 1, _format(
            width, [{"label": f"browse list ({selection})", "path": "/browse", **m}]
        )


@pytest.mark.parametrize("selection", _LIST_SELECTIONS)
@pytest.mark.parametrize("width", VIEWPORTS)
def test_browse_list_title_stays_readable(
    live_server, browser, setup_admin, wide_item, width, selection
):
    """Title is the row's link to the item — crushing it makes the row dead.

    This is G43's other half: the overflow gate above cannot see a column
    squeezed to nothing, because a squeezed column makes the page *narrower*,
    not wider.
    """
    with _browse_list_at(live_server, browser, setup_admin, width, selection) as page:
        t = page.evaluate(_TITLE_MEASURE)
        assert t is not None, "no title cell rendered — the list view did not paint"
        assert t["width"] >= _MIN_CONTENT_PX, (
            f"Title link is {t['width']}px at a {width}px viewport with the "
            f"{selection!r} column selection (floor {_MIN_CONTENT_PX}px): "
            f"{t['text']!r}.\nFix the cell, not the test — `min-w-[8rem]` on the "
            "title cell is the expected remedy (G43)."
        )
