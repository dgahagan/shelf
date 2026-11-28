"""The base-path lint (#165): every URL Shelf emits carries SHELF_BASE_PATH.

Guards the guard on a tmp tree — one red and one green case per rule.
"""
import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_base_path.py"
_spec = importlib.util.spec_from_file_location("check_base_path", _SCRIPT)
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


def _tree(tmp_path, templates=None, scripts=None, sw=None, python=None):
    (tmp_path / "app/templates").mkdir(parents=True)
    (tmp_path / "static/js").mkdir(parents=True)
    for name, body in (templates or {}).items():
        p = tmp_path / "app/templates" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    for name, body in (scripts or {}).items():
        (tmp_path / "static/js" / name).write_text(body)
    if sw is not None:
        (tmp_path / "static/sw.js").write_text(sw)
    for name, body in (python or {}).items():
        (tmp_path / "app" / name).write_text(body)
    return lint.find_violations(tmp_path)


@pytest.mark.parametrize("body", [
    '<a href="/x">x</a>',
    '<form action="/api/x"></form>',
    '<button hx-post="/api/x"></button>',
    '<div hx-get="/api/x?a=1"\n     hx-target="#t"></div>',
    '<img src="/{{ item.cover_path }}">',
    '<a href="{% if a %}/a{% else %}/b{% endif %}">',
    '<a href="{{ tab.path }}">',
    '<a @click="go(\'/item/1\')">',
    '<img :src="x || \'/covers/1.jpg\'">',
    "{% set action_prefix = '/api/items' %}",
    "<form hx-post=\"{{ base_path }}{{ scan_action|default('/api/scan', true) }}\">",
    "<p>{{ _('Add it in <a href=\"/settings\">Settings</a>') }}</p>",
    # Diff review F2: a root URL passed as a call argument inside {{ ... }}.
    "<p>{{ _('Add it in <a href=\"%(u)s\">Settings</a>', u='/settings') }}</p>",
    # Diff review codex M3: single-quoted attributes.
    "<a href='/escape'>",
    # Diff review F6: every attribute in the list is read.
    '<a hx-push-url="/browse?q=1">',
    '<a hx-replace-url="/browse">',
    '<button formaction="/api/x">',
    '<video poster="/covers/1.jpg">',
    '<img srcset="/covers/1.jpg 2x">',
])
def test_template_red(tmp_path, body):
    assert _tree(tmp_path, templates={"t.html": body}), body


@pytest.mark.parametrize("body", [
    '<a href="{{ base_path }}/x">x</a>',
    '<button hx-post="{{ base_path }}/api/x"></button>',
    '<img src="{{ base_path }}/{{ item.cover_path }}">',
    '<a href="{{ base_path }}{{ tab.path }}">',
    '<a href="#section">',
    '<a href="https://example.com/x">',
    '<a href="//cdn.example/x">',
    '<a @click="go(\'{{ base_path }}/item/1\')">',
    '{# href="/x" is how it used to read #}',
    '<a href="/x">x</a> {# base-path: ok — test fixture #}',
    "<p>{{ _('Add it in <a href=\"%(u)s\">Settings</a>', u=base_path ~ '/settings') }}</p>",
    "<span>{{ _('/ %(n)s with ISBN', n=total) }}</span>",
])
def test_template_green(tmp_path, body):
    assert _tree(tmp_path, templates={"t.html": body}) == [], body


def test_bare_marker_is_a_violation(tmp_path):
    v = _tree(tmp_path, templates={"t.html": '<a href="/x">x</a> {# base-path: ok #}'})
    assert len(v) == 1 and "no reason" in v[0]


def test_allowlist_keys_on_the_full_path(tmp_path):
    ok = _tree(tmp_path, templates={"upcoming.html": '<img src="{{ r.cover_url }}">'})
    assert ok == []
    other = tmp_path / "other"
    assert _tree(other, templates={"x/upcoming.html": '<img src="{{ r.cover_url }}">'})


@pytest.mark.parametrize("body", [
    "fetch('/api/x', {})",
    'fetch("/api/x")',
    "fetch(`/api/x/${id}`)",
    "var u = '/item/' + id;",
    "document.querySelector('form[hx-post=\"/api/scan\"]')",
    "el.style.src = 'url(/static/fonts/a.woff2)';",
    # Diff review codex M3: a path may start with any path character.
    "fetch('/1')",
    "location.href = '/-private';",
    "location.href = '/.well-known/x';",
])
def test_script_red(tmp_path, body):
    assert _tree(tmp_path, scripts={"a.js": body}), body


@pytest.mark.parametrize("body", [
    "fetch(shelfUrl('/api/x'), {})",
    "fetch(shelfUrl('/api/items/' + id + '/cover'))",
    "var u = shelfUrl('/item/' + id);",
    "el.style.src = 'url(' + shelfUrl('/static/fonts/a.woff2') + ')';",
    "// fetch('/api/x') in a comment\n/* and '/api/y' here */",
    "var parts = path.split('/');",
    "var re = 'https://example.com/x';",
    "fetch('/api/x') // base-path: ok — test fixture",
])
def test_script_green(tmp_path, body):
    assert _tree(tmp_path, scripts={"a.js": body}) == [], body


@pytest.mark.parametrize("body", [
    'HTML = f"<a href=\\"/settings\\">Settings</a>"',
    "HTML = '<img src=\"/covers/1.jpg\">'",
    "HTML = '<button hx-post=\"/api/x\">'",
])
def test_python_red(tmp_path, body):
    """Diff review F3: HTML built in a router or service is read too."""
    assert _tree(tmp_path, python={"r.py": body}), body


@pytest.mark.parametrize("body", [
    '@router.get("/api/x")',
    'HTML = f"<a href=\\"{app_config.BASE_PATH}/settings\\">Settings</a>"',
    'URL = "https://example.com/x"',
    "HTML = '<a href=\"/x\">'  # base-path: ok — test fixture",
])
def test_python_green(tmp_path, body):
    assert _tree(tmp_path, python={"r.py": body}) == [], body


def test_a_path_argument_of_another_kind_is_skipped(tmp_path):
    other = tmp_path / "notes.txt"
    other.write_text('<a href="/x">')
    assert lint.find_violations(tmp_path, [str(other)]) == []


def test_precache_entries_are_exempt_but_not_the_rest_of_sw(tmp_path):
    sw = "const PRECACHE = [\n  '/static/css/app.css',\n  '/static/js/store.js',\n];\nfetch('/store');\n"
    v = _tree(tmp_path, sw=sw)
    assert len(v) == 1 and ":5:" in v[0]


def test_shelf_url_helper_itself_is_exempt(tmp_path):
    assert _tree(tmp_path, scripts={"shelf-url.js": "var x = '/a';"}) == []


def test_tree_is_clean():
    violations = lint.find_violations()
    assert not violations, "\n" + "\n".join(violations)


def test_every_allowlist_entry_still_matches():
    """A stale exemption must not outlive the line it excused."""
    root = _SCRIPT.parent.parent
    for rel, expr in lint.ALLOWLIST:
        text = (root / rel).read_text()
        assert "{{ " + expr + " }}" in text, (rel, expr)
