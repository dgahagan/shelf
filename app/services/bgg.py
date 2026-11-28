"""BoardGameGeek XML API2 client for board game metadata (issue #167).

Two calls: `search_games` (title -> candidates, with thumbnails) and
`lookup_game` (BGG id -> one metadata dict). Both return a `ProviderResult`
(`provider="bgg"`) so a rejected token, a spent quota and a dead socket keep
their own outcome instead of collapsing into "no such game".

BGG requires a registered application token on every XML API request since
autumn 2025 (https://boardgamegeek.com/applications). It is sent as a Bearer
header; without one BGG answers 401, which this module reports as
`no_credential` before ever making the request.

Requests are paced through `outbound.py` (see `HOST_RATE_LIMITS`), and every
consumer must show "Powered by BGG" next to BGG data — the templates carry
that, not this module.

The XML is parsed with the standard library: `xml.etree.ElementTree` never
resolves external entities, and the libexpat Python ships with (2.4.1 and
later) has billion-laughs protection built in.
"""

import html
import logging
import os
import re
import xml.etree.ElementTree as ET

import httpx

from app.services import outbound, provider_result

logger = logging.getLogger(__name__)

PROVIDER = "bgg"
BGG_API_URL_DEFAULT = "https://boardgamegeek.com/xmlapi2"

# A thing request takes up to 20 ids; the search card shows at most this many.
MAX_THING_IDS = 20

# 401/403 mean BGG refused the token. Read before rate limiting, as everywhere.
_AUTH_STATUSES = (401, 403)

# What a board game page links to, and the metadata key each lands in.
_LINK_KEYS = {
    "boardgamedesigner": "designers",
    "boardgamepublisher": "publishers",
    "boardgamecategory": "categories",
    "boardgamemechanic": "mechanics",
}


#: Shelf locale -> BGG's name for the language a version is printed in.
BGG_LANGUAGES = {"de": "German", "en": "English", "it": "Italian", "zh_Hans": "Chinese"}


def versions_in_language(versions: list[dict], locale: str) -> list[dict]:
    """`versions` with the ones printed in `locale`'s language first, then
    the rest. The library-language group is newest first (the box most likely
    on the shelf, and a classic can have fifteen German printings); the rest
    keep BGG's order. Versions without box art are dropped: they are no use
    to a cover picker."""
    wanted = BGG_LANGUAGES.get(locale)
    with_art = [v for v in versions if v.get("cover_url")]
    first = sorted((v for v in with_art if wanted and wanted in v.get("languages", [])),
                   key=lambda v: -(v.get("publish_year") or 0))
    return first + [v for v in with_art if v not in first]


def wants_versions(locale: str) -> bool:
    """Whether filing a game in `locale` needs BGG's version list: only to
    find the title printed on a box in a language other than English."""
    return BGG_LANGUAGES.get(locale) not in (None, "English")


def localized_version(meta: dict, locale: str) -> dict | None:
    """The printing whose title a library in `locale` files the game under,
    or `None` (English, an unmapped language, or no printing in it).

    The title most of that language's printings carry wins, so a one-off
    "Codenames XXL" or "Azul Mini" does not rename the game; a tie goes to
    the newer title. Of the printings with the winning title, the newest is
    returned (its box art is the likeliest on the shelf).
    """
    if not wants_versions(locale):
        return None
    wanted = BGG_LANGUAGES[locale]
    printed = [v for v in meta.get("versions") or []
               if wanted in v.get("languages", []) and (v.get("title") or "").strip()]
    if not printed:
        return None
    count: dict[str, int] = {}
    newest: dict[str, dict] = {}
    for v in printed:
        title = v["title"].strip()
        count[title] = count.get(title, 0) + 1
        year = v.get("publish_year") or 0
        if title not in newest or year >= (newest[title].get("publish_year") or 0):
            newest[title] = v
    best = max(count, key=lambda t: (count[t], newest[t].get("publish_year") or 0))
    return newest[best]


def localize(meta: dict, locale: str) -> dict:
    """`meta` with the title (and box art, when it has its own) of the
    printing `localized_version` picks. BGG's own name is kept as
    `original_title` (stored, and matched by Browse search) and leads
    `alternate_titles`, so nothing is lost. Unchanged when there is none."""
    version = localized_version(meta, locale)
    if version is None:
        return meta
    title = version["title"].strip()
    out = {**meta, "title": title}
    original = meta.get("title")
    if original and original.casefold() != title.casefold():
        out["original_title"] = original
        out["alternate_titles"] = [original] + [t for t in meta.get("alternate_titles") or []
                                                if t.casefold() != title.casefold()]
    if version.get("cover_url"):
        out["cover_url"] = version["cover_url"]
        out["thumbnail_url"] = version.get("thumbnail_url") or version["cover_url"]
    return out


def api_url() -> str:
    """The XML API2 root: `SHELF_BGG_API_URL` (test stubs only), else BGG itself."""
    return (os.environ.get("SHELF_BGG_API_URL") or BGG_API_URL_DEFAULT).rstrip("/")


def page_url(bgg_id: int | str) -> str:
    """The public BGG page for a game, for the attribution link."""
    return f"https://boardgamegeek.com/boardgame/{bgg_id}"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _int_value(item: ET.Element, tag: str) -> int | None:
    """`<tag value="3"/>` -> 3; missing, empty or 0 -> None (BGG writes 0 for unknown)."""
    el = item.find(tag)
    if el is None:
        return None
    try:
        value = int(el.get("value") or "")
    except ValueError:
        return None
    return value or None


def _clean_description(raw: str | None) -> str | None:
    """BGG descriptions arrive entity-escaped with `&#10;` line breaks."""
    if not raw:
        return None
    text = html.unescape(raw)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _primary_name(item: ET.Element) -> str | None:
    for name in item.findall("name"):
        if name.get("type") == "primary":
            return name.get("value")
    first = item.find("name")
    return first.get("value") if first is not None else None


def parse_thing(item: ET.Element) -> dict:
    """One `<item>` of a thing response -> Shelf's metadata dict.

    Keys: bgg_id, title, alternate_titles, kind ("boardgame" or
    "boardgameexpansion"), publish_year, description, min_players,
    max_players, play_time_mins, min_age, designers, publishers, categories,
    mechanics, base_game_ids (for an expansion), cover_url, thumbnail_url.
    The `authors` and `publisher` keys mirror the first designers/publisher in
    the shape the rest of Shelf reads.
    """
    meta: dict = {
        "bgg_id": int(item.get("id")),
        "kind": item.get("type") or "boardgame",
        "title": _primary_name(item),
        "alternate_titles": [
            n.get("value") for n in item.findall("name")
            if n.get("type") == "alternate" and n.get("value")
        ],
        "publish_year": _int_value(item, "yearpublished"),
        "description": _clean_description(item.findtext("description")),
        "min_players": _int_value(item, "minplayers"),
        "max_players": _int_value(item, "maxplayers"),
        "play_time_mins": _int_value(item, "playingtime") or _int_value(item, "maxplaytime"),
        "min_age": _int_value(item, "minage"),
        "cover_url": (item.findtext("image") or "").strip() or None,
        "thumbnail_url": (item.findtext("thumbnail") or "").strip() or None,
        "base_game_ids": [],
    }
    for key in _LINK_KEYS.values():
        meta[key] = []
    for link in item.findall("link"):
        link_type = link.get("type")
        if link_type in _LINK_KEYS and link.get("value"):
            meta[_LINK_KEYS[link_type]].append(link.get("value"))
        # On an expansion, the inbound boardgameexpansion link names the base game.
        if link_type == "boardgameexpansion" and link.get("inbound") == "true":
            try:
                meta["base_game_ids"].append(int(link.get("id")))
            except (TypeError, ValueError):
                pass
    versions = item.find("versions")
    meta["versions"] = [] if versions is None else [
        parse_version(v) for v in versions.findall("item") if v.get("id")
    ]
    # "(Uncredited)" is BGG's placeholder, not a person.
    meta["designers"] = [d for d in meta["designers"] if d != "(Uncredited)"]
    meta["authors"] = ", ".join(meta["designers"][:3]) or None
    meta["publisher"] = meta["publishers"][0] if meta["publishers"] else None
    return meta


def parse_version(item: ET.Element) -> dict:
    """One `<item type="boardgameversion">` (asked for with `versions=1`).

    A version is one printing: its own box art, its own title in its own
    language ("Die Siedler von Catan"), its publisher and year. Keys:
    version_id, name (BGG's label, e.g. "German edition 2022"), title (the
    printed name), languages, publisher, publish_year, cover_url,
    thumbnail_url.
    """
    canonical = item.find("canonicalname")
    publishers = [l.get("value") for l in item.findall("link")
                  if l.get("type") == "boardgamepublisher" and l.get("value")]
    return {
        "version_id": int(item.get("id")),
        "name": _primary_name(item),
        "title": canonical.get("value") if canonical is not None else None,
        "languages": [l.get("value") for l in item.findall("link")
                      if l.get("type") == "language" and l.get("value")],
        "publisher": publishers[0] if publishers else None,
        "publish_year": _int_value(item, "yearpublished"),
        "cover_url": (item.findtext("image") or "").strip() or None,
        "thumbnail_url": (item.findtext("thumbnail") or "").strip() or None,
    }


def parse_things(xml_text: str) -> list[dict]:
    """A whole thing response -> list of metadata dicts, in BGG's order."""
    root = ET.fromstring(xml_text)
    return [parse_thing(item) for item in root.findall("item") if item.get("id")]


def parse_search(xml_text: str) -> list[dict]:
    """A search response -> [{bgg_id, title, publish_year, kind}], de-duplicated by id."""
    root = ET.fromstring(xml_text)
    seen: set[int] = set()
    results = []
    for item in root.findall("item"):
        try:
            bgg_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if bgg_id in seen:
            continue
        seen.add(bgg_id)
        results.append({
            "bgg_id": bgg_id,
            "title": _primary_name(item),
            "publish_year": _int_value(item, "yearpublished"),
            "kind": item.get("type") or "boardgame",
        })
    return results


def _classify(resp: httpx.Response) -> provider_result.ProviderResult | None:
    classified = provider_result.classify_response(PROVIDER, resp, auth_statuses=_AUTH_STATUSES)
    if classified is not None and classified.outcome == "rejected":
        logger.warning("BGG rejected the configured API token (HTTP %d)", resp.status_code)
    return classified


async def fetch_things(
    ids: list[int], token: str | None, client: httpx.AsyncClient, *, versions: bool = False,
) -> provider_result.ProviderResult:
    """Fetch up to `MAX_THING_IDS` games in one request. Payload: list of dicts.

    `versions=True` also lists every printing (box art per edition and
    language); a popular game has a hundred or more, so only ask for one id.
    """
    if not token:
        return provider_result.no_credential(PROVIDER)
    ids = [int(i) for i in ids][:MAX_THING_IDS]
    if not ids:
        return provider_result.no_match(PROVIDER)
    try:
        resp = await outbound.fetch(
            client, "GET", f"{api_url()}/thing",
            params={"id": ",".join(str(i) for i in ids), "stats": "1",
                    **({"versions": "1"} if versions else {})},
            headers=_headers(token), timeout=15,
        )
    except Exception:
        logger.debug("BGG thing request failed", exc_info=True)
        return provider_result.transport_failed(PROVIDER)
    classified = _classify(resp)
    if classified is not None:
        return classified
    try:
        things = parse_things(resp.text)
    except ET.ParseError:
        logger.debug("BGG thing response did not parse", exc_info=True)
        return provider_result.transport_failed(PROVIDER)
    if not things:
        return provider_result.no_match(PROVIDER, status=resp.status_code)
    return provider_result.found(PROVIDER, things, status=resp.status_code)


async def lookup_game(
    bgg_id: int, token: str | None, client: httpx.AsyncClient, *, versions: bool = False,
) -> provider_result.ProviderResult:
    """One game by BGG id. Payload: the metadata dict (not a list)."""
    result = await fetch_things([bgg_id], token, client, versions=versions)
    if not result.found:
        return result
    return provider_result.found(PROVIDER, result.payload[0], status=result.status)


async def search_games(
    query: str, token: str | None, client: httpx.AsyncClient, *, limit: int = 10,
) -> provider_result.ProviderResult:
    """Search BGG by title, base games and expansions alike.

    Payload: a list of metadata dicts (the full thing record for each hit, so
    the result card can show a cover, player count and year). Exact title
    matches come first, then BGG's own order.
    """
    if not token:
        return provider_result.no_credential(PROVIDER)
    query = query.strip()
    if not query:
        return provider_result.no_match(PROVIDER)
    try:
        resp = await outbound.fetch(
            client, "GET", f"{api_url()}/search",
            params={"query": query, "type": "boardgame,boardgameexpansion"},
            headers=_headers(token), timeout=15,
        )
    except Exception:
        logger.debug("BGG search request failed", exc_info=True)
        return provider_result.transport_failed(PROVIDER)
    classified = _classify(resp)
    if classified is not None:
        return classified
    try:
        hits = parse_search(resp.text)
    except ET.ParseError:
        logger.debug("BGG search response did not parse", exc_info=True)
        return provider_result.transport_failed(PROVIDER)
    if not hits:
        return provider_result.no_match(PROVIDER, status=resp.status_code)

    wanted = query.casefold()
    hits.sort(key=lambda h: (h["title"] or "").casefold() != wanted)
    hits = hits[:max(1, min(limit, MAX_THING_IDS))]

    things = await fetch_things([h["bgg_id"] for h in hits], token, client)
    if not things.found:
        # The search itself answered; keep its bare hits rather than lose them.
        if things.outcome in ("rejected", "rate_limited"):
            return things
        return provider_result.found(PROVIDER, hits, status=resp.status_code)
    by_id = {t["bgg_id"]: t for t in things.payload}
    return provider_result.found(
        PROVIDER, [by_id.get(h["bgg_id"], h) for h in hits], status=resp.status_code,
    )


async def test_token(token: str, client: httpx.AsyncClient) -> dict:
    """Settings "Test" button: one cheap thing request (Catan, id 13)."""
    if not token:
        return {"ok": False, "message": "No BGG token configured"}
    result = await lookup_game(13, token, client)
    if result.found:
        return {"ok": True, "message": "BGG token works"}
    messages = {
        "rejected": "BGG rejected the token",
        "rate_limited": "BGG is rate-limiting requests — try again in a minute",
        "transport_failed": "Could not reach BoardGameGeek",
    }
    return {"ok": False, "message": messages.get(result.outcome, "BGG returned no data")}
