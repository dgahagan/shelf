#!/usr/bin/env python3
"""UI catalogs: extract, merge, compile, and the check-i18n tripwire.

    python scripts/i18n.py update    # extract strings, merge into every .po
    python scripts/i18n.py compile   # .po -> committed .mo; stamps the .po's
                                     # PO-Revision-Date if a translation changed
    python scripts/i18n.py --check   # fail if a .po misses an extracted string,
                                     # a .mo is stale against its .po, or a
                                     # template string has a bare %

Two domains: `messages` (app/ Python and templates, keywords `_`, `gettext`,
`ngettext`, `pgettext`, `N_`) and `js` (static/js, keyword `t`). The .mo files
are committed so the image needs no Babel, which makes a stale one a real
bug: the .po says Italian, the app still serves English. No .pot is kept;
extraction runs fresh every time, so there is nothing to drift.
"""

import io
from datetime import datetime
import re
import sys
from pathlib import Path

from babel.messages.catalog import Catalog
from babel.messages.extract import extract_from_dir
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po, write_po

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "app" / "locales"
# Every language with a catalog. English is the source and has none.
LANGUAGES = ("it",)
# domain -> (directory, Babel method map relative to it, keywords)
DOMAINS = {
    "messages": ("app", [("**.py", "python"), ("templates/**.html", "jinja2")],
                 {"_": None, "gettext": None, "ngettext": (1, 2),
                  "pgettext": ((1, "c"), 2), "N_": None}),
    "js": ("static/js", [("**.js", "javascript")], {"t": None}),
}
JINJA_OPTIONS = {"templates/**.html": {"extensions": "jinja2.ext.i18n"}}

# Babel always writes these headers into the .po and .mo. POT-Creation-Date
# changes on every run and Generated-By on every Babel bump, so either would
# churn the catalogs (and their PRs) with no translation changed; gettext
# reads none of them, so they're dropped.
# ponytail: wraps Babel's private _get_mime_headers; recheck on a Babel bump.
_DROPPED_HEADERS = {"Project-Id-Version", "POT-Creation-Date", "Generated-By"}
Catalog.mime_headers = property(
    lambda self: [h for h in Catalog._get_mime_headers(self) if h[0] not in _DROPPED_HEADERS],
    Catalog._set_mime_headers,
)


def _extract(domain: str) -> Catalog:
    subdir, method_map, keywords = DOMAINS[domain]
    catalog = Catalog()
    for filename, lineno, message, comments, context in extract_from_dir(
        ROOT / subdir, method_map, JINJA_OPTIONS, keywords=keywords,
        comment_tags=("Translators:",), strip_comment_tags=True,
    ):
        catalog.add(message, None, [(f"{subdir}/{filename}", lineno)],
                    auto_comments=comments, context=context)
    return catalog


def _po_path(lang: str, domain: str) -> Path:
    return LOCALES / lang / "LC_MESSAGES" / f"{domain}.po"


def _read(path: Path) -> Catalog:
    with open(path, "rb") as f:
        return read_po(f)


def _mo_bytes(catalog: Catalog) -> bytes:
    buf = io.BytesIO()
    write_mo(buf, catalog)
    return buf.getvalue()


def _write_po(path: Path, catalog: Catalog) -> None:
    with open(path, "wb") as f:
        write_po(f, catalog, width=0, omit_header=False, ignore_obsolete=True)


def update() -> None:
    for domain in DOMAINS:
        template = _extract(domain)
        for lang in LANGUAGES:
            path = _po_path(lang, domain)
            if path.exists():
                catalog = _read(path)
                catalog.update(template, no_fuzzy_matching=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                catalog = Catalog(locale=lang, domain=domain, project="Shelf",
                                  copyright_holder="Shelf contributors", fuzzy=False)
                catalog.update(template, no_fuzzy_matching=True)
            _write_po(path, catalog)
            print(f"updated {path.relative_to(ROOT)}")


def compile_all() -> None:
    for domain in DOMAINS:
        for lang in LANGUAGES:
            path = _po_path(lang, domain)
            catalog = _read(path)
            mo = path.with_suffix(".mo")
            # A .mo holds only translated strings, so a different one means the
            # translations changed: stamp the .po. New empty msgids don't count.
            if not mo.exists() or mo.read_bytes() != _mo_bytes(catalog):
                catalog.revision_date = datetime.now().astimezone()
                _write_po(path, catalog)
            mo.write_bytes(_mo_bytes(catalog))
            print(f"compiled {mo.relative_to(ROOT)}")


def _bare_percent(text: str) -> bool:
    return re.search(r"%(?![(%])", text.replace("%%", "")) is not None


def check() -> int:
    problems = []
    for domain in DOMAINS:
        extracted = _extract(domain)
        wanted = {(m.id, m.context) for m in extracted if m.id}
        for m in extracted:
            # Jinja's newstyle gettext always applies `msg % variables`, so a
            # bare % in a template string 500s the page; write it as %%.
            ids = m.id if isinstance(m.id, tuple) else (m.id,)
            if any(f.startswith("app/templates/") for f, _ in m.locations) and any(
                    _bare_percent(i) for i in ids):
                problems.append(f"{m.locations[0][0]}: bare % in {m.id!r}, write %%")
        for lang in LANGUAGES:
            path = _po_path(lang, domain)
            if not path.exists():
                problems.append(f"{path.relative_to(ROOT)} is missing")
                continue
            catalog = _read(path)
            have = {(m.id, m.context) for m in catalog if m.id}
            for m in catalog:
                if not any(f.startswith("app/templates/") for f, _ in m.locations):
                    continue
                ids = m.id if isinstance(m.id, tuple) else (m.id,)
                strs = [s for s in (m.string if isinstance(m.string, tuple) else (m.string,)) if s]
                names = {n for i in ids for n in re.findall(r"%\((\w+)\)", i)}
                for s in strs:
                    if _bare_percent(s) or not set(re.findall(r"%\((\w+)\)", s)) <= names:
                        problems.append(f"{path.relative_to(ROOT)}: msgstr {s!r} for {m.id!r}: "
                                        "use %% for a percent sign and only the msgid's %(name)s")
            for msgid, _ in sorted(wanted - have, key=str):
                problems.append(f"{path.relative_to(ROOT)}: not extracted: {msgid!r}")
            mo = path.with_suffix(".mo")
            if not mo.exists() or mo.read_bytes() != _mo_bytes(catalog):
                problems.append(f"{mo.relative_to(ROOT)} is stale")
    for p in problems:
        print(p)
    if problems:
        print("Run `make i18n` (update + compile), translate, then `make i18n` again.")
        return 1
    print("i18n catalogs OK")
    return 0


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "update":
        update()
    elif arg == "compile":
        compile_all()
    elif arg == "--check":
        sys.exit(check())
    else:
        sys.exit(__doc__)
