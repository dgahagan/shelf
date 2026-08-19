# Changelog

All notable changes to Shelf are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.8.0] - 2026-08-19

Navigation, from [@LegendaryB](https://github.com/LegendaryB)'s
[#17](https://github.com/dgahagan/shelf/issues/17) — plus two navigation bugs
found alongside it, and a database-restore fix found while hardening the
test suite.

### Added

- **Tabs for integrations you haven't set up now hide themselves**
  ([#17](https://github.com/dgahagan/shelf/issues/17)). **Intake** disappears
  until a vision provider is configured, and **Discover** until a Hardcover
  token is saved. A tab that cannot do anything is a dead end, not a
  preference, so this needs no setting and is on for every install. Configure
  the integration and its tab returns on the next page load — no restart.

- **Choose which tabs show, in Settings → Navigation.** A checkbox per tab,
  instance-wide. **Browse** and **Settings** are deliberately not hideable —
  the page that controls visibility has to stay reachable.

  Hiding is presentation only. A hidden tab's URL still works, so bookmarks
  and shared links keep working, and roles still decide what a viewer or
  editor may reach — visibility settings never grant access, and never
  override the role rules.

  Tab *reordering*, also asked for in #17, is deliberately not here: an
  order-picker costs more than the preference is worth on a nine-item bar.
  Worth revisiting if a second person asks.

### Fixed

- **The nav bar no longer overflows the screen on phones and small windows.**
  With every tab visible the bar ran off the right edge at any width below
  about 920px, taking the whole page's horizontal scroll with it. Below
  1024px the tabs now collapse into a menu button, which closes on Escape or
  a click outside. Measured across 360–1920px: no horizontal overflow at any
  width.

- **Restoring a database backup actually restores it.** Restore replaced
  `shelf.db` with a plain file copy while the database's `-wal`/`-shm`
  sidecar files were still live, so SQLite replayed the stale write-ahead log
  over the newly restored file. The usual result was the *pre-restore* data
  coming straight back while the page reported success; the unlucky result
  was `database disk image is malformed`. Restore now copies through SQLite's
  own backup API, which takes the right locks and leaves the log consistent
  with the file it belongs to.

  The existing test could not have caught this: it looked for a marker row
  that was present in the live database whether or not the restore had done
  anything.

- **"Back to collection" goes back where you actually came from.** Opening an
  item from **Series** or from **Stats** and clicking back silently returned
  you to Browse. The link now names the page you arrived from, and keeps it
  across a hop between linked formats or a trip through the edit form.
  Following an item from Browse, a search, or a bookmark still goes to
  Browse.

## [0.7.1] - 2026-08-19

A barcode-filing fix, from [#20](https://github.com/dgahagan/shelf/issues/20).

### Fixed

- **Manually adding a barcode nothing resolves, then scanning it again, no
  longer returns a 500** ([#20](https://github.com/dgahagan/shelf/issues/20)).
  Scanning an unresolvable barcode offers a manual-add form. Scanning that
  same barcode afterwards offered the form *again* instead of reporting the
  item you had just added — and submitting it a second time returned an HTTP
  500 error page. Only discs and video games were affected; books were
  always safe.

  Underneath, a manual add stored the scanned code in the ISBN column, even
  when it was a UPC — the conversion that normalises an ISBN will happily
  zero-pad a 12-digit UPC into something ISBN-shaped. The UPC scan path
  looks for discs by their UPC, so it could never find the row it had just
  written. A UPC now goes where it belongs, which also means a later scan of
  a disc you genuinely own finally matches it instead of offering to add a
  duplicate.

  Two related repairs come with it. The same disc scanned as a 12-digit
  UPC-A and as a 13-digit EAN-13 used to produce two separate rows; both
  forms now resolve to one. And existing libraries are repaired on upgrade —
  mis-filed barcodes are moved to the right column automatically. Where a
  mis-filed row *and* a correctly-filed one already exist for the same disc,
  both are left in place for you to merge rather than one being discarded.

## [0.7.0] - 2026-08-19

Archive import stops being a leap of faith. Found while running 0.6.0's
importer against a real 665-item library.

### Added

- **Import preview — see exactly what an archive import will do before it
  does it.** Settings → Data → Portable archive is now two steps: **Preview
  import** reads the zip and reports what a merge would change without
  writing anything, and only then does an **Import N items** button appear.
  The preview names the numbers that matter — how many items are new, how
  many are already in your library, how many would be updated, plus the
  covers, series, reading-log entries and loans that ride along — and tells
  you **how** each duplicate was matched: exactly, on ISBN, or heuristically,
  on title and author.

  That last distinction is the reason this exists. Most real libraries are
  mostly ISBN-less — in the 665-item library this was built against, 74% of
  duplicate matches came from the fuzzy title/author path — so the majority
  of an import's decisions were guesses the user never saw before they were
  acted on, irreversibly. Now they're shown first.

  You can also switch parts of the import off: new items, updates to matched
  items, covers, reading log, loans, valuation history. Each is a single
  checkbox — there are deliberately no per-item checkboxes, which would mean
  665 decisions to restore one backup. Anything you leave out is reported
  back afterwards ("Reading log: 11 rows not imported"), so a deselection
  never looks like data that silently vanished.

  The plan you approve is the plan that runs. If the library changes between
  the preview and the confirm, the affected items are left alone rather than
  imported under a stale verdict, and counted as drifted in the report.

### Changed

- **Archive import no longer replaces existing cover art.** Previously,
  importing in *Update duplicates* mode overwrote the cover file of every
  matched item — so re-importing an old archive, or merging someone else's,
  silently destroyed hand-picked covers with no way to get them back. On the
  665-item library that was 630 cover files rewritten by a single import.

  An archive cover now installs only onto an item that has **no** cover.
  Replacing existing ones is an explicit opt-in — a "Replace existing covers"
  checkbox that appears only in update mode, and only when there is something
  to replace. This applies to the scriptable one-shot endpoint too:
  `POST /api/import/archive` keeps its request and response shape and gains
  an optional `replace_covers=true` form field, off by default. If you were
  relying on the old overwrite behavior, pass it.

## [0.6.0] - 2026-08-18

A portability release, from [@LegendaryB](https://github.com/LegendaryB)'s
[#16](https://github.com/dgahagan/shelf/issues/16).

### Added

- **Portable archive — export and import your whole library, covers included**
  ([#16](https://github.com/dgahagan/shelf/issues/16)). Settings → Data has a
  new **Portable archive** card. Export writes one zip — `library.json` plus
  every cover file you have — covering items, locations, tags, series (with
  synopses and completeness), reading log, borrowers, checkouts and valuation
  history. Import merges that zip back into any Shelf instance and installs
  the covers straight from the file, so a moved library never refetches a
  single image from Open Library or Amazon.

  This closes a real gap rather than adding a convenience. Shelf had two ways
  to get data out and neither did the job: CSV export is twelve columns and
  drops tags, notes, reading history and covers; a database backup is
  complete but is the *whole instance* — password hashes and encrypted API
  credentials included — and, because covers live on disk rather than in the
  `.db`, restoring one silently leaves you with a library of blank spines.
  The archive is the middle piece: your library, none of your credentials,
  and the cover art that until now no mechanism preserved at all.

  Import **merges**, it doesn't replace — a wholesale replace is what backup
  restore is for. Duplicates are matched on ISBN + media type (title + author
  for items with no ISBN) and you choose whether to skip them or let the
  archive refresh their metadata. Locations, tags, borrowers and series are
  matched by name regardless of case and never overwritten, so importing a
  friend's archive can't clobber a synopsis you wrote. Reading history and
  loans come across only for items the import actually creates, so
  re-importing the same file twice doesn't double your history.

  The archive is deliberately admin-only in both directions — it carries
  notes, borrower names and your full reading history, which CSV export does
  not. Uploaded archives are treated as hostile input regardless of who
  uploads them: entry paths are checked against an exact expected layout
  (no traversal, no absolute paths, no symlinks, no nested directories),
  sizes are enforced on the bytes actually decompressed rather than on the
  headers a zip bomb controls, and every cover is re-validated as an image
  before it lands on disk.

## [0.5.0] - 2026-08-18

Three feature requests from [@LegendaryB](https://github.com/LegendaryB)'s
second round of feedback:
[#15](https://github.com/dgahagan/shelf/issues/15),
[#18](https://github.com/dgahagan/shelf/issues/18) and
[#19](https://github.com/dgahagan/shelf/issues/19).

### Added

- **Set your own value on an item**
  ([#18](https://github.com/dgahagan/shelf/issues/18)). The item edit form has
  a **Value** field that overrides the ISBNdb estimate everywhere a value is
  shown — the Stats total, the item page, and the insurance valuation report,
  where overridden rows are marked *manual* so a reader can tell owner-declared
  figures from list prices. This matters most if you don't have an ISBNdb key:
  estimates were the only source of value in the app, so the value tile and the
  valuation report were simply empty for you. It also serves collectors whose
  signed or rare editions are worth nothing like list price. The manual value
  is stored separately from the estimate rather than replacing it, so a batch
  valuation run still refreshes the estimate underneath, and clearing your
  override falls straight back to it. CSV export carries both columns.
- **Copy fields from an existing book when adding manually**
  ([#19](https://github.com/dgahagan/shelf/issues/19)). Manual entry is where
  you land whenever metadata lookup misses — obscure, foreign, and small-press
  books — and entering a series one volume at a time meant retyping the same
  author, publisher and shelf every time. The manual-add form now has a
  **Copy from…** picker: start typing a title you already own, pick it, and the
  author, publisher, year, platform, series and location are filled in for you
  to edit before saving. The title is deliberately never copied. The form also
  gained series and location fields, so "same series, same shelf" is a single
  pick.
- **Mark a series complete, and see which ones are**
  ([#15](https://github.com/dgahagan/shelf/issues/15)). Series cards now carry
  a completeness badge, and the `⋮` menu has *Mark complete* / *Unmark
  complete*. Three signals, cheapest truth first: your manual override always
  wins, because Hardcover's series data is often sparse or wrong once novellas
  and omnibuses are involved; otherwise a stored Hardcover check result shows
  ✓ Complete or "N missing" with the date it was checked; otherwise the
  existing local gap detection stands. A series is never called complete on
  position numbers alone — owning #1–#4 of a seven-book series has no local
  gaps to find. **Check completeness** results are now saved rather than
  discarded on reload, and All / Complete / Incomplete chips filter the page.

### Changed

- **Hardcover check results are stored on the series**, so a check survives a
  reload. Marking, checking and renaming stay independent of each other: a
  rename or merge carries the completeness flag and the stored check across
  with the synopsis (on a merge the destination's own values win), and clearing
  a synopsis no longer discards them — the series record is dropped only once
  nothing is left on it.
- **`cryptography` bumped 48.0.1 → 50.0.0**, clearing three advisories
  (PYSEC-2026-3552/3553/3554) that were keeping the dependency audit red.

### Fixed

- **Upgrades no longer stall and print tracebacks while applying migrations.**
  Shelf logs each applied migration, and log records are also written to the
  database — but that write opened a second connection while the migration's
  own transaction was still open, so every migration waited out SQLite's
  five-second busy timeout and then failed with a stack trace. One migration
  made this a five-second pause; this release has five, which would have meant
  around half a minute of what looked like a failed upgrade. Migrations now log
  once their transaction has committed, so an upgrade is immediate — and the
  migration history actually reaches the Logs page instead of being dropped.

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

[0.8.0]: https://github.com/dgahagan/shelf/releases/tag/v0.8.0
[0.7.1]: https://github.com/dgahagan/shelf/releases/tag/v0.7.1
[0.7.0]: https://github.com/dgahagan/shelf/releases/tag/v0.7.0
[0.6.0]: https://github.com/dgahagan/shelf/releases/tag/v0.6.0
[0.5.0]: https://github.com/dgahagan/shelf/releases/tag/v0.5.0
[0.4.1]: https://github.com/dgahagan/shelf/releases/tag/v0.4.1
[0.4.0]: https://github.com/dgahagan/shelf/releases/tag/v0.4.0
[0.3.0]: https://github.com/dgahagan/shelf/releases/tag/v0.3.0
[0.2.0]: https://github.com/dgahagan/shelf/releases/tag/v0.2.0
[0.1.0]: https://github.com/dgahagan/shelf/releases/tag/v0.1.0
