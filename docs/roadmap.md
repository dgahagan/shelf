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
  Nationalbibliothek, Italian ones to Italy's national network, Dutch ones to
  the Koninklijke Bibliotheek, Hungarian ones to OSZK NEKTÁR and Portuguese
  ones to PORBASE. Other
  countries deserve the same.
- **A translated interface.** In progress. The translation machinery and the
  first catalog have landed, and each user can pick a language or follow the
  browser. Other languages come next. The page shell (navigation, sign-in,
  setup, the Account dialog) is translated; the feature pages follow area by
  area.

## Import and migration

**Planned.** Goodreads, StoryGraph, LibraryThing, Libib, CSV and a portable
archive are supported today. Next is an importer for an **Audible/Libation
library export**, so an audiobook collection can be catalogued without retyping
it. Shelf stores the catalogue record, never the audio.

## Reading life

**Planned.** Ratings, did-not-finish and paused states, re-reads, a reading
journal, yearly goals and an Up Next list. This needs per-user state to mean
anything, so it follows the households work.

## Alerts and discovery

**Exploring.**

- **New-release alerts for authors** you follow, beyond the series you watch
  (series watching and the Upcoming releases page shipped in 0.73.0).
- **Buy links** on wishlist and series-gap rows — off by default, with
  Bookshop.org first because it supports independent bookshops. If you turn them
  on you enter *your own* affiliate tags, not the project's, and the link says
  what it is. Shelf takes nothing from it.

---

## Recently shipped

The last five releases that changed something you can see. Some releases change
only security or foundations — 0.50.1 and 0.51.0 hardened sign-in and fixed a
security issue without changing what you see — and those are left out here
rather than listed as "nothing visible". Full detail on every release, visible
or not, is in the [changelog](../CHANGELOG.md).

| Version | What landed |
|---|---|
| [0.74.0](https://github.com/dgahagan/shelf/releases/tag/v0.74.0) | **API tokens for dashboards.** Make a read-only token in your account menu and point Homepage, Homarr or Home Assistant at `/api/v1/stats` for item counts, loans, value and upcoming releases. Tokens never act above viewer; admins can revoke anyone's from Settings → Users. |
| [0.73.0](https://github.com/dgahagan/shelf/releases/tag/v0.73.0) | **Release radar.** Watch a series — or every complete one at once — and Shelf checks Hardcover daily for newly announced volumes. An Upcoming releases page, linked from Series, lists them soonest first with + Wishlist, Dismiss and Stop watching, and a daily digest goes to the notification URL you already use for loans. |
| [0.72.0](https://github.com/dgahagan/shelf/releases/tag/v0.72.0) | **Duplicates.** Admins get a page of records filed twice — by ISBN or barcode, or by title and author for records with no identifier — side by side. Pick the one to keep and merge: every physical copy, tag, loan and audit entry comes across, so two records of one book become one record with two copies. **Not duplicates** hides a group, and Restore brings it back. Part of Collector tools. |
| [0.71.0](https://github.com/dgahagan/shelf/releases/tag/v0.71.0) | **Shelf audits.** Start an audit of a shelf or the whole library from Scan → Inventory, scan from any device (several at once, or after a reload), and finish for a report: found, moved here, seen elsewhere, on loan, missing and unexpected. Mark the missing copies; a Found button or the next scan of that copy clears the mark. Part of Collector tools. |
| [0.70.0](https://github.com/dgahagan/shelf/releases/tag/v0.70.0) | **Copy labels.** Give each physical copy its own barcode from the new Labels page or a copy's edit panel, and print the codes on Avery 5160 or L7160 sheets. Scanning a label acts on that exact copy: Inventory and Move act on that copy alone, Lookup names it, and Add reports the copy you already own. Part of the new Collector tools feature. |

---

## Suggesting something

Open a [feature request](https://github.com/dgahagan/shelf/issues/new?template=feature_request.yml).
Requests are read and answered, and the ones that fit get folded into a group
above — several already have. Saying what problem you are trying to solve helps
more than proposing a solution, because the fix that lands is often not the one
first suggested.

This page is not a voting board and requests are not ranked by how many people
ask. Shelf is one person's project, built for a real collection.
