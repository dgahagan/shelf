# Changelog

All notable changes to Shelf are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.24.0] - 2026-08-29

When a barcode scan came back with nothing, Shelf said "Not found" — and meant
several different things by it. Sometimes the book really was unknown to every
source. Sometimes an API key had been rejected. Sometimes a provider was
rate-limiting the request, or the network was down. All four looked identical
on screen, so the one case you could actually fix — a bad key — was the one you
had no way to notice.

The cause was structural: each metadata source returned a bare value and
reported *why* somewhere else, so whichever part of Shelf asked the question
had to reassemble the answer by hand, and each one did it slightly differently.
Every source now returns what happened together with what it found, and the
scan card reads that single answer. Book scans get the honest reasons that UPC
scans already had, and the two paths can no longer drift apart.

### Added

- **A book scan says when a key was rejected.** Scan an ISBN with an invalid
  Hardcover token or Google Books key and the card now reads *"Hardcover
  rejected the configured key — this may not be a genuine miss. Check it in
  Settings → Integrations, or add it below."*, naming the source that actually
  refused. Previously this was indistinguishable from a book no source had
  heard of. An invalid Google Books key is answered by Google with HTTP 400 —
  the same status as a malformed query — which is exactly why it used to be
  filed as "no such book".

### Changed

- **A failed network connection during a book lookup now says so.** Only Open
  Library could previously surface a connection failure; Hardcover, Google
  Books and the DNB catalog each turned a dead socket into "not found". All
  four now reach the *"Network error during lookup — check connectivity"*
  card, and the scan is recorded in the log as an error rather than a miss.
  If your Shelf has been reporting books as unknown while offline, this is why.
- **A rate-limited game scan says so.** Exhausting the Twitch/IGDB request
  budget at the point where Shelf renews its access token used to render as
  "no IGDB match for this barcode". It now shows the same rate-limit notice
  every other source uses. One IGDB case is deliberately unchanged: a
  credential rejected by the game *search* request, rather than by the token
  request, still reads as "no match". Closing that is
  [#49](https://github.com/dgahagan/shelf/issues/49) and needs its own pass
  over the title-search result pages, which this release does not touch.
- **"Not found" no longer says it twice.** A card whose message is already
  "Not found — add manually below" used to add "no `<source>` match for this
  barcode" underneath it. The notice line is now reserved for the cases that
  tell you something new — a missing key, a rejected key, a spent quota.
- **A scan the barcode does not resolve still adds nothing.** The reasons above
  change what the card *says*, never what it files: a rejected key or a spent
  quota is still a miss, and Shelf will not invent an item from one.

### Fixed

- **Hardcover's ISBN-10 fallback now actually runs.** When a 13-digit lookup
  found nothing, Shelf retried against Hardcover's ISBN-10 field but sent the
  unchanged 13-digit value, so the retry could never match. It had never worked;
  a broken retry and a genuine miss returned the same empty answer, which is
  what kept it hidden. Contributed by
  [@martialartistslife](https://github.com/martialartistslife) in
  [#53](https://github.com/dgahagan/shelf/pull/53).
- **An unreadable reply from a metadata source no longer fails the scan.** A
  200 response whose body was not the JSON or XML expected — a captive portal
  or a proxy error page, typically — could raise out of the Open Library and
  DNB clients. Both now treat it as a miss and fall through to the next source.

## [0.23.0] - 2026-08-28

Google Books is one of the sources Shelf falls back to when a scan needs more
than Open Library can give it, and until now Shelf always called it
anonymously. Anonymous Google Books requests are metered per source IP
address, and that budget is shared with every other caller behind the same
address — your ISP's NAT pool, a VPN exit, a cloud host. So those lookups can
come back rate-limited on a completely idle Shelf, with nothing you could do
about it. A probe from the development machine while testing this release hit
exactly that: HTTP 429 with no key, on a query a credentialed request answered
immediately. You can now give Shelf a Google Books API key of your own.

Contributed by [@martialartistslife](https://github.com/martialartistslife) in
[#52](https://github.com/dgahagan/shelf/pull/52).

### Added

- **Google Books accepts an optional API key.** Settings → Integrations has a
  Google Books card: paste a key, press **Test Key** to confirm Google accepts
  it before you save, and every Google Books request Shelf makes — ISBN
  lookups, synopsis lookups and book cover search — is then made with your own
  quota. The key can also be supplied as `GOOGLE_BOOKS_API_KEY` in the
  environment, which overrides a stored one like every other credential.
- **The card explains where the key comes from.** A **How to get a key?**
  panel walks through it, because the key is issued by Google Cloud rather
  than by a Google Books account and the path is not obvious. It also names
  the one setting that will silently break it: an HTTP referrer restriction
  rejects every request, since Shelf calls the API from the server and not
  from your browser.
- **A Google Books section in the integrations guide**, covering the same
  ground at length plus the part worth knowing before you bother — Google
  Books is the *last* book source tried on an ISBN scan, so a key changes
  nothing visible while Open Library is answering, and earns its keep on thin
  scans and on bulk work like the synopsis backfill or a large Photo Intake.

### Changed

- **Nothing stops working without a key, and that is deliberate.** Keyless
  Google Books remains fully enabled; the key is optional in a way Hardcover,
  IGDB, TMDb and ISBNdb are not. Leave the card empty and Shelf behaves
  exactly as it did before.
- **The key is write-only and never reaches a URL.** It is encrypted at rest
  with the same key-outside-the-database scheme as every other credential, the
  field renders blank once saved (leave it blank to keep the stored value),
  and it is sent only in the `X-Goog-Api-Key` request header — so it cannot
  end up in the outbound request URLs Shelf logs, and needs no redaction to
  stay out of them.
- **A rejected Google Books key says so.** Google answers an invalid key with
  HTTP 400 rather than 401 or 403, so **Test Key** used to report a bare
  `Google Books returned HTTP 400` for the one failure the button exists to
  diagnose. It now reads **Google Books rejected the API key**.

## [0.22.3] - 2026-08-28

Scan a barcode that no metadata source recognises and Shelf raised a pop-up
with nothing written in it — an amber pill, correctly coloured, saying
nothing at all, while the card underneath it named the barcode and said "not
found". Two quieter versions of the same fault were in there too, both in
Lookup mode, where the pop-up said less than the card beside it. All three
are fixed, and every one of the fifteen scan outcomes is now held to saying
what its card says.

### Fixed

- **A barcode nothing recognises now names itself in the pop-up.** Scanning
  an ISBN or UPC that no provider resolves raised a blank pill; it now reads
  **`Not found: <barcode>`**, the same thing the card underneath already said.
  ([#50](https://github.com/dgahagan/shelf/issues/50))
- **A Lookup scan of a barcode you do not own names the barcode.** The pop-up
  read **`Not found`** and nothing else, so two unowned scans in a row were
  indistinguishable; it now names which barcode it was.
- **A Lookup scan of an item you do own names where it is.** The pop-up gave
  the title alone; it now ends with **`— Location: <place>`**, which is the
  answer Lookup mode exists to give.
- **No pop-up in Shelf can be empty, whatever raises it.** A message that
  arrives blank now reads **`Done`** instead of showing an empty pill.

## [0.22.2] - 2026-08-28

Shelf talks to other people's servers, and those servers push back — with a
rate limit, or with a header saying "wait this long". Two of the ways Shelf
handled that were wrong. It trusted a wait it should have discarded, which
turned a scan into a server error; and it asked the barcode provider for
lookups ten times faster than that provider allows, which got the lookups
refused and told you your barcode was the problem. Neither is something you
could have done anything about.

### Fixed

- **A malformed `Retry-After` header from a metadata provider no longer
  crashes the lookup.** If a provider answered a rate-limit response with a
  `Retry-After` of `nan`, Shelf tried to wait that long and raised instead —
  surfacing as a server error on the Google Books and catalogue-search paths.
  Such a header is now ignored and Shelf falls back to its own backoff, the
  same as it already did for the date form. No provider Shelf talks to is
  known to send one; this closes the path before one does.

### Changed

- **Scans of several barcodes in a row are paced slower, and stop being
  rejected by the barcode provider.** UPC Item DB's free tier permits six
  lookups a minute; Shelf was pacing itself at up to sixty, so working
  through a stack of discs or games ran into the provider's own limit and
  the cards came back saying you were rate-limited. Shelf now leaves ten
  seconds between barcode lookups, which is the rate the tier actually
  allows. A single scan is unaffected — the wait only applies to the second
  and later scans in quick succession, and only to barcode (UPC) lookups,
  not to ISBNs.

## [0.22.1] - 2026-08-27

Type a barcode into the scan box and Shelf told you about it twice. One pop-up
message came from the server and one from the browser, for the same scan — and
they did not always agree. The browser's version knew when an outcome was a
problem and coloured it as a warning; the server's called everything a success,
including "already lent" and "not currently checked out". Scanning with the
camera, meanwhile, only ever showed one. A scan now reports itself once, from
one place, in every mode.

### Fixed

- **A typed scan raises one pop-up message, not two.** Seven scan outcomes
  were double-reporting: adding or wishlisting a book by ISBN, a film by UPC
  and a game by UPC, plus lending, returning, moving and marking read. Each
  now surfaces exactly once. The camera path is unchanged — it always showed
  one, and still does. ([#45](https://github.com/dgahagan/shelf/issues/45))

### Changed

- **The surviving message is the browser's, so its wording changes slightly.**
  It is now built from the result card you can see, rather than composed
  separately on the server, which is what makes the two impossible to
  disagree. A lend reads **`Lent: <title> — Lent to <borrower>`** where it
  used to read **`Lent: <title> → <borrower>`**, and titles are no longer cut
  off at 40 or 50 characters.
- **A move now names where the item came from, not just where it went.** The
  message reads **`Moved: <title> — Living Room → Office`**; the retired
  version named the destination only.
- **An outcome that is not a success is coloured as a warning.** "Already lent"
  and "not currently checked out" used to arrive as a green success message
  alongside the correct amber one. Only the amber one is left.

## [0.22.0] - 2026-08-27

A scan that came back thin had one way of saying so: *no match*. But there are
several different reasons a lookup gets you nothing, and they need completely
different responses from you. A rejected API key needs fixing. A provider
that is rate-limiting you needs waiting out. A format Shelf has no metadata
source for needs neither — there is nothing to fix and nothing to configure.
"No match" sent you looking for a problem in the wrong place every time it
was not the truth. The card now names which dead end it actually hit.

### Added

- **A rejected IGDB key says so, instead of claiming the game does not
  exist.** A stale or rotated Twitch secret fails the token exchange, and
  every game scan afterwards read "no IGDB match for this barcode" — advice
  to give up, when the correct advice was to fix the key. The card now says
  **"IGDB rejected the configured key."** and the server log carries a WARNING
  naming the HTTP status at the default log level, where before the refusal
  was only visible at DEBUG. When a game now reads "no IGDB match", it means
  it. ([#42](https://github.com/dgahagan/shelf/issues/42))
- **A rate-limited lookup says "rate-limiting", not "not found".** A provider
  that has answered HTTP 429 has not told you the barcode is unknown — it has
  told you to come back later. Both cards carry the distinction now: a thin
  record says "a metadata provider is rate-limiting us right now. Re-scan
  later to fill it in.", and a **Not found** card says "A metadata source is
  rate-limiting us right now — this may not be a genuine miss." That second
  one is the one that saves work: it means the record may well exist, and
  typing the book in by hand is probably wasted effort.
- **An unreachable provider is told apart from an unknown barcode.** A UPC
  scan that could not reach the lookup service at all — no DNS, no route, a
  timeout — now shows **"Metadata lookup failed — check connectivity"** and is
  written to the scan log as `error` rather than `not_found`. Previously a
  disconnected container reported every barcode as genuinely missing, which is
  the same screen you get for a real miss and sends you hunting for the
  wrong thing.

### Changed

- **A CD is no longer searched on a film database.** Scanning a music CD filed
  it correctly as a CD and then asked you to sign up for a TMDb API key,
  because everything that was not a game went down the film branch. A CD now
  says **"Added with title only — Shelf has no metadata source for this format
  yet."** and, more to the point, no film request is made at all. This goes
  further than suppressing the wrong sentence: any future media type with no
  provider behind it gets the honest answer by default rather than a film
  search. CDs still get no metadata — that waits on a music provider — but
  they no longer send you to fix something that was never broken.
  ([#44](https://github.com/dgahagan/shelf/issues/44))
- **A Google Books failure mid-lookup now reads as "not found" rather than a
  network error.** When Google Books was the source that fell over, the whole
  ISBN lookup used to abandon itself and show the connectivity card even
  though other sources were still reachable. It is now treated the way the
  other fallback sources already were, so the cascade finishes. A box that is
  genuinely offline still gets the connectivity card, from the first source.

**The card deliberately does not name which provider is rate-limiting you.** A
book lookup consults up to four sources and any subset of them can be starved
at once, so naming one would be a guess. The server log names the exact host;
the card says only that it happened. For the same reason the **Find cover**
picker is unchanged — a rejected key there still reports "No covers found",
and giving that surface the same honesty is its own piece of work.

## [0.21.1] - 2026-08-27

Scanning a barcode could sit there for a minute and then tell you nothing was
found. Metadata providers on free or trial tiers meter you per day, and once
that day's allowance is spent they answer every request with "rate limited,
come back in an hour" — sometimes in several hours. Shelf treated that the
same way it treats a momentary blip: it waited, asked again, waited, asked
again. The provider had already said the answer would not change. This
release takes it at its word.

### Changed

- **A provider that asks you to wait longer than 30 seconds is no longer
  retried.** A server naming a wait of an hour is reporting a spent quota,
  not a hiccup, so Shelf returns the failure immediately instead of sleeping
  the maximum and asking again on every attempt. In practice a UPC scan that
  hung for about a minute now finishes in a fraction of a second, and the
  worst case — every retry stage burning the full cap — drops from roughly
  four minutes to the same fraction. Short waits are unaffected: a provider
  asking for 20 seconds still gets exactly 20 seconds, and a rate-limit
  response with no stated wait still falls back to the usual escalating
  backoff. ([#47](https://github.com/dgahagan/shelf/issues/47))
- **The log now names the provider that ran out.** At the default log level,
  Settings → Logs shows a line like `outbound: api.upcitemdb.com returned
  HTTP 429 asking for a 4275s wait, beyond the 30s ceiling — not retrying`.
  Until now an exhausted quota and a genuinely unknown barcode were
  indistinguishable from the outside.

  This release deliberately stops short of changing what you see on screen:
  the scan card and the title-search results still report a plain miss when
  a provider is out of quota. Making those say *why* touches the wording on
  several surfaces and is its own piece of work — the log line is the way to
  tell the two apart today, and
  [troubleshooting](docs/troubleshooting.md) now explains how.

### Fixed

- **A camera scan of an item with no cover no longer requests a missing
  image.** The overlay's cover slot was hidden for a cover-less result but
  still asked the browser for it, so every such scan left a stray failed
  request behind. Nothing on screen changes; the noise in the browser's
  network log does. ([#46](https://github.com/dgahagan/shelf/issues/46))

## [0.21.0] - 2026-08-26

The media-type dropdown on the scan tab was an oracle: whatever it said is
what the item became. Scan a novel with it still set to "DVD" from last week
and you filed a novel as a DVD — and because the setting is sticky, the people
most affected were the ones who had used Shelf longest. The barcode knew
better the whole time.

### Changed

- **The barcode outranks the dropdown when it is certain.** A 978/979 prefix
  is an ISBN, so a book scanned under any non-book setting is filed as a book
  and the card says it overrode you. A non-ISBN barcode is certainly not a
  book, so a disc scanned under "Book" is no longer filed as one. Because this
  keys off the barcode rather than a new default, it reaches existing users
  without them changing a setting — a saved choice is never silently
  reinterpreted.
- **A UPC's media type is detected from the product record**, not from the
  dropdown: platform and format wording in the raw retail title
  (`Nintendo Switch`, `[DVD]`, `4K UHD`) first, then the product category, and
  only ever for games. A category alone never decides "disc", and a category
  naming a console never decides "game" — that is the shelf the product sits
  on, not what the product is, and it is what stops a PlayStation 5 being
  catalogued as a video game. Where nothing contradicts your pick, your pick
  stands; CDs in particular have no detection at all, so the dropdown remains
  the only thing that can say "CD".
- **A game scanned in Wishlist mode is filed as wishlisted.** It was filed as
  owned — the game path never read the scan mode.
- **A re-scan of something you already own is recognised from the barcode
  alone**, before any provider call. It costs no network round-trip, and a
  rate-limited or offline lookup can no longer report an item already on your
  shelf as "Not found".

### Added

- **The scan card says why a record came back thin.** Three distinct notices,
  because the response differs: no provider key configured, a key the provider
  rejected, or a provider with no match for that barcode. Previously all three
  looked identical to a successful scan. (On IGDB a rejected credential is
  indistinguishable from a genuine miss, so it reads as "no match" — Settings
  → Integrations → **Test key** is how to tell.)
- **Auto in the media-type picker**, and the default for a new install. The
  platform field stays available under Auto, since a game can still be
  detected under it.

### Fixed

- **The scan result card is read by its own data attributes.** The camera
  overlay and the typed-entry toast each re-derived the outcome by
  substring-matching CSS class names out of the returned HTML, so any element
  styled with a warning colour inside a successful card could flip the whole
  card to a failure, and any muted paragraph above the authors line became the
  author. Both now read one shared reader.
- **An unrecognised media type is rejected at the route boundary.** Nothing
  validated the value before — the column has no constraint and the save layer
  checks field names, not values — so a malformed or tampered form could store
  anything.
- **A disc is no longer stamped "via tmdb" when TMDb never answered.** The
  film branch hard-coded the source on both the stored row and the result
  card, so an item filed from the UPC title alone still claimed TMDb as its
  provenance — and with the new notice beside it, the card argued with itself:
  *"DVD / Blu-ray via tmdb"* two lines above *"Add a TMDb API key"*. It now
  says `upc`, the way the game branch always has. Rows already stored with the
  wrong source are left alone; nothing rewrites your data.

## [0.20.0] - 2026-08-26

**Find cover** searched for a book no matter what the item was. On a DVD it
asked Google Books and Open Library for a film poster, and on a video game it
asked them for box art — so the picker that 0.19.0 made reachable everywhere
was useful on roughly one media type. It now asks the source that actually
holds the artwork: TMDb for discs, IGDB for games.

### Added

- **DVDs and Blu-rays search TMDb's poster set for the film.** A film usually
  has one poster in many languages rather than many different posters, so each
  tile is labelled by language — `TMDb · EN`, `TMDb · FR`. The item's year
  picks the right film when several share a title: *Dune* (1984) and *Dune*
  (2021) return completely different posters.
- **Video games search IGDB, and get both kinds of art.** Cover art and key
  artwork come back as separate tiles, each labelled with the game it belongs
  to and which kind it is — `IGDB · Portal · cover`, `IGDB · Portal · art` —
  so a search spanning a series is not ambiguous. The item's stored platform
  narrows the search when it is set.
- **The picker says which credential is missing.** A DVD with no TMDb key
  reads *"DVD cover search needs a TMDb API key — add one in Settings →
  Integrations."* instead of "No covers found for this title." — the artwork
  exists; Shelf just could not ask for it. A configured provider that
  genuinely found nothing still says "No covers found."

### Changed

- **Which source Find cover asks depends on the item's media type.** Books,
  ebooks, audiobooks, kids' books and comics are unchanged — still Google
  Books and Open Library, still combining the item's stored author with your
  query. DVDs and games no longer do; they search on the title (or what you
  type) alone. An item whose media type Shelf does not recognise takes the
  book path rather than failing.
- **The automatic cover chain is untouched.** The cascade that runs when an
  item is added (Open Library → Hardcover → DNB → Amazon → Google Books →
  IGDB) and the background retry queue behave exactly as before. Only the
  human-driven picker dispatches on media type.

### Notes

- IGDB key artwork is landscape (16:9) where cover art is portrait. The item
  page letterboxes it, but the Browse grid crops it to a portrait card, which
  can leave a wide artwork looking like a vertical slice. Pick cover art if
  you want the Browse thumbnail to read well.
- A game whose recorded platform genuinely never had that title returns no
  results, because the platform filter is doing its job. Typing a different
  query does not lift it — the platform comes from the item. Clear or correct
  the platform on the item if the search should be wider.

## [0.19.0] - 2026-08-26

The cover picker's gallery already existed, but it was almost entirely
unreachable: the "Find cover" controls only rendered inside the "no cover"
branch of the item page, so the moment any cover landed — including a wrong
one — the only way to change it was tracking down a JPEG yourself and using
the upload field on the edit page. Fixing an obviously wrong cover, the
common case, was harder than fixing a missing one.

### Added

- **The cover picker is reachable on every item**, not just cover-less ones.
  **Find cover** now opens the same candidate gallery whether or not the
  item already has a cover.
- **A query box in the picker.** Search any term instead of being stuck with
  the item's stored title. The item's stored author still narrows the search
  either way, so an item whose author is wrong may return nothing whatever
  you type — upload an image in that case.
- **The current cover renders as the first tile, marked *Current***, so you
  compare candidates against what's on the item rather than against memory.
- **Upload** now sits beside the gallery on the item page (previously only
  on the edit page and on manual add). Accepts JPEG / PNG / GIF / WebP,
  100 bytes to 10 MB; anything else is refused and the existing cover is
  untouched.
- **Remove cover.** Clears the cover; the image file is left on disk.
  Removing the cover of a book added in the last 48 hours can be undone by
  the automatic background retry after a container restart — an accepted,
  narrow-window limitation; a durable "removed on purpose" flag is deferred.

### Changed

- The picker button is now labelled **Find cover** (was "Search by Title").
- **Cover controls are editor+.** A viewer no longer sees them at all
  (previously they rendered for viewers on cover-less items and 403'd on
  click).
- **Retry cover** ("Retry ISBN") now appears only when a cover is missing —
  it re-runs the automatic download chain, which has nothing to do once a
  cover already exists.

### Fixed

- **A failed cover pick no longer destroys the gallery.** The grid
  re-renders with the failing tile marked, instead of replacing every
  candidate with one line of error text.

## [0.18.0] - 2026-08-25

Browse's list view showed five columns — Title, Author, Type, Location, Status —
and there was no way to change them. Everything else Shelf knows about an item,
its series, publisher, year, page count, language, ISBN, the value it is carrying,
was a click away on the detail page, one item at a time. So the list was fine for
finding a book and useless for the questions a table is actually good at: which of
these have no publisher, what did I add last month, which of these are in German.

The list view now has a **Columns** picker: thirteen columns to show or hide in
whatever combination you want, remembered for next time.

### Added

- **A column picker in Browse's list view.** The toolbar's new **Columns** button
  opens a checklist of thirteen columns — Author, Type, Location, Status, Value,
  Series, Publisher, Year, Pages, Language, Added, Platform and ISBN/UPC. The
  first four are on to start with, which is what the list showed before; the
  other nine are new and start off. **Value** is the one the original request
  asked for: your manual value where you have set one, otherwise the ISBNdb
  estimate, in your display currency.
- **Your choice is remembered, per browser.** Turn on Series and Year, close the
  tab, come back next week and they are still on. It is stored in the browser
  rather than against your account, so a phone and a desktop keep their own
  column sets — usually what you want, since they have very different amounts of
  room. A new browser, a new device, or a cleared cache starts back at the
  defaults.
- **Reset to defaults**, at the bottom of the picker, puts the list back to
  Author, Type, Location and Status in one click.

The selection checkbox, the cover thumbnail and **Title** are always shown and
are deliberately not offered in the picker. Title is the row's link to the item,
so a row without it would be a row you cannot click.

Turning on more columns than fit does not widen the page — the table scrolls
sideways inside its own frame and the rest of the page stays put. Columns with
nothing to show read as empty rather than broken: an item with no publisher gets
an em-dash, the same as everywhere else in Shelf.

### Changed

- **Columns no longer disappear on their own at narrow widths.** Author used to
  vanish below 768px, Type and Location below 1024px, and Status below 640px,
  whatever you wanted. Now your selection is what you get at every width, so a
  phone shows all four default columns and the table scrolls sideways to fit
  them. This is the deliberate trade for a picker that works: a column that
  hid itself at a breakpoint could not also be one you switched on, and being
  able to tick Author on a phone and have nothing happen is worse than a table
  you drag. If you prefer the narrower phone view, untick the columns you do not
  want on that device — the choice is per-browser, so it will not follow you back
  to the desktop.

## [0.17.7] - 2026-08-25

**Sync Now** on the Audiobookshelf card was greyed out on every page load, for
every setup — including one where the URL and token had been saved for months
and the scheduled sync was running perfectly. The button said *"Enter URL and
token to sync"* at someone who had already done exactly that. The only way to
revive it was to retype the token, which made it look like the saved credential
was the problem when it never was.

The button was asking the wrong question. It checked what was *typed into the
form*, and the token box is deliberately always empty — Shelf never writes a
saved credential back into the page. A manual sync reads the credentials the
server has stored, so that is what the button now checks.

### Fixed

- **Sync Now works for a saved Audiobookshelf configuration.** The button is
  live whenever the server has a URL and a token to sync with, whether they were
  saved in Settings or supplied through `ABS_URL` / `ABS_TOKEN` in the
  environment. Scheduled sync was never affected — this was only ever the manual
  button.
- **The button says which of the two things is missing.** Three states instead
  of one: *"Sync Now"* when it will work, *"Save your settings to sync"* when
  credentials are typed but not yet saved, and *"Enter URL and token to sync"*
  when there is nothing to sync with. The middle one is new — the old label
  could not describe it, and it is the state you are in the moment before you
  press Save.

Note that Sync Now deliberately stays disabled for credentials you have typed
but not saved, even though **Test** beside it lights up for them. That is not an
inconsistency: Test sends what is in the boxes, so it can check a credential
before you commit to it, while a sync reads what the server has stored and would
fail on values it has never seen.

## [0.17.6] - 2026-08-25

An integration credential does not have to be typed into Settings — it can come
from an environment variable instead, which is how you would wire Shelf to
Docker secrets or a secrets manager. But the Settings page decided whether an
integration was configured by asking whether it had *saved* the credential
itself, and an environment variable leaves nothing to save. So the **Test**
button sat greyed out in front of an integration that was working perfectly,
and the only way to check a key was to paste a second copy into the form.

The page now asks a different question — *is a credential available?* — which an
environment variable answers just as well as a stored one.

### Fixed

- **Test buttons work for credentials supplied by environment variable.** All
  five — Audiobookshelf, Hardcover, ISBNdb, TMDb and IGDB — are live when the
  credential is in the environment, and they check it where it actually lives
  rather than reporting it missing. IGDB still waits for both halves of its
  pair: one of the two is not a credential.
- **Valuate Collection and both Hardcover transfers use it too.** Collection
  valuation, Import from Hardcover and Export to Hardcover read the same
  environment credential the rest of Shelf already read. Previously they either
  refused to start or started and immediately announced that the key was
  missing.
- **The documentation describes what the page actually does.** Configuration
  used to promise that an env-supplied credential "shows as *set by
  environment*" in the interface. It never has, in any version. That claim is
  replaced by what is true: the field stays blank because Shelf never echoes a
  secret back, the environment value takes priority over anything stored, Test
  key works against it, and removing it means changing your environment —
  Settings cannot reach it.

Deliberately still absent: a credential that comes only from the environment
shows **no** "Remove saved key" checkbox and **no** "Saved — leave blank to
keep" label. Both would be lying. There is no stored row to remove, and no
checkbox can reach into your environment, so Shelf stays quiet about where a
working credential came from rather than offering a control that cannot work.

## [0.17.5] - 2026-08-25

A filtered Browse view is meant to be bookmarkable — that is the point of
putting the filters in the address bar. But if the address ever arrived
damaged, from a link a chat app had truncated or a hand-edited URL with a typo
in it, Shelf answered with a server error page instead of a library. A
bookmark that rots into an error is worse than one that rots into an empty
shelf, because the error gives you nothing to correct.

Now a filter value that cannot name anything simply matches nothing, which is
already what a bookmark pointing at a location you have since deleted does.

### Added

- **The README shows how many tests each suite carries.** Two badges beside the
  CI one — unit and end-to-end, counted separately, because the two suites
  cannot run in a single invocation and a combined number would hide that. The
  counts are generated from what pytest collects, not typed in, and a lint in
  the standard check set fails if they drift, so the badge cannot quietly go out
  of date the way a hand-written one does.

### Fixed

- **A damaged Browse link no longer shows an error page.** A filter value that
  cannot name a location — from a truncated link, a typo, or an edited address
  bar — used to fail the whole page with a server error, on both Browse and the
  search behind it. It now does what a link to a location you have since
  deleted already did: shows no items, with the filter still listed so you can
  clear it. Deliberately *no* items rather than all of them: the filter chip
  stays on screen, and a chip that says the view is narrowed while the grid
  shows the whole collection would be lying to you.

## [0.17.4] - 2026-08-25

Three screens were laid out against a width nobody had declared. A file picker,
a badge and a dropdown are each as wide as the font renders them, and Shelf's
layout had been budgeting those as if they were fixed sizes — so a row that fit
on the machine it was measured on ran off the edge of the screen on a machine
with a slightly wider system font. On a 320px phone the Settings page scrolled
sideways; on a tablet the photo-intake review row squeezed the book title down
to something too narrow to read.

The fix is to declare the widths that were being guessed, and to give the rows
somewhere to wrap when they still do not fit. Nothing about what Shelf does
changes — but if you use Settings on a small phone, or review a shelf photo on
a tablet, the page you get is the one that was intended.

### Fixed

- **Settings no longer scrolls sideways on a narrow phone.** The Data tab's
  three file pickers and the Library tab's location, borrower and platform rows
  had no declared width, so they were as wide as the system font made them —
  fine on some machines, 25px past the edge of a 320px screen on others. The
  file pickers now take the full width of their row, and the location, borrower
  and platform rows wrap their Remove button onto a second line rather than
  pushing it off the screen.

### Changed

- **The photo-intake review row now becomes a single line at 1024px instead of
  768px.** Between those widths it keeps the stacked three-line layout. The
  single-line version was fitting only because of how wide one particular
  system font happened to be; on other machines the title box was squeezed to
  92px, which is too narrow to read a book title in. Below 1024px the title now
  gets the full width of the row. On a desktop-width window nothing moves.

## [0.17.3] - 2026-08-25

Nothing in this release changes what Shelf does. It moves the test suite onto
pytest 9, which the previous set of pinned versions could not accept, and clears
out a warning filter that no longer had anything to suppress. It is written down
here because the version exists, not because you will notice it.

Runtime dependencies are untouched, and the published image never installs
development dependencies — so there is nothing in this upgrade for a running
instance to gain beyond staying current.

### Internal

- **The test stack moves to pytest 9.** pytest goes from 8.3.5 to 9.0.3 and
  pytest-asyncio from 0.25.3 to 1.4.0. The two had to move together:
  pytest-asyncio 0.25.3 declares `pytest<9`, so raising pytest on its own left
  the development requirements unsolvable and continuous integration failed
  before a single test ran.
- **A dead warning filter is gone.** `pytest.ini` carried a suppression for a
  deprecation warning that only the old pytest-asyncio emitted. Under 1.4.0 it
  emits none, and a filter that matches nothing cannot do anything except hide
  a future warning that matters.

Both suites pass unchanged on the new stack — 1,521 unit and integration tests
and 123 browser tests — on Python 3.12 and Python 3.14.

## [0.17.2] - 2026-08-24

Open a filtered Browse link — `/browse?owned=0`, a bookmark, a "DVDs on Shelf A"
URL you shared with someone — and the numbers beside each filter option were the
whole library's, not the filtered view's. Touch any filter and they all jumped to
different values you had not asked for, and a media type that no longer had
matches vanished from the list entirely. Nothing was wrong with the items you saw;
the counts above them were simply answering a different question.

They now answer the same question on the first paint that they have always
answered afterwards: how many items you would get if you picked that option.

### Fixed

- **Browse filter counts no longer change the moment you touch a filter.** The
  Collection page counted every option against your whole library when it first
  loaded, then switched to counting against your current filters as soon as you
  used one. On a filtered URL those are different numbers, so the dropdowns
  rewrote themselves on the first interaction — locations losing their counts,
  types disappearing, the total more than doubling. Both renders now come from
  one shared calculation, so they cannot disagree.
- **A filtered Collection page could contradict itself.** Because the total was
  filtered while the per-option counts were not, `All Types (88)` could sit
  directly above `Book (90)` in the same dropdown — an "All" claiming fewer items
  than one of its own entries.
- **A very long search term in a `/browse?q=` URL is now capped** at 200
  characters, the same cap `/api/search` has always applied, so a pathological
  link cannot drag the page down.

### Changed

- **Counts next to each filter option describe what selecting it would give
  you**, which means an option can legitimately show more items than the grid
  below it: with a type filter active, `All Types` counts what you would see
  *without* that filter. This was already how the page behaved after any
  interaction; it is now also how it looks when it first loads.

## [0.17.1] - 2026-08-24

If you scanned a DVD or a video game, Shelf filed the barcode's title and
nothing else — no synopsis, no year, no cover — and it did that even when you
had entered valid TMDb and IGDB credentials. It looked like one bug. It was
four, stacked on the same path, and each one on its own was enough to produce
the same empty-looking result.

The setup instructions told you to copy TMDb's "API Key (v3 auth)", which is the
only credential Shelf never accepted; when TMDb rejected it, the code could not
tell "wrong key" from "no such film", so it filed the bare title and said
nothing. The barcode databases return retail shelf titles, not film titles —
`Goodfellas [DVD]  Feature Thriller Drama  Action  Suspense …` — and that whole
string was sent to TMDb as a search query, matching nothing. Any poster that
did come back was blocked before it could download. And the **Test key** button
confirmed all of this was fine.

This release repairs all four, and closes a credential that was being written
to your container log.

### Fixed

- **DVD and video-game scans file a synopsis, year and cover again.** Barcode
  titles are now cleaned of format tags, platform suffixes and edition noise
  before they reach TMDb or IGDB, and if the cleaned title finds nothing Shelf
  tries progressively shorter versions of it until a provider answers — which is
  what recovers a title buried under a retail category list. TMDb posters
  download. Either kind of TMDb credential authenticates.

  It will stop short rather than guess: Shelf will not shorten a title down to a
  single short word, because a one-word search does not come back empty, it
  comes back with a *different* film. When nothing matches, the item is still
  added with its title so the scan is never lost — you can then use **Retry
  ISBN** or **Search by Title** on the item.

  **Items you already scanned are not rewritten.** Anything filed as a bare
  title stays as it is; delete and re-scan it to pick up the metadata.
  ([#36](https://github.com/dgahagan/shelf/issues/36))

- **Changing your IGDB credentials takes effect immediately.** The cached Twitch
  token was not tied to the credentials it was issued for, so after you pasted a
  new Client ID or Secret in Settings, Shelf kept using the old token — and kept
  failing — for up to an hour.

### Changed

- **TMDb accepts either credential type, and the setup docs now say so.** Paste
  the 32-character **API Key (v3 auth)** or the long **API Read Access Token
  (v4 auth)**; Shelf detects which one you gave it. Previously only the v4 token
  worked, while the documentation asked for the v3 key.

- **Settings → TMDb → Test key now fails for a key that cannot work.** It used
  to authenticate differently from every real lookup, so it could report success
  for a credential that returned nothing on an actual scan. It now makes exactly
  the request a scan makes. If it starts failing for you after this upgrade, the
  key was never working — that is the bug being reported, not a new one.

### Security

- **Credentials are no longer written to the container log.** Your Twitch client
  secret was logged in full on every IGDB token refresh, and your TMDb key on
  every **Test key** click, because both travelled as URL query parameters and
  every outbound request URL is logged. The Twitch credentials now travel in the
  request body, and a filter blanks the value of any credential-named query
  parameter before the line is written — a second layer, because TMDb's v3
  authentication requires its key in the query string and cannot avoid it.

  Exploiting this needed access to your logs, so the exposure is limited to
  wherever those logs went. If you have shipped container logs anywhere off the
  host, or shared them when reporting a bug, **rotate your Twitch client secret
  and your TMDb key.**

## [0.17.0] - 2026-08-24

Some bugs are one mistake. Others are the same mistake, again, because the thing
you had to remember was written down in four places and you only changed three.
Shelf had three of those: the Browse filter set, the shape of an item as it gets
saved, and the version stamp on the offline cache. Between them they account for
most of the bugs reported since 0.7 — a filter that quietly stops narrowing, a
new field that saves from one screen but not another, a stylesheet the browser
refuses to let go of.

This release makes each of those a single place, with a check that fails if a
second copy appears. Most of it is invisible. The visible part is seven layout
fixes, a filter you could not clear, and a CSV import that was quietly
duplicating part of your library every time you used it.

### Fixed

- **The Settings page fits on a phone.** At a 390px-wide screen it was rendering
  519px wide on General and 640px on Data, so every tab scrolled sideways. Seven
  layout defects are fixed in total — the reported one plus six nobody had
  reported yet, across Settings, Browse, the item editor, Store Mode and the tag
  editor. Browser tests now measure every page at five widths (320, 390, 430,
  640 and 768) and fail on anything that overflows or squeezes a text column
  below 80 pixels, so this class of bug cannot ship again unnoticed.
  ([#35](https://github.com/dgahagan/shelf/issues/35))

- **Importing a CSV no longer duplicates items that have no ISBN.** The
  duplicate check only ran for rows *with* an ISBN, so every video game, DVD and
  ISBN-less book was re-added on every import — exporting your library and
  importing it straight back added a second copy of every one of them. Rows
  without an ISBN are now matched on title, author and media type, ignoring case
  and surrounding spaces.

  It deliberately only matches other rows that also lack an ISBN: a CSV row with
  no ISBN will **not** be treated as a duplicate of an edition you own that does
  have one, because those are genuinely different copies and silently merging
  them would lose one.

- **Filtering Browse by language alone now offers a way to clear it.** The
  "clear filters" control checked eight of the nine filters, and language was
  the one it missed — so a language-only filter left you with no way out but
  editing the URL.

- **Clearing filters no longer resets your grid/list choice.** Switching back
  to your preferred view after every clear was not a preference change you asked
  for.

### Changed

- **Browsers pick up a new stylesheet without being told twice.** Store Mode
  caches the app shell for offline use, keyed by a version stamp that a human
  had to remember to bump. Forgetting meant returning browsers kept serving an
  old stylesheet indefinitely — a hard refresh would not shift it. The stamp is
  now derived from the cached files themselves, so changing any of them changes
  it automatically.

  **On first launch after upgrading, Store Mode will re-download its offline
  files once.** That is the fix working; it settles immediately after.

- **The insurance valuation report and CSV export are unchanged**, and no
  database migration ships with this release. Existing data is untouched.

### Added

- **A responsive-layout check, a service-worker version check, and test-suite
  checks** now run on every change, alongside the existing security, CSRF and
  Alpine checks. Continuous integration runs the full browser suite too, which
  it previously skipped.

### Internal

Not user-visible, but it is what the release is mostly made of:

- The Browse filter set is declared once and drives the search query, every
  dropdown's cross-filter counts, the page templates and the browser code from
  that one declaration. The initial page load still carries its own copy —
  tracked in [#37](https://github.com/dgahagan/shelf/issues/37).
- Every path that creates an item — scanning, manual entry, CSV import, photo
  intake, Hardcover and Audiobookshelf sync, the offline queue, archive
  restore — now goes through one function that reads the item's shape from the
  database rather than carrying its own copy of it.
- The largest source file was split from 2,481 lines into four by feature area,
  and the Settings page from one 1,517-line template into one per tab.

## [0.16.3] - 2026-08-24

Nothing on screen was broken, which is most of why this was worth fixing. Every
load of the Settings page left an uncaught error behind in the browser's
console — invisible unless you went looking for it, and there on every single
load. A console that always has an error in it is a console nobody reads, so
the next error, the one that actually means something, arrives already
camouflaged. 0.16.2 cleared three of these off Photo Intake. This release
clears the last one Shelf was producing, and adds two guards so the next one
cannot sit there unnoticed for weeks.

### Fixed

- **The Settings page no longer throws on load.** Its archive-import summary
  carried the same guard shape that made `/intake` throw before 0.16.2's fix:
  Alpine's CSP build evaluates both sides of `&&` before applying it, so a
  guard meant to check whether an import result existed dereferenced it
  regardless, and threw when it didn't. This was the last such guard the new
  lint can see. The class is now closed twice over — a lint rejects the unsafe
  form before it can be committed, and the automated browser tests now fail
  when a page leaves an uncaught error behind.

  Nothing about what the page *shows* changes: the import report still lists
  what was imported, updated and skipped, and still counts and names any errors
  the archive produced. The error was thrown while rendering a summary that
  rendered correctly anyway.

## [0.16.2] - 2026-08-23

Photo Intake's review list is where you check what the vision model thought it
saw before any of it reaches your library — and the title, the one thing you
most need to read, was the narrowest control in the row. It was narrowest of
all on the rows carrying the **recognized** badge: the badge that means *this
one was identified from the cover, not read, so double-check it*. On a phone a
34-character title showed nine characters. Underneath, the row's mobile layout
had never been declared at all, so which controls shared a line depended on
whether the optional badge happened to be there, and any change that helped one
row shape quietly hurt the other. This release declares that layout and gives
the title the room it needed.

### Fixed

- **Photo Intake review rows give the title room to be read.** Below 768px the
  row is now three declared lines — checkbox, title and badge; author and ISBN;
  the media type — identical for both row shapes, and from 768px up it stays
  the single line it always was. A plain row's title goes from 87px to 287px on
  a phone and from 161px to 225px on the desktop row; a `recognized` row's goes
  from 88px to 152px on desktop and holds its 214px on a phone, where it can no
  longer collapse because a neighbouring width changed. Tablet-portrait and
  narrow desktop windows gain the most: at 680px a `recognized` title went from
  19 readable characters to 34 — the whole title.

- **`/intake` no longer throws on every page load.** Three template guards
  dereferenced the import result before it existed. Alpine's CSP build
  evaluates both sides of `&&` before applying the operator, so the guard
  itself threw — three console errors per load, any one of which could have
  masked a real one.

### Changed

- **Long author names now truncate on desktop, where they did not before.**
  The author field narrows from 192px to 128px at 768px and up — the cost of
  the space the title gained. The field is still fully editable and scrollable,
  and the author is not what the `recognized` badge asks you to verify. Below
  768px the field is flexible and lands near 150px, wider than the fixed width
  it would otherwise have had.

## [0.16.1] - 2026-08-23

Photo Intake uploaded your photo at full resolution, and a modern phone camera
makes that a problem. A 50-megapixel still is around 8 MB and 8160 pixels down
its long edge, while the model resizes every image on arrival and never looks
at more than a fraction of those pixels — so each send pushed roughly eleven
times more data up the link than the model would ever see. Worse, Anthropic
refuses any image over 8000 pixels on a side, which is exactly what a recent
flagship phone produces, and all Shelf could tell you was "try again". This
release shrinks the photo in your browser before it is sent, and makes a
provider's refusal say what it actually objected to.

### Fixed

- **A 50-megapixel phone photo sends and reads.** Photos over the model's
  limit were rejected outright; the same photo now uploads at a fraction of the
  size and comes back with the books in it. A real 6144 × 8160 Pixel still went
  from a 7.6 MB upload that Anthropic refused to a 0.66 MB upload that read all
  eleven spines.

- **Choosing a different photo while one is being analyzed no longer mixes them
  up.** Previously the first photo's results could land under the second
  photo's preview. The photo-choosing buttons now grey out for the duration of
  an analysis, and a replacement arriving by any other route discards the
  older analysis rather than displaying it.

- **Send as-is and Send high-res pressed together send one photo, not two.**
  Tapping both in quick succession used to start two analyses and bill for
  both.

### Changed

- **Send as-is uploads a resized copy, at exactly the size the model ingests.**
  Nothing larger goes up once Shelf knows what the provider will accept. This
  also applies to a plain **Read Photo** when the photo is over the model's
  ingest size but not by enough to trigger the tiling offer — a band where the
  old behaviour uploaded the full file for no benefit. **Send high-res
  (tiled) is unchanged**: tiles are still cropped at full resolution, which is
  the entire point of that option. If the plan step fails for any reason, the
  original file is sent exactly as before, so a hiccup there is never fatal.

- **The "what the AI will see" preview is now drawn the same way the upload
  is.** Same resizing pass, same dimensions — so the image you judge for
  legibility before paying for tiles is the image that gets sent, rather than a
  rougher approximation of it. The resize now steps down in halves rather than
  in one jump, which is gentler on small text like spine lettering. The
  provider may still adjust the image slightly on its own ingest.

- **For Ollama and OpenAI-compatible providers, the Image size setting now sets
  the upload size too.** It previously only decided when the high-res tiling
  offer appeared. If you run a model that reads larger images natively, raise
  it — at the 1024 default a 50-megapixel photo uploads at about 0.2 MB, at
  3000 it uploads at about 1.1 MB. Anthropic and OpenAI have fixed ingest sizes
  of their own, so nothing changes there.

- **A provider that rejects a photo now says why.** Instead of "Anthropic API
  error (HTTP 400) — try again", the error line carries the provider's own
  explanation — for example that the image exceeds 8000 pixels on a side.
  Transient failures (rate limits, timeouts, server errors) keep the old "try
  again" wording, because retrying is genuinely the right response to those.
  The same applies to OpenAI-compatible endpoints. Ollama is unchanged: its
  error responses are not shaped like the cloud APIs' and its common failure
  already had a tailored message.

- **Each analysis logs what it uploaded** — one line per request naming every
  part's filename, type and size, visible on the Logs page. Useful for
  confirming a photo really was resized before it went out, or for sizing a
  tiled send.

There is still no way to cancel an analysis once it has started, and no rotate
or crop step before sending — a stale analysis is discarded rather than
aborted, and tiling is the crop.

## [0.16.0] - 2026-08-22

Photo Intake only ever offered a plain file picker. On a desktop that meant a
webcam was unreachable — photograph the shelf on your phone, transfer the
file, browse for it. On a phone it was worse in the other direction: the
picker forced the camera open and hid the photo library, so a shelf photo you
had already taken could not be chosen, and a fresh shot gave no hint whether
the spines were legible until after the analysis had been paid for. This
release gives intake an explicit capture step on both.

### Added

- **Take photo and Choose photo** replace the single picker on the Photo
  Intake page. On a phone, **Take photo** opens the native camera app and
  hands back a full-resolution still — deliberately not an in-page
  viewfinder, because a live video frame is typically 1080p while the camera's
  own still is 12 MP or more, and spine legibility (and whether the high-res
  tiling offer appears at all) depends on those pixels. **Choose photo** opens
  the phone's photo library or a file on disk, which the old picker hid on
  most phones.

- **A webcam viewfinder on desktop.** Where there is no camera app to hand off
  to, **Take photo** opens an in-page viewfinder with **Capture** and
  **Cancel**, so a laptop pointed at a shelf is now a usable path. It needs the
  HTTPS certificate trusted (as the barcode scanner already does); on a desktop
  with no camera the button says "No camera found. Use Choose photo instead."
  and leaves you on the picker rather than dead-ending.

- **A low-resolution advisory.** When a photo is small rather than oversized —
  a webcam frame, or a library photo a messaging app re-compressed — the plan
  step says "This photo may be too small to read" and offers **Take another
  photo** / **Choose another photo**. It is advisory, not a gate: Read Photo
  stays enabled and the analysis runs if you say so. A native phone photo
  essentially never trips it. The advisory and the high-res tiling offer are
  mutually exclusive by construction — one says the photo is too big to send
  as-is, the other that it is too small to read well; you never see both.

### Changed

- **On phones, the picker no longer forces the camera.** The old single input
  carried a hint most mobile browsers read as "camera only, no library"; that
  hint is gone, so **Choose photo** now reaches the photo roll. Taking a shot
  is one tap further — it has its own button.

Nothing is stored server-side by either path: captured and chosen photos are
analyzed in memory, exactly as uploads were before. Gathering several frames of
a long shelf into one analysis is not part of this release.

## [0.15.0] - 2026-08-22

Photo Intake could only read spines. That left out the books least likely to
have a usable barcode in the first place — kids' picture books too thin to
print a spine, vintage manuals, anything you would naturally lay flat — and it
threw away the ISBN printed on a back cover even when the photo showed it
plainly. This release makes a face-up photo a first-class input: covers are
recognized as well as read, a printed ISBN gets the same lookup a barcode scan
does, and each candidate row carries its own media type so a DVD in the pile
stops being filed as a book.

### Added

- **Face-up covers are read.** Photograph books on a shelf, in a stack, or laid
  flat with the front cover showing. The model reads the spines it can read and
  identifies the covers it can't, so a barcode-less picture book gets a row
  where a spine photo would have given you nothing. Recognition leans on a
  printed title or byline to anchor itself, so a cover carrying no text at all
  is the hardest case and may produce no row rather than a recognized one.

- **A `recognized` marker on rows the model identified rather than read.** A
  title lifted off cover art is usually right and occasionally confidently
  wrong. The badge sits on the row itself — no hovering, no guessing which rows
  deserve a second look before you confirm.

- **Printed ISBNs reach the catalogue.** If a back cover with its barcode
  happens to be the side in frame, the ISBN beside it is read, checksum-checked
  and pre-filled into an editable field on the row. Confirming that row runs the
  same lookup a barcode scan does, so it arrives with exact-edition publisher,
  year, page count and cover art instead of a title-and-author guess.

  A misread ISBN is dropped to blank rather than guessed at, and a valid ISBN
  naming a visibly different book is rejected rather than trusted — a wrong
  edition costs you nothing, a wrong book costs you a catalogue entry.

- **A media type per candidate row.** Set a row to DVD / Blu-ray, video game or
  any other type before confirming. The duplicate check is scoped to that type,
  so the ebook of *The Hobbit* no longer blocks the hardback, and non-book rows
  skip the book-cover lookup entirely. Discs and games are classified, not
  looked up: setting a row to DVD keeps it out of the book catalogue, but there
  is no TMDb or IGDB lookup from an intake row yet.

- **The Done panel says which rows found nothing.** A row that imported on title
  alone now reads `— no metadata found, added title only`, so a thin result is
  visible at import time rather than discovered weeks later on the item page.

### Changed

- **The Photo Intake button now reads "Read Photo"**, not "Read Spines" — the
  page no longer does only spines.

- **Cover enrichment skips non-book rows**, in photo intake *and* in CSV import.
  An authorless DVD or video game sent through the cover pipeline could match
  the first Open Library hit for its title and acquire a novel's ISBN and cover
  art. Both paths now filter to book-ish media types before queueing. If you
  have imported discs or games by CSV with cover enrichment on, check them.

- **Cost estimates rose slightly.** The unified spine-and-cover prompt is longer
  and each returned row now carries an ISBN and a source, so the preview
  estimates more tokens per book than it used to. The estimate changed; the
  price per token did not.

### Fixed

- **A correctly-read ISBN is no longer discarded when the back cover prints an
  alternative title.** `The Hobbit or There and Back Again` on the cover against
  `The Hobbit` in the catalogue was treated as a disagreement and the ISBN
  thrown away — whether it survived came down to whether the cover printed a
  colon. The `or …` / `, or …` form is now recognized as the same book.

## [0.14.0] - 2026-08-22

The `items` table carries 36 columns. The detail page rendered 20 of them and
the edit form exposed 20 — but not the same 20, and ten were visible in
neither. The gaps were not cosmetic: the page never told you when a book
entered your catalogue, whether you actually owned it, how old its valuation
was, or that you had read it three times. This release surfaces the item
record's own facts, as distinct from the book's.

### Added

- **A record footer on the item page** — `Added 2026-03-14 · Updated
  2026-08-02 · via audiobookshelf` — in a muted line below the action buttons.
  `Source` moves here out of the metadata grid, where it never belonged: the
  grid answers "what is this book", the footer answers "what is this row".

- **A Wishlist badge** beside the title when an item is not owned. Browse has
  badged wishlist items for a while; the detail page did not, so the one page
  that shows a book in full was the one page that would not tell you whether
  you own it.

- **An as-of date on estimated values** — `$24.00 (as of 2026-03-01)` — so a
  price fetched eight months ago no longer looks like today's. Manual values
  deliberately keep their `(manual)` marker with no date: nothing records when
  you typed one, and showing a date there would assert something false.

- **Reading history.** A book you have finished more than once now shows a
  collapsed `Read 3 times` section listing every logged read with its start
  and finish dates. It renders from both places the reading-status control is
  drawn, so marking a book read updates the count in place rather than
  swapping in a section whose history vanished.

- **Series progress on the item page**, from two clearly separated sources:
  `· you own 3 of 1–4, missing #2` from your own shelves, and `· 7 in series
  (Hardcover)` when a Hardcover series record exists. They are never blended
  into one number — a local gap count and a published series length answer
  different questions, and merging them produces a figure that is true of
  neither. The series name now links to the Series page.

- **A Series # field on the edit form.** `series_position` has been writable
  through the API all along but had no input on the form — you could store a
  position and then never correct it. Fractional positions (a `#2.5` novella)
  are accepted and now render as `#2.5` rather than being truncated to `#2` on
  the item page and in Hardcover search results.

- **An admin-only Integration IDs block**, collapsed at the bottom of the item
  page: the internal id, ISBN-10, UPC, and the Audiobookshelf and Hardcover
  identifiers. Read-only and admin-only — these are sync-owned, and
  hand-editing them desynchronises an item silently. Editors and viewers do
  not see the block at all.

### Changed

- **Series that differ only in capitalisation are now one series on the Series
  page.** `Dune Saga` and `dune saga` previously produced two cards, even
  though renaming, disbanding, completeness checks and the Hardcover record
  all already treated them as the same series. The Series page was the odd one
  out; it now groups case-insensitively like everything else, displaying the
  most common spelling.

### Fixed

- **The edit form no longer erases a stored value of `0`.** Any numeric field
  holding zero rendered as an empty input, and saving the form — even after
  changing something unrelated — wrote the blank back as NULL.

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

[0.24.0]: https://github.com/dgahagan/shelf/releases/tag/v0.24.0
[0.23.0]: https://github.com/dgahagan/shelf/releases/tag/v0.23.0
[0.22.3]: https://github.com/dgahagan/shelf/releases/tag/v0.22.3
[0.22.2]: https://github.com/dgahagan/shelf/releases/tag/v0.22.2
[0.22.1]: https://github.com/dgahagan/shelf/releases/tag/v0.22.1
[0.22.0]: https://github.com/dgahagan/shelf/releases/tag/v0.22.0
[0.21.1]: https://github.com/dgahagan/shelf/releases/tag/v0.21.1
[0.21.0]: https://github.com/dgahagan/shelf/releases/tag/v0.21.0
[0.20.0]: https://github.com/dgahagan/shelf/releases/tag/v0.20.0
[0.19.0]: https://github.com/dgahagan/shelf/releases/tag/v0.19.0
[0.18.0]: https://github.com/dgahagan/shelf/releases/tag/v0.18.0
[0.17.7]: https://github.com/dgahagan/shelf/releases/tag/v0.17.7
[0.17.6]: https://github.com/dgahagan/shelf/releases/tag/v0.17.6
[0.17.5]: https://github.com/dgahagan/shelf/releases/tag/v0.17.5
[0.17.4]: https://github.com/dgahagan/shelf/releases/tag/v0.17.4
[0.17.3]: https://github.com/dgahagan/shelf/releases/tag/v0.17.3
[0.17.2]: https://github.com/dgahagan/shelf/releases/tag/v0.17.2
[0.17.1]: https://github.com/dgahagan/shelf/releases/tag/v0.17.1
[0.17.0]: https://github.com/dgahagan/shelf/releases/tag/v0.17.0
[0.16.3]: https://github.com/dgahagan/shelf/releases/tag/v0.16.3
[0.16.2]: https://github.com/dgahagan/shelf/releases/tag/v0.16.2
[0.16.1]: https://github.com/dgahagan/shelf/releases/tag/v0.16.1
[0.16.0]: https://github.com/dgahagan/shelf/releases/tag/v0.16.0
[0.15.0]: https://github.com/dgahagan/shelf/releases/tag/v0.15.0
[0.14.0]: https://github.com/dgahagan/shelf/releases/tag/v0.14.0
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
