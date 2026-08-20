"""Navigation tab registry and per-request visibility resolution.

The nav renders on every page, so the settings it depends on are read once
and cached at module level; every settings write path that can change a
nav-relevant value calls `invalidate_cache()`.

Visibility is presentation only — hidden tabs keep serving their routes.
Role gating is the exception: it mirrors the security rules the routers
already enforce and always wins over the visibility config.
"""

import json
import logging

logger = logging.getLogger(__name__)

NAV_TABS = [
    {"key": "browse", "label": "Browse", "path": "/browse"},
    {"key": "scan", "label": "Scan", "path": "/scan", "roles": ("admin", "editor")},
    {"key": "intake", "label": "Intake", "path": "/intake", "roles": ("admin", "editor"),
     "requires": "vision"},
    {"key": "store", "label": "Store", "path": "/store"},
    {"key": "series", "label": "Series", "path": "/series"},
    {"key": "discover", "label": "Discover", "path": "/discover", "requires": "hardcover"},
    {"key": "stats", "label": "Stats", "path": "/stats"},
    {"key": "settings", "label": "Settings", "path": "/settings", "roles": ("admin",)},
    {"key": "logs", "label": "Logs", "path": "/logs", "roles": ("admin",)},
]

# The page that controls visibility must stay reachable, and so must the
# collection itself — neither can be hidden, however the form is submitted.
ALWAYS_VISIBLE = ("browse", "settings")

HIDEABLE_TABS = [t for t in NAV_TABS if t["key"] not in ALWAYS_VISIBLE]
HIDEABLE_KEYS = frozenset(t["key"] for t in HIDEABLE_TABS)

# Every settings key the nav depends on, read together so one cold read
# covers a whole page render.
NAV_SETTING_KEYS = (
    "vision_provider",
    "anthropic_api_key",
    "openai_api_key",
    "hardcover_token",
    "nav_hidden_tabs",
)

_cached_settings: dict[str, str] | None = None


def invalidate_cache() -> None:
    """Drop the cached settings so the next render re-reads them."""
    global _cached_settings
    _cached_settings = None


def _nav_settings() -> dict[str, str]:
    """Nav-relevant settings, with env overrides applied and secrets decrypted.

    Read through `get_setting` rather than the raw table so an env-provided
    token counts as configured and `hardcover_token` is decrypted.
    """
    global _cached_settings
    if _cached_settings is None:
        from app.database import get_db, get_setting
        try:
            with get_db() as db:
                _cached_settings = {k: get_setting(db, k) for k in NAV_SETTING_KEYS}
        except Exception:
            # A nav that can't read settings must not take the page down;
            # fall back to "nothing configured, nothing hidden" for this
            # render and try again on the next one.
            logger.warning("Could not read nav settings; falling back to defaults", exc_info=True)
            return {k: "" for k in NAV_SETTING_KEYS}
    return _cached_settings


def _is_configured(requirement: str, settings: dict[str, str]) -> bool:
    """Whether an integration is set up enough for its tab to do anything.

    Mirrors what each feature itself enforces before running: the vision
    backends' hard requirements (`app/services/vision.py`) and the presence
    of a Hardcover token.
    """
    if requirement == "vision":
        provider = (settings.get("vision_provider") or "").strip()
        if provider == "anthropic":
            return bool((settings.get("anthropic_api_key") or "").strip())
        if provider == "openai":
            return bool((settings.get("openai_api_key") or "").strip())
        if provider == "ollama":
            return True  # URL and model both have defaults
        return False
    if requirement == "hardcover":
        return bool((settings.get("hardcover_token") or "").strip())
    return True  # an unknown requirement never hides a tab


# What each requirement needs, for the settings UI's explanatory hint.
REQUIREMENT_LABELS = {
    "vision": "a vision provider",
    "hardcover": "a Hardcover token",
}


def hideable_tab_states(settings: dict[str, str] | None = None) -> list[dict]:
    """Each hideable tab with both inputs to its visibility.

    -> {"key", "label", "hidden", "available", "requirement_label"}

    `available` is the auto-hide input (integration configured or not);
    `hidden` is the manual-hide input (the settings checkbox). The two are
    independent — a tab can be checked visible but still unavailable, or
    manually hidden despite being fully configured. Callers combine them as
    needed; this just reports both so the UI can explain the difference.
    """
    if settings is None:
        settings = _nav_settings()
    hidden = hidden_keys(settings)
    states = []
    for tab in HIDEABLE_TABS:
        requires = tab.get("requires")
        available = _is_configured(requires, settings) if requires else True
        states.append({
            "key": tab["key"],
            "label": tab["label"],
            "hidden": tab["key"] in hidden,
            "available": available,
            "requirement_label": REQUIREMENT_LABELS.get(requires, ""),
        })
    return states


def hidden_keys(settings: dict[str, str] | None = None) -> set[str]:
    """The manually hidden tab keys, tolerating absent or malformed values."""
    raw = (settings if settings is not None else _nav_settings()).get("nav_hidden_tabs")
    if not raw:
        return set()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("nav_hidden_tabs is not valid JSON; ignoring it")
        return set()
    if not isinstance(parsed, list):
        return set()
    return {k for k in parsed if isinstance(k, str) and k in HIDEABLE_KEYS}


def visible_tabs(user: dict | None) -> list[dict]:
    """The tabs this user should see: role AND integration AND not hidden."""
    settings = _nav_settings()
    hidden = hidden_keys(settings)
    role = (user or {}).get("role")
    tabs = []
    for tab in NAV_TABS:
        roles = tab.get("roles")
        if roles and role not in roles:
            continue
        requires = tab.get("requires")
        if requires and not _is_configured(requires, settings):
            continue
        if tab["key"] in hidden:
            continue
        tabs.append({"key": tab["key"], "label": tab["label"], "path": tab["path"]})
    return tabs


# Where the item-detail "back" link goes when arriving from somewhere other
# than Browse. This is a whitelist, not an echo: `back_target` is the only
# way a `?from=` value may influence emitted HTML, and any key not listed
# here — including anything crafted to look like a URL or path — silently
# degrades to the default with no `from=` param emitted at all.
BACK_TARGETS = {
    "series": ("/series", "Back to series"),
    "stats": ("/stats", "Back to stats"),
}
DEFAULT_BACK_TARGET = ("/browse", "Back to collection")


def back_target(key: str | None) -> dict:
    """Resolve a `?from=` value to a safe back-navigation target.

    Returns `{"key": ..., "path": ..., "label": ...}`. A valid key is
    normalized and echoed back so templates can propagate it further; any
    other value (absent, unknown, or hostile — `//evil.com`,
    `javascript:alert(1)`, `../../etc`, etc.) resolves to the `/browse`
    default with `key` set to `""`, so callers never render it into a URL.
    """
    if isinstance(key, str) and key in BACK_TARGETS:
        path, label = BACK_TARGETS[key]
        return {"key": key, "path": path, "label": label}
    path, label = DEFAULT_BACK_TARGET
    return {"key": "", "path": path, "label": label}
