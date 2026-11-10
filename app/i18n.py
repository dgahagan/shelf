"""UI translation: gettext catalogs, one locale per request.

English is the source language: every msgid is the English string, and `en`
has no catalog, so an untranslated string renders as written. The locale
lives in a contextvar that AuthMiddleware sets once per request, so
templates, route messages and the JS catalog all read the same value.

Translate at call time, never at import time. A module-level `gettext()`
runs before any request exists and freezes to English (GOTCHAS.md G154).
Strings stored in a registry (nav labels, headings) are marked with `N_()`
so extraction sees them, and translated where they are rendered.
"""

import gettext as _gettext
import os
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

# Locale identifiers in gettext's directory form: `it`, `zh_Hans`, `pt_BR`.
SUPPORTED = ("en", "it", "zh_Hans")
DEFAULT = "en"
LOCALE_DIR = Path(__file__).parent / "locales"
# Each language named in itself, so a reader who can't read the current UI
# can still find their own in the picker.
LANGUAGE_NAMES = {"en": "English", "it": "Italiano", "zh_Hans": "简体中文"}
# Regions that stand for a script, which no rule can derive (only Chinese).
ALIASES = {"zh_TW": "zh_Hant", "zh_HK": "zh_Hant", "zh_MO": "zh_Hant",
           "zh_CN": "zh_Hans", "zh_SG": "zh_Hans"}

current_locale: ContextVar[str] = ContextVar("current_locale", default=DEFAULT)


def enabled() -> bool:
    """Whether the translated UI is on. Off unless `SHELF_I18N` says on, while
    only part of the UI is translated; the flag goes once every area is.
    Read per call, so a test can flip it with monkeypatch."""
    return os.environ.get("SHELF_I18N", "").strip().lower() in ("1", "on", "true", "yes")


def N_(message: str) -> str:
    """Mark a string for extraction without translating it."""
    return message


def match(tag: str | None) -> str | None:
    """The supported locale for one language tag, most specific first.

    `pt-BR`, `pt_br` and `PT-br` are all `pt_BR`; `zh-Hant` is `zh_Hant`.
    Then an alias (`zh-TW` is `zh_Hant`), then the bare language, then the
    language's only shipped variant (a bare `zh` or `zh-Hant` finds `zh_Hans`
    while that is the one Chinese catalog). None when nothing fits.
    """
    subtags = (tag or "").strip().replace("_", "-").split("-")
    lang = subtags[0].lower()
    script = next((s.title() for s in subtags[1:] if len(s) == 4 and s.isalpha()), None)
    region = next((s.upper() for s in subtags[1:] if len(s) == 2 or (len(s) == 3 and s.isdigit())), None)
    for candidate in (
        "_".join(filter(None, (lang, script, region))),
        script and f"{lang}_{script}",
        region and ALIASES.get(f"{lang}_{region}"),
        lang,
    ):
        if candidate in SUPPORTED:
            return candidate
    # shortcut: no per-language default variant; add a {"zh": "zh_Hans"} map once a language ships two
    variants = [s for s in SUPPORTED if s.split("_")[0] == lang]
    return variants[0] if len(variants) == 1 else None


def resolve(user_language: str | None, accept_language: str | None) -> str:
    """The locale for a request: the user's choice, else the browser's, else English.

    `Accept-Language` entries are ranked by q-value, ties by position, and each
    maps to a locale through `match()`; `q=0` means "not this one".
    """
    if chosen := match(user_language):
        return chosen
    ranked = []
    for position, part in enumerate((accept_language or "").split(",")):
        tag, _, params = part.partition(";")
        q = 1.0
        for param in params.split(";"):
            key, _, value = param.strip().lower().partition("=")
            if key == "q":
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0
        if (locale := match(tag)) and q > 0:
            ranked.append((-q, position, locale))
    return min(ranked)[2] if ranked else DEFAULT


@lru_cache(maxsize=None)
def _catalog(domain: str, lang: str) -> _gettext.NullTranslations:
    return _gettext.translation(domain, LOCALE_DIR, [lang], fallback=True)


def gettext(message: str) -> str:
    # gettext("") returns the catalog's header block, not "".
    return message and _catalog("messages", current_locale.get()).gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _catalog("messages", current_locale.get()).ngettext(singular, plural, n)


def pgettext(context: str, message: str) -> str:
    return _catalog("messages", current_locale.get()).pgettext(context, message)


def js_catalog() -> dict[str, str]:
    """The `js` domain for the current locale as {English: translation}.

    Rendered into base.html as a JSON block for `t()` in app.js. Only plain
    strings: the header entry and plural forms are left out.
    """
    return _js_catalog(current_locale.get())


@lru_cache(maxsize=None)
def _js_catalog(lang: str) -> dict[str, str]:
    catalog = getattr(_catalog("js", lang), "_catalog", {})
    # ponytail: reads GNUTranslations' private dict; public API has no "all messages" call
    return {k: v for k, v in catalog.items() if isinstance(k, str) and k and v}
