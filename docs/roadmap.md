# Roadmap

What Shelf is likely to grow next, grouped by theme.

**This is direction, not a schedule.** There are no dates and no order here. Items
move, merge and get dropped as the project learns things — a group appearing on
this page is not a commitment that it ships, and its absence is not a refusal.
For what actually shipped, read the [changelog](../CHANGELOG.md).

Some groups say what they wait on. Where that appears it is a technical fact —
the work genuinely reuses something built earlier — not a queue position.

**Status words:** *Planned* means the shape is settled and it is a matter of
building it. *Exploring* means the idea is accepted but the shape is not.

---

## Choose your features

**Planned.** Shelf has grown a lot of surface, and not everyone wants all of it.
A setup step and a settings page to turn whole feature areas on and off, so an
install that only catalogues books does not carry lending, valuation, store mode
and the rest in its navigation. The settings page shipped in 0.55.0 as
Settings → **Features**, and the setup wizard now asks for a profile —
Minimal, Standard or Everything — which Features can change later.

Most of the groups below arrive switched off behind this, which is why it comes
early.

**A navigation rework goes with it.** The tab bar has grown one tab at a time
and is now long enough that the next feature makes it worse. The plan is to
group the tabs by what you are actually doing — the views onto your collection
in one place, the scanner-and-camera tools in another — rather than keep adding
to a single row. Turning feature areas off and grouping what is left are two
halves of the same problem, so they are being designed together.

## Homelab integration

**Planned.** Shelf should behave like the rest of your stack. The first part shipped in 0.74.0.

- **API tokens and a dashboard document — shipped in 0.74.0.** Read-only
  tokens you make from your account menu, `GET /api/v1/stats`, and tested
  recipes for Homepage and Home Assistant. See
  [Homelab integration](homelab-integration.md).
- **The rest of the read API** — items, loans, wishlist, series and upcoming
  releases under the same tokens, with a published schema. A stable
  read-mostly surface that will not move under you.
- **SSO via OIDC** ([#89](https://github.com/dgahagan/shelf/issues/89)) — sign in
  with the identity provider you already run instead of Shelf keeping its own
  user list. This one builds on the account rework in *Multi-user & households*,
  so it follows that work.

If what you actually need is your reverse proxy handling the login (Authelia,
Authentik and friends), **trusted proxy-header auth** is part of the households
work below and arrives well before full OIDC.

## Multi-user & households

**Planned.** [#48](https://github.com/dgahagan/shelf/issues/48) — the largest
thing on this page, and it will arrive over several releases rather than one.

Today every account shares one library. The goal is that one Shelf install can
host several households — family, friends — each with its own complete library,
and each able to share it with chosen people at chosen access levels. Individual
items and whole locations can be marked private so they never appear to an
outside viewer. On top of that: borrow requests with a lending ledger, wishlists
visible for gift-buying with secret claims, and "who owns this?" search across
the households you can see.

Existing installs upgrade without noticing. Your data becomes the first
household, and nothing visibly changes until you invite someone.

## More sources and languages

**Planned.** Shelf's metadata is good if your books are English and in Open
Library, and thinner otherwise.

- **More national sources.** German ISBNs already route to the Deutsche
  Nationalbibliothek, French ones to the BnF, Italian ones to Italy's national
  network, Dutch ones to
  the Koninklijke Bibliotheek, Hungarian ones to OSZK NEKTÁR and Portuguese
  ones to PORBASE, and Chinese-language ones to NeoDB. Other
  countries deserve the same.
- **A translated interface.** In progress. The translation machinery and the
  first catalog have landed, and each user can pick a language or follow the
  browser. Other languages come next. The page shell (navigation, sign-in,
  setup, the Account dialog), Browse, the item page, item edit, manual add and Scan
  are translated; the other feature pages
  follow area by area. Until then it sits behind `SHELF_I18N`, off by default.

## Import and migration

**In progress.** Goodreads, StoryGraph, LibraryThing, Libib, CSV, a portable
archive and an **Audible library export** (audible-cli or Libation, since
0.76.0) are supported today. Next is looking up an audiobook's details and cover
by its ASIN, so an imported title fills in what the export leaves out. Shelf
stores the catalogue record, never the audio.

## Reading life

**Planned.** Ratings, did-not-finish and paused states, re-reads, a reading
journal, yearly goals and an Up Next list. This needs per-user state to mean
anything, so it follows the households work.

## Alerts and discovery

**Exploring.**

- **New-release alerts for authors** you follow, beyond the series you watch
  (series watching and the Upcoming releases page shipped in 0.73.0).

---

## Recently shipped

The last five releases that changed something you can see. Some releases change
only security or foundations — 0.50.1 and 0.51.0 hardened sign-in and fixed a
security issue without changing what you see — and those are left out here
rather than listed as "nothing visible". Full detail on every release, visible
or not, is in the [changelog](../CHANGELOG.md).

| Version | What landed |
|---|---|
| [0.78.3](https://github.com/dgahagan/shelf/releases/tag/v0.78.3) | **Keyboard-shortcut help in every language.** In Italian or Chinese, the **?** button now opens the shortcut list, and its close button and backdrop close it. The list opens centred and fits a phone screen. Shortcuts also stop firing while Ctrl, Cmd or Alt is held, so Ctrl+F and the browser's other shortcuts work again. |
| [0.78.1](https://github.com/dgahagan/shelf/releases/tag/v0.78.1) | **The valuation report counts a value of 0.** An item you valued at 0 now counts as priced at $0.00 instead of being filled in with the collection's average price, so the report's totals are no longer too high. |
| [0.78.0](https://github.com/dgahagan/shelf/releases/tag/v0.78.0) | **Browse finds items with no location.** The Location filter lists **No location** with a count, so items that were never filed can be found and moved in bulk, contributed by @larsvilhuber. Also: re-adding a video game with no platform reports it as already in your collection instead of adding a second copy, and the item edit page and manual add in Italian, contributed by @vjandrea. |
| [0.77.0](https://github.com/dgahagan/shelf/releases/tag/v0.77.0) | **Store Mode trusts the sync.** A wishlisted book no longer shows as Owned after a sync, the verdict card updates to Shelf's answer as soon as an online scan syncs, an ISBN-10 leaves the queue, and a long queue syncs in one go. Also Chinese ISBNs looked up in NeoDB and Browse in Simplified Chinese, contributed by @litong5969, and the item page in Italian with regional and script language variants, contributed by @vjandrea. |
| [0.76.0](https://github.com/dgahagan/shelf/releases/tag/v0.76.0) | **Audible library import.** Upload an export from audible-cli or Libation under Settings → Data, and each title becomes an audiobook with its narrator, length and ASIN; re-imports add nothing twice, an audiobook already synced from Audiobookshelf is matched instead of duplicated, and the print copy you own shows **Also available as: Audiobook**. Also French ISBNs looked up in the BnF, contributed by @larsvilhuber, and the page shell in Simplified Chinese, contributed by @litong5969. |
---

## Suggesting something

Open a [feature request](https://github.com/dgahagan/shelf/issues/new?template=feature_request.yml).
Requests are read and answered, and the ones that fit get folded into a group
above — several already have. Saying what problem you are trying to solve helps
more than proposing a solution, because the fix that lands is often not the one
first suggested.

This page is not a voting board and requests are not ranked by how many people
ask. Shelf is one person's project, built for a real collection.
