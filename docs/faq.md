# FAQ

**Does Shelf need an internet connection?**
For metadata and covers, yes — lookups go to Open Library and friends. The
catalog itself, Browse, Stats, lending and Store Mode's scan verdicts all work
offline. Nothing phones home.

**Is my data sent anywhere?**
Only the ISBN/UPC being looked up goes to metadata providers. Photo Intake
sends the photo to whichever vision provider you configured — choose Ollama
for fully local. Share links are served by *your* server.

**Do I need any API keys?**
No. Books, covers, DVD titles (via UPC Item DB) and everything else core work
keyless. Keys unlock extras: Hardcover (sync, series checks), IGDB (games),
TMDb (discs), ISBNdb (valuation), a vision provider (Photo Intake). The
keyless tiers are metered per day, though — a heavy cataloguing session can
exhaust one, after which scans come back empty until it resets. A key raises
or removes that limit. Google Books is the one source that accepts a key
without needing one: it works keyless, and a key of your own only buys you a
quota that nobody else is sharing.

The scan card tells you which of these happened rather than making you guess:
no key configured, a key the provider rejected, a provider that is
rate-limiting you right now, a format Shelf has no metadata source for (CDs,
today), or a genuine miss. See
[Troubleshooting](troubleshooting.md#a-scan-comes-back-empty-and-the-log-says-a-provider-asked-for-a-long-wait).

**Why HTTPS with a self-signed certificate?**
Phone cameras and offline mode both require a secure origin, so Shelf must
be HTTPS; without a domain, self-signed is the only way to do that out of
the box. (The one exception is Photo Intake's **Take photo** button on a
phone, which opens the native camera app rather than using `getUserMedia`
and works over plain `http://` — but trust the cert for everything else.)
You can trust the cert on your devices or front it with a real one —
see [HTTPS & reverse proxy](https-and-reverse-proxy.md).

**Can I use a USB barcode scanner?**
Yes — any scanner that acts as a keyboard and sends Enter. No setup.

**Does camera scanning work on iPhone?**
Yes, since 0.10.0 (iOS Safari uses a ZXing decoder). It needs HTTPS and a
camera permission.

**What barcodes are supported?**
EAN-13 (ISBN-13 and UPC-like), EAN-8, UPC-A, UPC-E. ISBN-10s can be typed.

**What about books with no barcode?**
Title search (Open Library), manual add, or Photo Intake of the cover
face-up — the model recognizes the cover, and reads the printed ISBN if the
back cover is showing.

**Can several people use it?**
Yes: admin / editor / viewer roles on one shared library. Per-user reading
tracking and per-household libraries are planned.

**Can I import from Goodreads / StoryGraph / LibraryThing / Libib?**
Goodreads and StoryGraph today (upload the export as-is). LibraryThing and
Libib are planned. Anything else: CSV with at least a `title` column.

**How do I move Shelf to another machine?**
Copy the `data/` directory — or export a portable archive and import it on
the new instance. See [Upgrading & backups](upgrading-and-backups.md).

**How do I back up?**
Copy `data/`, or Settings → Data → Backup & Restore (optionally
passphrase-encrypted). Portable archive for a credential-free copy with
covers.

**Where are the covers stored?**
`data/covers/`. They're downloaded once and served locally.

**Why did a book get the wrong cover / edition?**
Metadata sources key on ISBN but sometimes collapse editions. On the item
page use **Find cover** to pick another — it works even when the item
already has a cover, not just a blank one — or **Edit** to fix the record.

**I removed a cover by mistake — can I get it back?**
Only indirectly, and only for a book added in the last 48 hours: a
container restart re-queues cover-less recent books for automatic lookup,
which would refetch one. Older items, or a cover you don't want refetched,
need **Find cover** or **Upload** to set a new one by hand.

**Is the valuation a resale value?**
No — ISBNdb list price, i.e. replacement cost. Right for insurance, not for
selling.

**Can I change the currency?**
Yes, Settings → Library → Collection. Formatting only; no conversion.

**Is there an API?**
Not a documented, token-authenticated one yet — the routes exist for the UI
(FastAPI) but require the session cookie. A stable `/api/v1` with personal
access tokens is on the roadmap.

**Kobo / Kindle / OPDS / reading ebooks in the browser?**
Out of scope. Shelf catalogs physical media (and links to your
Audiobookshelf for digital); ebook servers do the rest better.

**What's the license?**
AGPL-3.0. Free to use, self-host and modify; if you run a modified version as
a network service you must publish your changes under the same license.

**Something else?**
[Troubleshooting](troubleshooting.md), then
[open an issue](https://github.com/dgahagan/shelf/issues).
