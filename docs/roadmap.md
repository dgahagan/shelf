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
and the rest in its navigation.

Most of the groups below arrive switched off behind this, which is why it comes
early.

## Homelab integration

**Planned.** Shelf should behave like the rest of your stack.

- **API tokens and a documented JSON API.** Shelf is FastAPI, so a schema exists
  in principle, but every endpoint today wants a browser session cookie — no use
  from `curl`, a script, or a dashboard. A revocable token you paste into another
  tool, and a stable read-mostly surface that will not move under you.
- **Dashboard widgets** — Homepage and Homarr — and a **Home Assistant** recipe.
  Both are thin layers over the token-authed API, so they follow it directly.
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
  Nationalbibliothek and Italian ones to Italy's national network. Other
  countries deserve the same.
- **A translated interface.** The UI is English-only today. Translating the
  templates and letting the browser or a per-user setting pick the language.
- **Music** ([#81](https://github.com/dgahagan/shelf/issues/81)) — CDs and records
  can be catalogued now, but nothing looks them up, because Shelf has no music
  metadata provider. MusicBrainz and the Cover Art Archive would fix that.

## Import and migration

**Planned.** Goodreads, StoryGraph, CSV and a portable archive are supported
today. Adding **LibraryThing** and **Libib**, and an importer for an
**Audible/Libation library export** so an audiobook collection can be catalogued
without retyping it. Shelf stores the catalogue record, never the audio.

## Collectors and inventory

**Planned.** For collections where the individual copy matters, not just the
title: per-copy condition, edition and provenance fields, printable accession
labels, a reconciliation report for a shelf audit, and a duplicate audit across
the whole collection.

## Reading life

**Planned.** Ratings, did-not-finish and paused states, re-reads, a reading
journal, yearly goals and an Up Next list. This needs per-user state to mean
anything, so it follows the households work.

## Alerts and discovery

**Exploring.**

- **New-release alerts** for the series and authors you follow, plus a calendar
  of what is coming.
- **Price alerts** on wishlist items.
- **Buy links** on wishlist and series-gap rows — off by default, with
  Bookshop.org first because it supports independent bookshops. If you turn them
  on you enter *your own* affiliate tags, not the project's, and the link says
  what it is. Shelf takes nothing from it.

---

## Recently shipped

The last five releases. Full detail in the [changelog](../CHANGELOG.md).

| Version | What landed |
|---|---|
| [0.34.0](https://github.com/dgahagan/shelf/releases/tag/v0.34.0) | An optional browser URL for Audiobookshelf, so **Listen** and **Read** links work when Shelf reaches your server at a different address than your browser does |
| [0.33.1](https://github.com/dgahagan/shelf/releases/tag/v0.33.1) | Adding the same item twice at once reports the duplicate instead of filing it twice |
| [0.33.0](https://github.com/dgahagan/shelf/releases/tag/v0.33.0) | CSV import recognises a book it already holds whatever form the ISBN is written in, and refuses an import mode it does not know instead of overwriting |
| [0.32.0](https://github.com/dgahagan/shelf/releases/tag/v0.32.0) | Legacy Scholastic price-point book barcodes scan properly instead of filing whatever product shares the code |
| [0.31.0](https://github.com/dgahagan/shelf/releases/tag/v0.31.0) | Photo Intake looks up DVDs and video games, not just books |

---

## Suggesting something

Open a [feature request](https://github.com/dgahagan/shelf/issues/new?template=feature_request.yml).
Requests are read and answered, and the ones that fit get folded into a group
above — several already have. Saying what problem you are trying to solve helps
more than proposing a solution, because the fix that lands is often not the one
first suggested.

This page is not a voting board and requests are not ranked by how many people
ask. Shelf is one person's project, built for a real collection.
