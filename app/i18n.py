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
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

SUPPORTED = ("en", "it")
DEFAULT = "en"
LOCALE_DIR = Path(__file__).parent / "locales"
# Each language named in itself, so a reader who can't read the current UI
# can still find their own in the picker.
LANGUAGE_NAMES = {"en": "English", "it": "Italiano"}

current_locale: ContextVar[str] = ContextVar("current_locale", default=DEFAULT)


def N_(message: str) -> str:
    """Mark a string for extraction without translating it."""
    return message


def resolve(user_language: str | None, accept_language: str | None) -> str:
    """The locale for a request: the user's choice, else the browser's, else English.

    `Accept-Language` entries are ranked by q-value, ties by position; region
    subtags are dropped (`it-CH` is `it`), and `q=0` means "not this one".
    """
    if user_language in SUPPORTED:
        return user_language
    ranked = []
    for position, part in enumerate((accept_language or "").split(",")):
        tag, _, params = part.partition(";")
        lang = tag.strip().lower().split("-")[0]
        q = 1.0
        for param in params.split(";"):
            key, _, value = param.strip().lower().partition("=")
            if key == "q":
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0
        if lang in SUPPORTED and q > 0:
            ranked.append((-q, position, lang))
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
