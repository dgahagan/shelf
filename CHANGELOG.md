# Changelog

All notable changes to Shelf are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.13.0] - 2026-08-22

A book with no series assigned was unreachable. The Series page filtered those
items out of existence, and Browse has no series filter of any kind — so the
only way to find them was to already know their titles. They now surface as an
Unassigned block at the bottom of the Series page, reported by
[@LegendaryB](https://github.com/LegendaryB)
([#31](https://github.com/dgahagan/shelf/issues/31)).

### Added

- **Books with no series now appear on the Series page**, in an "Unassigned"
  block after your real series. The heading carries the true total — "1014
  books with no series" — and the strip below it shows a sample of twelve
  covers, with "· showing 12" in the count line when there are more. Click any
  cover to open the item and set a series on its edit page.

  It is deliberately not a series. It has no rename, disband, mark-complete,
  synopsis or Hardcover-check controls, it is excluded from the `Series (N)`
  heading count, it never appears in the rename autocomplete, and it sorts
  last rather than by size — an unassigned pile is usually the biggest group
  in a library, and it should not become the headline of a page about series.

  The Complete and Incomplete filter chips hide it. A pile of unsorted books
  makes no claim about completeness either way, and filing it under
  "Incomplete" would be exactly the kind of claim the three-state model exists
  to avoid.

  Scope is books — `book`, `kids_book`, `audiobook`, `ebook` and `comic`. CDs,
  DVDs and video games essentially never carry a series, so including them
  would bury the books you were looking for.

  If your library has no series at all, the block still renders alongside the
  "No series yet" message — that is exactly when knowing how many unsorted
  books you have is most useful.

## [0.12.0] - 2026-08-21

Scanning a stack of books used to get slower and flakier the longer you went:
every scan downloaded its cover while you waited, and nothing paced or retried
the requests going out. Covers now download in the background, and every
outbound lookup is throttled per host and retried on transient failures
([#27](https://github.com/dgahagan/shelf/issues/27)).

### Changed

- **Scanning no longer waits for the cover.** The result card appears as soon
  as the metadata lookup finishes, with a placeholder that fills itself in a
  second or two later. Covers are fetched by a background worker instead of
  inside the scan request, so a slow or unresponsive cover host no longer
  holds up the scan — which is what made bulk scanning degrade.

  If the worker is busy — a big import draining, say — the placeholder settles
  after a couple of seconds rather than polling forever. The cover still
  arrives; it shows up on the next page load. CSV import and photo-intake
  enrichment go through the same queue, and a restart re-queues anything added
  in the last 48 hours that is still missing a cover.

- **Outbound lookups are paced and retried.** Every metadata and cover request
  now goes through a shared per-host rate limiter and, where appropriate, a
  bounded retry with backoff that honours `Retry-After`. Hosts are paced
  independently, so a slow Hardcover response no longer delays an Open Library
  lookup.

  The pacing follows each service's published guidance. Notably, Open Library
  limits ISBN-keyed cover requests to 100 per IP per 5 minutes and returns a
  403 beyond that — which previously read as "no cover found" and left large
  imports silently blank. Shelf now paces that host accordingly and identifies
  itself with a contact URL, as Open Library asks.

  Timeouts are retried only off the request path. A scan still fails after a
  single timeout rather than retrying two more times, so the worst case for a
  scan is what it always was.

- **Settings shows what the cover queue is doing.** A line under Retry Missing
  Covers reports how many lookups are queued, how many gave up since startup,
  and how many items have no cover — visible when there is something to
  report, so a batch that quietly failed is no longer invisible.

### Fixed

- **Retry Missing Covers no longer attaches book covers to DVDs, games and
  CDs.** The bulk retry swept every item without a cover, including non-books,
  and handed them to a book-catalogue title search. Because that search accepts
  the first match when an item has no author listed, a DVD called "Dune" could
  end up with the novel's cover — and the novel's ISBN written onto it. The
  sweep is now restricted to books; covers for discs and games are re-fetched
  from the item page, which uses the sources that can actually answer for them.

- **A single slow lookup no longer aborts a bulk cover retry.** One Open
  Library timeout returned a server error and discarded the covers already
  fetched in that run. Each item is now handled independently, so the run
  finishes and reports what it managed.

## [0.11.1] - 2026-08-20

Removing a borrower who had ever returned a book failed with a 500, reported
by [@LegendaryB](https://github.com/LegendaryB)
([#29](https://github.com/dgahagan/shelf/issues/29)). Fixing it surfaced a
second, quieter problem in the same corner of Settings: none of the
delete confirmations were running at all.

### Fixed

- **Removing a borrower with past loans no longer returns a 500**
  ([#29](https://github.com/dgahagan/shelf/issues/29), reported by
  [@LegendaryB](https://github.com/LegendaryB)). Lend a book, take it back,
  then try to remove the borrower — the delete failed with a server error,
  and it kept failing. A borrower became permanently undeletable the moment
  their first loan completed.

  Loan rows reference the borrower and the database enforces that reference,
  so deleting a borrower who still had history attached was rejected
  outright. The original guard only checked for loans that were still *out*,
  which is why a borrower with nothing on loan still could not be removed.
  Removing a borrower now removes their completed loan history with them —
  the same "clean up the references and delete" behaviour that removing a
  location or a platform has always had. Their loans disappear from the
  affected items' history; other borrowers' loans on those same items are
  untouched.

  Note that this is not reversible in place. A backup taken before the
  deletion restores it; a portable archive export does not, because merge
  import will not re-attach loan history to books you still have. The
  confirmation dialog now tells you how many past loan records are about to
  go, which brings us to the second half of this release.

- **Delete confirmations on the Settings page actually appear now.** Every
  "are you sure?" on that page — borrowers, locations, and game platforms —
  had been silently dead. The confirmation was wired up as an inline
  handler, and Shelf's content-security policy refuses to run those, so all
  three destructive deletes fired immediately on click with nothing asked.
  There was no error and no visible symptom; the dialog simply never
  happened. All three now use a policy-clean handler and genuinely ask
  first, and there is browser-level test coverage pinning that they do.

- **A borrower who still has a book out gets a real answer.** Attempting
  that removal used to dump a line of raw JSON into the browser, which you
  had to navigate back from. It now returns you to Settings with a plain
  explanation that the item needs checking in first.

## [0.11.0] - 2026-08-20

The metadata half of internationalization. Shelf now knows what language an
edition is in, lets a bilingual household browse by it, and gives German
ISBNs a first-class metadata source: the Deutsche Nationalbibliothek.

### Added

- **DNB metadata source for German ISBNs.** Scans and adds of `978-3` ISBNs
  consult the Deutsche Nationalbibliothek's SRU catalog (free, no key, CC0
  metadata) *before* the usual Open Library → Hardcover → Google Books
  cascade — the national bibliography is authoritative for its own
  registration group. A DNB miss falls through to the existing cascade
  unchanged, and non-German ISBNs behave exactly as before. The routing is a
  registry (`ISBN prefix → provider`), so future national sources are one
  client file and one line each.
- **Edition language, everywhere it needs to be.** Items have a `language`
  field (ISO 639-1), captured automatically from DNB, Open Library, Google
  Books, and photo intake; editable on the add and edit forms (unmappable
  codes are preserved, never silently discarded); shown on the item page.
  Existing libraries are backfilled once from unambiguous ISBN registration
  groups (`978-0/1` → English, `978-3` → German, …) — items outside the
  unambiguous set stay unset.
- **Browse language filter.** Appears only when your library actually
  contains language data, offers only the languages it contains, and
  composes with every other filter — counts included.
- **Search-language setting.** Settings → Display → *Metadata search
  language* steers title search, CSV-import ISBN recovery, and the
  photo-intake edition preference toward your language's editions, so a
  German user's spine photos stop resolving to English editions. Defaults
  to English — nothing changes unless you change it.
- **DNB cover art.** German ISBNs try the DNB/MVB cover service after the
  existing sources, filling covers Open Library and Amazon often miss.
- **Library archives carry language** through export → import round trips;
  archives from older versions import cleanly.
- **Scan feedback on manual entry.** Typing an ISBN and hitting Enter now
  pops a toast with the outcome (added / duplicate / invalid) — previously
  the result card landed below the fold and the submit looked like a
  silent no-op.
- **Photo intake shows it is working.** A visible spinner panel during
  spine analysis ("large shelves can take a minute") and while adding the
  confirmed books — the old button-label swap was easy to miss on mobile.

### Fixed

- **Browse filter dropdowns went dead after the first change.** The
  cross-filter count refresh replaces the dropdowns via an out-of-band
  swap, but the swapped-in elements were never re-wired — so the second
  and every later dropdown change silently did nothing until a page
  reload. Present in every release since the counts shipped; caught by
  this release's new end-to-end coverage.

Books whose author name carries an accent, a middle initial, or a stroked
letter get their cover art again. If your library has items stuck without a
cover, run Settings → Data → Maintenance → **Retry Missing Covers** after
upgrading — it will now find many of them, and it now also looks at items it
used to skip entirely.

### Fixed

- **Author matching no longer rejects the same person written a different
  way.** Every metadata lookup checks the result's author before trusting
  it — that guard is what stops a study guide or graded-reader adaptation
  being mistaken for the real book. But the check was a plain substring
  test, so it only accepted names spelled character-for-character alike,
  and quietly rejected the same author written any other way:

  | Your item says | The source says | Result |
  |---|---|---|
  | `Stanislaw Lem` | `Stanisław Lem` | no cover |
  | `Richard P. Feynman` | `Richard Phillips Feynman` | no cover |
  | `James Duane` | `James J. Duane` | no cover |

  Matching now folds accents and stroked letters (`ł`, `ø`, `đ`, `ħ` — which
  Unicode normalisation alone leaves untouched), and accepts an initial in
  place of the name it abbreviates. Surnames must still agree exactly and
  distinct given names are still rejected, so `Frank Herbert` continues not
  to match `Brian Herbert` and the study-guide guard is unchanged.

  **Photo intake was hit hardest**, because the vision model transcribes
  whatever is printed on the spine — which is exactly where ASCII-ised
  accents and abbreviated middle names come from. On the shelf photo used
  for this project's own demo, 3 of 11 books lost their covers to this.

  The check lived in three separately-maintained copies (item cover
  enrichment, photo intake, synopsis lookup), all with the same flaw; they
  are now one shared helper, so the next improvement lands on all three.

- **"Retry Missing Covers" can now actually recover the items it is for.**
  The button skipped every item that had no ISBN — `WHERE isbn IS NOT NULL`
  — and for the rest tried only the ISBN cover chain, never the title and
  author search. So the two groups most likely to be missing art (items
  added without an ISBN, and editions whose ISBN has no cover anywhere)
  were precisely the ones it could never fix.

  Retry now considers every item without a cover and runs the same full
  resolution the import path uses, including the title/author fallback and
  storing any ISBN it recovers along the way. Combined with the author fix
  above, a single run should clear a good deal of long-standing backlog.

## [0.10.0] - 2026-08-20

Camera scanning now works on iOS Safari — on the scan page **and** in Store
Mode — reported by [@dgahagan](https://github.com/dgahagan)
([#12](https://github.com/dgahagan/shelf/issues/12)) and largely built by
[@fabian1512](https://github.com/fabian1512)
([#23](https://github.com/dgahagan/shelf/pull/23)).

**Store Mode re-downloads its offline files on first visit after upgrading.**
The service worker cache version moved to v4 to pick up the new scanner
files; this is automatic, but the first load needs a connection.

### Fixed

- **Barcode scanning on iOS Safari** ([#12](https://github.com/dgahagan/shelf/issues/12)).
  Shelf's scanner used html5-qrcode everywhere, which has long-standing
  camera-stream, autofocus and detection-rate problems on iOS Safari —
  scanning was unreliable to the point of being unusable on iPhones and
  iPads. Shelf now detects iOS and drives the camera with
  [ZXing](https://github.com/zxing-js/browser) there instead, keeping
  html5-qrcode byte-for-byte unchanged on every platform where it already
  works. USB and Bluetooth scanners were never affected.

  **Store Mode gets the fix too, not just the scan page.** Store Mode is the
  take-your-phone-to-the-bookshop surface — the place an iOS camera is most
  likely to be the only scanner available — and the original contribution
  covered only the scan page. Both pages now share one scanner engine, so
  the next engine fix cannot land on one page and miss the other, which is
  exactly how Store Mode was left behind by this bug in the first place.

  The ZXing path restricts decoding to the 1D retail formats (EAN-13, EAN-8,
  UPC-A, UPC-E), requests a 1080p-ideal stream and enables ZXing's
  try-harder mode — those settings are what buy the detection rate on iOS.
  UPC-E is new to the format list, which matters for video-game and DVD
  barcodes.

  Engine selection is a device check, not a preference: there is no setting
  to override it, because html5-qrcode on iOS does not fail — it starts
  successfully and simply detects poorly, so there is nothing to detect at
  runtime.

### Added

- **Vendored JavaScript is now verified against its pinned hashes.** Shelf
  ships all third-party JS locally rather than from a CDN, with SHA-384
  hashes recorded in `static/vendor/HASHES` — but nothing checked them.
  A test now recomputes every vendored file's hash and fails if a blob or a
  hash line was altered, in either direction, so a modified dependency
  cannot pass unnoticed.

### Changed

- **Both camera pages share a single scanner engine module.** The camera
  lifecycle — engine selection, start, stop, pause, resume — moved into one
  framework-free module used by the scan page and Store Mode alike. No
  user-visible behaviour changed on any platform that already worked.

## [0.9.0] - 2026-08-20

Collection values render in the currency you choose, requested by
[@LegendaryB](https://github.com/LegendaryB)
([#26](https://github.com/dgahagan/shelf/issues/26)).

**This is display formatting, not conversion.** Shelf never converts amounts
between currencies — see the note under the setting below for why, and what
that means if you use ISBNdb valuation.

### Added

- **A display currency setting, and every value surface honours it**
  ([#26](https://github.com/dgahagan/shelf/issues/26)). Settings → Collection
  gains a currency picker covering 20 currencies. The stats tile, item detail,
  the item-edit field label and its ISBNdb hint, the valuation report (summary
  tiles, group subtotals, per-item cells and the grand total), the stats
  valuation chart's tick labels and tooltips, and the live valuation run log
  all switch to the currency you pick.

  Symbol placement, spacing and precision follow the currency rather than
  being bolted onto a dollar format: prefix currencies render tight
  (`€1,234.56`), suffix currencies take a space (`1,234.56 kr`), and
  zero-decimal currencies round (`¥1,235`). Thousands separators are now
  applied everywhere — previously only the stats tile grouped them, so the
  same number could render two ways on two pages.

  **Amounts are never converted.** The setting relabels what a number *is*,
  it does not restate it in another currency. Exchange-rate conversion was
  considered and rejected: it needs a live rate feed in an app that is meant
  to work offline, and it would make two insurance reports generated a week
  apart disagree on the total with nothing in the collection changed — which
  is exactly what an insurance document must not do. Manual values you type
  need no conversion at all; they are already in your currency.

  One consequence is called out in the UI rather than hidden. ISBNdb returns
  **USD list prices**, so with a non-USD currency selected, batch valuation
  stores USD amounts that then display with your symbol. A caveat now appears
  beside the *Valuate Collection* button and in the valuation report footer
  whenever the currency is not USD, so the numbers are never silently
  mislabelled — most visibly in the report, whose whole purpose is insurance
  documentation.

  Existing installs are unaffected: USD is the default, and its output is
  byte-for-byte what it was before.

### Changed

- **The build's test and asset tooling got substantially cheaper to run.**
  `make test` is now quiet and parallel (~105s → ~17s), with
  `make test-verbose` for the old per-test roll-call and `make test-fast` for
  a re-run of just the last failures. `make checks-fast` splits the instant
  offline lints out from the network-bound dependency audit, while
  `make checks` keeps its full release meaning. `make css` resolves Tailwind
  from a pinned `package.json` instead of refetching on every invocation, and
  emits identical output. Building from source now also runs `npm install` as
  part of `make setup`.

  This also repaired `make verify`'s minimum-test-count guard, which had never
  actually worked — its comparison silently evaluated as false regardless of
  how many tests were present, so it would not have caught a deleted suite.

## [0.8.1] - 2026-08-20

A permanent upgrade crash-loop, reported and fixed by
[@exactmike](https://github.com/exactmike)
([#24](https://github.com/dgahagan/shelf/issues/24)) — plus two smaller
issues found while verifying 0.8.0.

**If your container is stuck crash-looping on `duplicate column name`, this
release fixes it with no manual intervention.** Upgrade and start it; the
database repairs itself on boot.

### Fixed

- **Upgrading no longer leaves the container permanently crash-looping**
  ([#24](https://github.com/dgahagan/shelf/issues/24),
  [PR #25](https://github.com/dgahagan/shelf/pull/25) by
  [@exactmike](https://github.com/exactmike)). A migration's `ALTER TABLE`
  could land on disk while the `schema_version` row recording it did not. On
  the next boot the migration replayed against a column that already existed,
  crashed with `duplicate column name: manual_value` *before* reaching the
  write that would have recorded it, and did the same thing on every restart
  after that. Confirmed on 0.5.0 through 0.8.0, on databases old enough to
  still have migrations pending.

  The mechanism is narrower than it first appears, and it explains the
  fingerprint. Python's `sqlite3` opens an implicit transaction before *DML*
  only, never before DDL — so an `ALTER` issued with no transaction open runs
  in autocommit and lands by itself, while every later `ALTER` in the same run
  joins the pending transaction and rolls back cleanly. Only the *first*
  pending migration was ever exposed, which is why this looked like a
  one-column problem.

  A wedged database now heals itself on the next start: the already-applied
  migration is recorded rather than replayed, and every migration behind it
  applies normally.

- **Migrations are now atomic, so this class of wedge cannot recur.** Each
  migration's schema change and the row recording it commit in one
  transaction — killed mid-upgrade, both roll back together. The fix above
  repairs databases already broken; this stops new ones from breaking, for any
  future migration shape rather than only the `ADD COLUMN` case that was
  reported.

  Two related hardening changes came with it. A migration against a table that
  doesn't exist yet is tolerated only when the table is one the schema
  bootstrap genuinely creates later — a typo'd table name now fails loudly at
  boot instead of being silently recorded as applied. And two migration runs
  that overlap (a restore landing while the app is starting) no longer collide
  on a duplicate version row.

- **Static assets no longer serve stale after an upgrade**
  ([#21](https://github.com/dgahagan/shelf/issues/21)). `/static` and
  `/covers` sent `ETag` and `Last-Modified` but no `Cache-Control`, so
  browsers fell back to heuristic freshness and could keep executing an old
  `components.js` for weeks. In 0.8.0 that surfaced as the mobile nav menu
  rendering permanently expanded with an unresponsive hamburger button —
  `Undefined variable: navMenu` in the console — because the cached script
  predated the component. Both mounts now send `Cache-Control: no-cache`,
  which forces revalidation and costs only cheap 304s. Covers needed it too:
  they are overwritten in place at a stable path.

  A tripwire test now pins the service worker's precache list to a digest of
  the files it names, so changing a precached asset without bumping
  `SW_VERSION` fails the suite rather than shipping a stale cache.

- **The offline service worker no longer serves a stale stylesheet after an
  upgrade.** `app.css` is precached and served cache-first, and its cache name
  is keyed to `SW_VERSION` — which stayed `v2` across releases whose `app.css`
  differed. Anyone who had opened the offline store page kept getting the
  older stylesheet indefinitely: cache-first means the request never reaches
  the network, so neither the `Cache-Control` fix above nor a hard refresh
  could dislodge it.

  The visible result was the nav bar rendering as a hamburger menu **at every
  window width**, because the cached stylesheet predated the responsive
  breakpoint rules the current markup depends on. Bumping to `v3` renames the
  cache, so the service worker's activate step purges the old one and
  re-fetches every precached file. No action needed on upgrade.

### Changed

- **Settings → Navigation now says when a tab is hidden because its
  integration isn't set up** ([#22](https://github.com/dgahagan/shelf/issues/22)).
  A tab auto-hidden for a missing Hardcover token or vision provider still
  showed as checked, which reads as a broken setting. Those rows now carry
  *"Hidden until … is set"* and a **Configure** link straight to the relevant
  integration.

  The checkbox deliberately keeps its original meaning — "not manually
  hidden" — and stays enabled, so a preference set now survives configuring
  the integration later.

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

[0.13.0]: https://github.com/dgahagan/shelf/releases/tag/v0.13.0
[0.12.0]: https://github.com/dgahagan/shelf/releases/tag/v0.12.0
[0.11.1]: https://github.com/dgahagan/shelf/releases/tag/v0.11.1
[0.11.0]: https://github.com/dgahagan/shelf/releases/tag/v0.11.0
[0.10.1]: https://github.com/dgahagan/shelf/releases/tag/v0.10.1
[0.10.0]: https://github.com/dgahagan/shelf/releases/tag/v0.10.0
[0.9.0]: https://github.com/dgahagan/shelf/releases/tag/v0.9.0
[0.8.1]: https://github.com/dgahagan/shelf/releases/tag/v0.8.1
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
