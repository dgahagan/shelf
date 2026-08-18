# Changelog

All notable changes to Shelf are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.1] - 2026-08-18

Bugfix release for two [@LegendaryB](https://github.com/LegendaryB) reports:
[#13](https://github.com/dgahagan/shelf/issues/13) and
[#14](https://github.com/dgahagan/shelf/issues/14).

### Fixed

- **Sort preference is applied, not just displayed, in a new tab**
  ([#13](https://github.com/dgahagan/shelf/issues/13)). Browse keeps your
  filters in `sessionStorage` (per-tab) and your sort in `localStorage`
  (persistent), so opening Shelf in a fresh tab hit a fallback path that set
  the sort dropdown's value but fired its request with `htmx.trigger` — which
  is unreliable during init, because htmx wires its listeners on
  `DOMContentLoaded` and can miss a synthetic event dispatched by Alpine's
  deferred setup. The dropdown read "Title A–Z" while the rows stayed in the
  server's default newest-first order. That fallback now takes the same
  `htmx.ajax` route the filter restore already used, and carries the current
  view so a restored sort can't turn a list back into grid cards.
- **List view button no longer clipped** in the Browse view toggle
  ([#14](https://github.com/dgahagan/shelf/issues/14)). The toggle needs
  `overflow-hidden` for its rounded corners, but as a flex item it was also
  shrinking below its content width, so the right-hand button was cut off at
  every desktop width. It no longer shrinks.

## [0.4.0] - 2026-08-17

### Added

- **Rename, merge, and disband series from the Series page** — each series card
  now has a `⋮` menu. *Rename series…* moves every book in the series to a new
  name; typing the name of a series you already have merges the two, which is
  the quick fix for duplicate series records (three "Dune", three "Hyperion
  Cantos") that metadata lookup can leave behind. A merge deliberately **keeps
  each book's existing position** rather than renumbering — two books can
  legitimately land on #1, and the existing gap detection surfaces the result
  on the merged card. The series synopsis follows the rename; on a merge the
  destination's synopsis is kept if it has one, otherwise the other series'
  synopsis moves across. *Remove all books…* disbands a series behind an inline
  confirm: the books stay in your library, they just stop belonging to that
  series, and the now-unused synopsis is cleaned up.

## [0.3.0] - 2026-08-16

First release driven by community bug reports — thanks to
[@LegendaryB](https://github.com/LegendaryB) for issues
[#5](https://github.com/dgahagan/shelf/issues/5)–[#9](https://github.com/dgahagan/shelf/issues/9)
and [@emre155](https://github.com/emre155) for
[#10](https://github.com/dgahagan/shelf/pull/10).

### Added

- **Bulk-edit series** — set or clear the series on many items at once from the
  Browse bulk action bar, with autocomplete over series you already own
  ([#5](https://github.com/dgahagan/shelf/issues/5)).
- **Series synopses** — each series on the Series page can carry its own
  description, edited inline. With Hardcover configured, "Fetch synopsis"
  pulls it automatically. Metadata is stored per series and cleaned up when a
  series stops being referenced
  ([#6](https://github.com/dgahagan/shelf/issues/6)).
  Note that Hardcover populates series descriptions sparsely — most series
  there have none, in which case Shelf says so and opens the editor so you can
  write your own. Where several Hardcover records share a series name, all of
  them are checked for a description, not just the first.

### Fixed

- **Infinite scroll never loaded a second page** — in *either* view. Both
  layouts render inside an Alpine `<template x-if>`, whose content Alpine
  clones into the DOM at runtime; htmx doesn't watch for that, so the
  load-more sentinel was never wired up and scrolling past the first 60 items
  did nothing. Browse now hands newly rendered content to htmx explicitly
  ([#7](https://github.com/dgahagan/shelf/issues/7)).
- **List view turned into cover cards while scrolling** — pagination didn't
  carry the current view mode, so page 2 came back as grid cards and appended
  them into the table ([#7](https://github.com/dgahagan/shelf/issues/7)).
- **Filters were shown but not applied after leaving and returning to Browse**
  — filter state now survives a trip to another page and is re-applied on
  return, not just repainted into the controls
  ([#8](https://github.com/dgahagan/shelf/issues/8)).
- **The tag filter was silently dropped** by every filter change after the
  first search ([#8](https://github.com/dgahagan/shelf/issues/8)).
- **Search was wiped by any other filter change on narrow screens** — the
  mobile and desktop search boxes share a name, so changing another filter
  submitted both and the empty one won
  ([#8](https://github.com/dgahagan/shelf/issues/8)).
- **Middle-click and ctrl/cmd-click now open items in a new tab**, in both
  grid and list view; item titles are real links again. Based on
  [#10](https://github.com/dgahagan/shelf/pull/10) by
  [@emre155](https://github.com/emre155), reimplemented for the Alpine CSP
  build, which cannot evaluate the `window.open` call the original patch used
  ([#9](https://github.com/dgahagan/shelf/issues/9)).

## [0.2.0] - 2026-08-12

### Added

- **Photo Intake — OpenAI-compatible backend** — a third vision provider that
  targets any OpenAI Chat Completions endpoint (OpenAI, OpenRouter, or a local
  server such as vLLM / LM Studio / LocalAI) via a configurable base URL, API
  key, and model. Reuses the existing tiling and dedup pipeline.
- **Photo Intake — location picker** — pick the destination location right at
  the upload step; the last-used location is remembered for next time.

### Fixed

- **Add User was silently broken** — the Alpine CSP build cannot evaluate the
  nested-path assignment `x-model="newUser.username"` needs, so the form
  submitted empty fields no matter what was typed. Found, diagnosed, and fixed
  by @exactmike ([#2](https://github.com/dgahagan/shelf/issues/2),
  [#3](https://github.com/dgahagan/shelf/pull/3)).
- **The same silent-write bug in three more places** — Audiobookshelf library
  selection checkboxes, Hardcover import status filters, and title/author
  edits in the Photo Intake review step all silently discarded input for the
  same reason. All rebound CSP-safely.
- **User-management errors are now visible** — CSRF/auth rejections returned
  non-JSON bodies that crashed the response handling, so Add User, role
  changes, password resets, and deletes failed with no feedback at all; they
  now show the actual error ([#3](https://github.com/dgahagan/shelf/pull/3)).
- The e2e test server no longer deadlocks when uvicorn's log output fills the
  OS pipe buffer ([#3](https://github.com/dgahagan/shelf/pull/3)).

### Changed

- The Alpine CSP lint now rejects any `x-model` bound to a nested or bracketed
  path, so this bug class can't reappear.
- Docker publish hardening: the built image is secret-scanned before push, and
  a build-context `.dockerignore` keeps local data out of the context.

## [0.1.0] - 2026-07-05

First public release.

### Added

- **Scanning** — camera barcode scanning (ISBN/UPC), USB/Bluetooth scanner
  support, and 8 scan modes: Add, Wishlist, Lend, Return, Move, Inventory,
  Lookup, Quick Rate
- **Photo Intake** — bulk-add books from a photo of your shelves via a vision
  model (Anthropic API or local Ollama), with high-res tiling, ingest-cap
  preview, and per-option cost estimates
- **Metadata pipeline** — cascading lookup across Open Library, Hardcover, and
  Google Books; cover art from Open Library, Hardcover, Amazon, Google Books,
  IGDB, or manual upload
- **Title search** — Open Library (books), TMDb (movies), IGDB (video games)
- **Video games** — UPC scanning and IGDB title search with a customizable
  platform list (Atari 2600 through PS5)
- **Collection management** — locations, custom tags, reading tracking,
  wishlist, series tracking with gap detection, stats dashboard, synopsis
  backfill
- **Lending** — Lend/Return scan modes, borrower tracking, overdue badges,
  optional daily digest (ntfy or webhook)
- **Store Mode** — offline PWA: instant owned/wishlist verdicts in-store with
  zero signal; unknown scans queue on-device and sync to your wishlist later
- **Import/export** — CSV both ways; Goodreads and StoryGraph exports imported
  as-is with auto-detection
- **Integrations** — Hardcover (bidirectional reading sync), Audiobookshelf
  (library sync + physical/digital linking), ISBNdb (valuation), TMDb, IGDB
- **Valuation report** — location-grouped, print-ready insurance report
- **Sharing** — revocable public read-only wishlist/collection links
- **Multi-user** — admin / editor / viewer roles
- **Security** — strict CSP (no `unsafe-inline`/`unsafe-eval`, no CDNs), CSRF
  protection, encrypted credential storage, optional passphrase-encrypted
  backups, HTTPS out of the box, non-root container

[0.4.1]: https://github.com/dgahagan/shelf/releases/tag/v0.4.1
[0.4.0]: https://github.com/dgahagan/shelf/releases/tag/v0.4.0
[0.3.0]: https://github.com/dgahagan/shelf/releases/tag/v0.3.0
[0.2.0]: https://github.com/dgahagan/shelf/releases/tag/v0.2.0
[0.1.0]: https://github.com/dgahagan/shelf/releases/tag/v0.1.0
