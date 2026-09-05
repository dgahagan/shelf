# Troubleshooting

First stop for anything odd: **Logs** in the nav (admin) or
`docker compose logs -f shelf`.

Since 0.30.0 the container log no longer carries a line per outbound request.
That line held the whole request URL, and an authenticated ntfy topic keeps its
password in that URL, so the trace and the secret were the same line and both
had to go. Shelf's own log lines are unaffected; a failed lookup or
notification still names what it was talking to, by scheme and host.

## Browser says the connection isn't private

Expected on first run — Shelf's certificate is self-signed. Click through,
or fix it properly: [HTTPS & reverse proxy](https-and-reverse-proxy.md).

If you *did* trust the cert and still get the warning, the name you're using
isn't in it. Set `CERT_SAN` to include that IP/hostname, delete
`data/certs/`, restart, re-trust.

## Camera won't start / no camera button

Applies to both the barcode scanner and Photo Intake's **Take photo** button
on desktop (its in-page viewfinder), which both use `getUserMedia`:

- Must be HTTPS. `http://` or an untrusted origin on some browsers disables
  `getUserMedia` — trust the certificate, see
  [HTTPS & reverse proxy](https-and-reverse-proxy.md).
- Permission was denied once — reset it in the browser's site settings for
  your Shelf URL.
- iOS: Safari only (Chrome on iOS is Safari underneath and also works);
  in-app browsers (e.g. from a messaging app) often block the camera. Open
  in Safari proper.
- Another app/tab holds the camera — close it.
- Desktop with no camera attached: Photo Intake's **Take photo** button
  shows "No camera found. Use Choose photo instead." — use **Choose photo**.

Photo Intake's **Take photo** on a phone is different — it opens the native
camera *app* via an HTML capture input, not `getUserMedia`, so it works even
over plain `http://` and isn't affected by the in-app-browser restriction
above.

## USB scanner types nothing

Click into the barcode field first. If the scanner types characters but no
Enter, configure it to send a carriage-return suffix (every scanner manual
has a barcode for this).

## Barcode scans but "not found"

- Many pre-2007 books carry only an ISBN-10 *printed* and an EAN that isn't
  the ISBN; type the ISBN-10.
- Store-price-sticker barcodes aren't ISBNs. Peel.
- Genuinely obscure editions: use **Title search** or **Add manually**. The
  title-search result box makes the same distinctions this card does — a
  rejected key, a rate-limited provider and an unreachable one each say so,
  so an empty box there is a real miss and not a silent failure.
- A **connectivity card** is a different problem: the lookup could not reach
  the provider at all (DNS, no route, a timeout). It is not a missing record,
  and the scan is logged as `error` rather than `not_found`. Check the
  container's network before hunting for the barcode. The wording differs by
  what you scanned — an ISBN gives **"Network error during lookup — check
  connectivity"**, a UPC **"Metadata lookup failed — check connectivity"**.
  Every book source reaches that card: Hardcover, Google Books and the DNB
  catalog used to turn an unreachable network into a plain "not found", so a
  Shelf that was simply offline reported the books as unknown. The SBN
  catalog, which answers Italian ISBNs, reaches it too.
- **Italian books quietly stop enriching** — titles still file, but with no
  author, publisher or year, and the lookup falls back to Open Library. The
  SBN endpoint is undocumented and reverse-engineered, so ICCU can move or
  retire it without notice. Nothing is broken and nothing is lost: an
  unreachable or unreadable SBN answers "not found" and the ordinary cascade
  takes over. The fix is a Shelf update, not anything you can configure.
- If several barcodes in a row come back empty, the provider's daily quota
  may be spent rather than the records missing — see [A scan comes back empty
  and the log says a provider asked for a long
  wait](#a-scan-comes-back-empty-and-the-log-says-a-provider-asked-for-a-long-wait).
- A "Not found" card naming a rejected Hardcover or Google Books key means
  the credential, not the barcode, needs fixing — see [A scan added only a
  title](#a-scan-added-only-a-title).
- If *every* provider went quiet at once and the cards say no key is
  configured, the keys are probably intact and unreadable — see [Every stored
  API key stopped working at
  once](#every-stored-api-key-stopped-working-at-once).

## Metadata came back wrong or sparse

Sources disagree. **Edit** the record; **Find cover** for another image;
**Fetch synopsis** if the description is missing. For German books, make
sure you're on 0.11+ (DNB source).

**A German title or author shows small boxes, and a search for the full
title finds nothing.** DNB marks a title's leading article and a name's
particle with invisible non-sorting characters, and before 0.27.2 they were
stored as part of the text. New lookups on 0.27.2+ store clean text. Rows
stored earlier are not rewritten: open **Edit** and retype the title or
author to clear them. The same release stops a translation listing its
author twice.

**An Italian title begins `L' ` and a search for it finds nothing.** SBN
catalogues an elided Italian article with a space after the apostrophe, so
*L'enigma del faraone* is stored as `L' enigma del faraone` and a Browse
search for `L'enigma` misses it. Search on any later word — `enigma del
faraone` — or retype the title in **Edit**. Shelf files what the catalogue
holds rather than second-guessing it; normalising this is a change to the
shared bibliographic normaliser and will come in its own release.

**An Italian classic lists its author in Latin.** SBN's authority headings use
the Latin form, so *Omero* files as `Homerus` and *Tucidide* as `Thucydides`.
This is deliberate, and DNB makes the same trade: the authority heading is the
only field that reliably holds the author rather than the translator or the
illustrator. Retype it in **Edit** if you prefer the vernacular name.

**An Italian book filed with no language at all.** Where SBN records two
languages for one edition — a Greek text with an Italian translation, say —
Shelf files neither rather than choosing one. Set it in **Edit**.

**A 979-12 book has no cover.** The Amazon cover fallback takes only ISBNs
beginning 978, so a 979-12 book can be covered only by Open Library. **Find
cover** searches by title and often turns one up.

**DVDs and games that filed a bare title — no synopsis, no year, no cover —
were a bug, not a missing key.** TMDb rejected the credential type the setup
docs told you to paste, and retail barcode titles were sent to the provider
verbatim. Both are fixed; the affected items are not rewritten in place, so
delete them and re-scan. Check Settings → Integrations → TMDb → **Test key**
first: it now fails for a key that cannot work, where it used to pass.

Setting a preferred language
(Settings → Library → Collection) ranks matching editions first in title
search.

## Covers missing after an import

Imports fetch covers in the background; give it a few minutes on a big
batch. Then Settings → Data → Maintenance → **Retry missing covers**. Items
with no ISBN (manual adds, discs, games without IGDB) need a manual cover
or **Find cover**.

## A scan added only a title

The card tells you which of these it was, because the fix is different each
time:

- **"Add a TMDb API key in Settings → Integrations…"** — no credential is
  configured. Add one; the item is already filed and will fill in on a
  **Retry cover** / **Fetch synopsis**, or delete and re-scan.
- **"TMDb rejected the configured key."** — a credential is set and the
  provider refused it. Settings → Integrations → **Test key**. Pasting the
  wrong one of TMDb's two credential types is the usual cause.
- **"a metadata provider is rate-limiting us right now. Re-scan later to fill
  it in."** — nothing is broken. The provider answered with HTTP 429. Wait and
  re-scan; the item is already filed. No provider is named because a book
  lookup consults up to four and any subset can be starved at once.
- **"the title names console hardware, so no film or game lookup was
  attempted."** — nothing to fix. The shortened title Shelf would have
  searched on is just `PlayStation` or `Nintendo`, or a brand name such as
  `Logitech`, and a film database answers that with a confident match for an
  unrelated film, so Shelf files the title it read and asks nobody. Correct the type or the title on the item page if
  it read the title wrong. This also covers a hardware title that carries
  `DVD`, `CD` or `CD-ROM` — the tag is read as a shelf-listing artifact, not as
  the item's type. This one is decided by the *title*; the next is decided by
  the *format*.
- **"Shelf has no metadata source for this format yet."** — nothing to fix,
  and nothing to configure. The format has no provider wired up, so no lookup
  was attempted. CDs are the case today; the disc is filed under its barcode
  title. Since 0.26.0 a music CD reaches this on **Auto** — an audio tag in the
  retail title or a `Music CDs` category is enough, so you no longer have to
  pick CD from the dropdown to get the quiet answer instead of a guessed film.
- **"no TMDb match for this barcode."** — nothing to fix. The provider
  genuinely has no record for that title; **Find cover** or **Edit** it by
  hand.

**Games make the same distinctions.** A rejected Twitch credential used to be
indistinguishable from a genuine miss — IGDB's search returned an empty list
for both — so the card said "no IGDB match" either way. It now says **"IGDB
rejected the configured key"** when the token exchange is refused, and the
server log carries a WARNING naming the HTTP status. Exhausting the Twitch
request budget at that same token exchange gives the rate-limit line rather
than a miss.

A credential IGDB rejects on the *game search* request — as opposed to the
token exchange above — is now told apart too, and the cached Twitch token is
thrown away when it happens, so fixing the app in the Twitch console takes
effect on your next scan instead of after a restart.

**Books make the same distinction too, on a different card.** A book scan
tries up to four sources in order, so a rejected Hardcover or Google Books
key has nowhere left to fall back to for even a bare title — the barcode
renders **"Not found — add manually below"** instead of an added item, with
**"Hardcover rejected the configured key"** or **"Google Books rejected the
configured key"** underneath. Settings → Integrations → **Test key** is the
fix either way. Google Books answers a bad key with HTTP **400**, not 401 or
403 — a status that reads like a plain missing record in a log — so the scan
card, not the log, is where to check first.

## Find cover finds nothing for a DVD or a game

**Find cover** asks TMDb for a disc and IGDB for a game, so an empty result
usually means one of three things:

- **No credential.** The picker says so outright — *"DVD cover search needs a
  TMDb API key"*, or the equivalent for IGDB's Client ID and Secret. Add it in
  Settings → Integrations.
- **A credential that is present but rejected.** The picker says so by name —
  *"TMDb rejected the configured key"*, with a link to Settings. (It used to
  fall back to *"No covers found for this title."*, which was misleading.)
  IGDB needs *both* the Client ID and the Client Secret, and a Twitch secret
  that has been rotated fails the token exchange without any other symptom.
- **The provider is rate-limiting us.** *"IGDB is rate-limiting us right now"* —
  wait and try again; nothing is wrong with your key or the item.
- **Shelf could not reach the provider.** *"Could not reach TMDb — check
  connectivity"*: DNS, no route, or a timeout. Not a missing record.
- **The game's platform is narrowing the search.** A game searches IGDB with
  the platform recorded on the item, so a title IGDB does not list for that
  platform returns nothing — and typing a different query does not lift the
  filter, because the platform comes from the item, not the search box. Clear
  or correct the platform with **Edit** if the search should be wider.

For a book the picker is unchanged: it always combines the item's stored
author with your query, so a wrong author on the record finds nothing whatever
you type.

## Photo Intake finds nothing / garbage

- Is a provider configured and does its **Test** pass?
- Local model too small for the job: try a cloud model once to compare.
- Accept the **tiling** offer for high-resolution photos.
- An error starting "Anthropic rejected the request" or "OpenAI API
  rejected the request" quotes the provider's own reason (trimmed to a
  sentence or so) — the usual fixes are the high-res offer or a smaller
  photo; an error ending in "try again" is a transient one worth retrying.
- **Logs** shows an `Intake analyze:` line for every send, naming each
  uploaded part's filename, type and size — a quick way to confirm a photo
  really was resized before it went out (a resized as-is send appears as
  `photo.jpg`, an unmodified one keeps the original filename, a tiled send
  lists one `tile-N.jpg` per tile).
- Glare, angle, distance — see [Photo Intake](user-guide/photo-intake.md#getting-good-results).

## Store Mode isn't offline

Service workers need a trusted origin. Trust the certificate on the phone or
use a real one; `localhost` always works. After fixing trust, open the store
page once online so it can install.

## Container starts then exits / restarts

Read the log. Common causes:

- **Invalid `CERT_SAN`** — must be comma-separated `DNS:name` / `IP:addr`
  entries only.
- **Permission denied on `/data`** — the container runs as UID 1000; on
  SELinux hosts add `:z` to the volume; elsewhere `chown -R 1000:1000 data`.
- **Port in use** — change the host side of the mapping.

A crash-loop right after an *upgrade* on an old database was a known bug
fixed in 0.8.1 — upgrade to that or later and it heals itself.

## Locked out

Another admin: Settings → Users → reset password. Only admin? Stop the
container and run, from the host:

```bash
sqlite3 data/shelf.db ".schema users"
sqlite3 data/shelf.db "SELECT id, username, role FROM users;"
```

Passwords are bcrypt hashes. Generate one —
`python3 -c "import bcrypt; print(bcrypt.hashpw(b'newpass', bcrypt.gensalt()).decode())"`
— and `UPDATE` your user's hash column with it. Restart. (Take a copy of
`shelf.db` first.)

## Every stored API key stopped working at once

Since 0.30.0 this is a diagnosable failure rather than a silent one. Stored
credentials are encrypted with `data/encryption.key`; if that file is replaced,
lost, or restored from a different instance, the ciphertext no longer opens.
Shelf now logs one warning per setting naming the setting —

```
settings[tmdb_api_key]: stored ciphertext does not open under the current
encryption key — re-enter the credential in Settings
```

— and the credential reads as **unset**, so the scan card says no key is
configured rather than that the provider rejected one. Re-enter each affected
key in Settings → Integrations; nothing else is lost, and no other data is
affected.

Before 0.30.0 the raw ciphertext was sent to the provider as though it were
your key, so this looked exactly like a revoked credential with nothing in the
log to search for. If several providers failed on the same day and none of the
keys had actually been revoked, that was this.

Keep `encryption.key` with any copy of `data/` you intend to restore from — see
[Upgrading & backups](upgrading-and-backups.md).

## Overdue reminders never arrive

- **Send test** on the Lending card — if that fails, the URL or format is
  wrong (ntfy needs the full topic URL, e.g. `https://ntfy.sh/my-topic`).
- The digest goes out once a day at most and only when something is
  overdue; check "Overdue after" isn't 0.

## Hardcover / Audiobookshelf sync does nothing

- **Test** the connection on its card.
- ABS: make sure at least one library is selected.
- Both run on an interval read every 5 minutes; "Sync now" is immediate.
- ABS: a summary of *0 added, 0 updated, N unchanged* means the sync ran and
  found nothing new — that is success, not a stall.
- Env-var overrides (`HARDCOVER_TOKEN`, `ABS_TOKEN`) beat what's stored —
  if you changed the key in Settings and nothing changed, check your `.env`.

## The Listen / Read on Audiobookshelf link goes nowhere

The item page shows the button, but clicking it lands on a page that will not
load — a timeout, or a name your browser cannot resolve.

Shelf is reaching Audiobookshelf at an address your browser cannot. That is
normal when Shelf runs in Docker and talks to ABS over the container network
(`http://audiobookshelf:80`) or a LAN hostname, while you open Audiobookshelf
through a reverse proxy on a public name.

Fill in **Browser URL** under Settings → Integrations → Audiobookshelf Sync with
the address *you* type into your browser. It is used for these links and nothing
else — sync, library discovery and cleanup keep using the Audiobookshelf URL
above it. `ABS_PUBLIC_URL` does the same from the environment.

If the field looks empty but the links already work, an `ABS_PUBLIC_URL` in your
environment is driving them: an env-only value renders the field blank while
staying in force.

## A scan comes back empty and the log says a provider asked for a long wait

A barcode scan returns quickly with no metadata ("Not found"), or a cover
search finds nothing, even for an item the provider clearly has.

Check **Logs** (Settings → Logs, or the container log) for a line like:

```
outbound: api.upcitemdb.com returned HTTP 429 asking for a 4275s wait, beyond the 30s ceiling — not retrying
```

The host in that line names the provider whose quota is spent. Trial tiers
are metered per day — UPC Item DB's is 100 lookups/day, Google Books has a
per-day project quota — and once one is spent, the provider answers every
request with the same 429 until it resets. Shelf does not wait one out: it
gives up at once so a scan isn't held for about a minute per lookup.

Wait for the daily reset, then **Retry Missing Covers** (Settings) or
re-scan the barcode. For a provider with a paid tier, add a key in Settings
to raise or remove the limit.

The scan card says so too now: a **Not found** card whose lookup was
rate-limited carries "A metadata source is rate-limiting us right now — this
may not be a genuine miss." The log line still names *which* host, which the
card deliberately does not — a book lookup consults up to four sources and any
subset can be starved at once.

## Rate-limited (HTTP 429) in the UI

This one is Shelf's own inbound limit, not a provider's. Per-IP limits
protect `/api/`, `/share/`, `/login` and `/setup`. Behind a reverse proxy
without `SHELF_TRUST_PROXY=1`, every client looks like the proxy's IP and
shares one bucket — set the variable.

## Still stuck

[Open an issue](https://github.com/dgahagan/shelf/issues/new/choose) with
your version, browser/device, and the relevant log lines.
