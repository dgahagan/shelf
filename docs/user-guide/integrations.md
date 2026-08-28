# Integrations

Shelf works fully with no accounts anywhere. Each integration below adds
something specific. All are configured under Settings → Integrations, each
card has an inline setup guide, and every key is stored encrypted and shown
write-only.

## Hardcover

[hardcover.app](https://hardcover.app) — free, community-run Goodreads
alternative with an excellent data model.

**Adds:** bidirectional reading-status sync, richer metadata and synopses on
lookup, series completeness checks and one-click "add missing volumes to
wishlist", import of your Hardcover library, export of your Shelf library to
Hardcover, and the **Discover** tab (recommendations).

**Setup:** Hardcover → Settings → API → copy the token → paste into the
Hardcover card. Choose a sync schedule for reading status; run **Import
library** once if you have history there.

## Audiobookshelf

[audiobookshelf.org](https://www.audiobookshelf.org) — self-hosted
audiobook/podcast server.

**Adds:** sync of selected ABS libraries into Shelf as audiobook / eBook
items, cross-linking with physical copies (the item page shows both and
deep-links into ABS), periodic re-sync.

**Setup:** in ABS, Settings → Users → your user → API Token. Enter the ABS
URL and token, **Test**, then choose which libraries to include. Set an
interval for automatic sync or run it by hand. Items removed from ABS can be
cleaned up from the same card.

A scan that comes back thin tells you **on the card** which of five things
happened: no credential configured, a credential the provider rejected, a
provider that is rate-limiting you right now, a format Shelf has no metadata
source for, or a provider with no match. IGDB makes the same distinctions as
TMDb — a rejected Twitch credential says so rather than reading as a miss.
See [Troubleshooting](../troubleshooting.md#a-scan-added-only-a-title).

## IGDB (video games)

[IGDB](https://www.igdb.com) via Twitch developer credentials — free.

**Adds:** video-game metadata, cover art, platform and series on UPC scan;
title search for retro cartridges.

**Setup:** [dev.twitch.tv/console](https://dev.twitch.tv/console) → Register
Your Application (category "Application Integration", any redirect URL) →
copy Client ID and generate a Client Secret → paste both.

## TMDb (DVDs / Blu-rays)

[themoviedb.org](https://www.themoviedb.org) — free API key.

**Adds:** film metadata and posters from UPC scans, movie title search.

**Setup:** TMDb account → Settings → API → request access → paste **either**
credential the API page shows: the 32-character **API Key (v3 auth)** or the
long **API Read Access Token (v4 auth)**. Shelf detects which one you pasted
and authenticates accordingly. Use **Test key** to confirm before saving — it
now probes TMDb exactly the way a real lookup does.

## ISBNdb (valuation)

[isbndb.com](https://isbndb.com) — paid.

**Adds:** list-price valuation per item and in bulk, the insurance report's
numbers, value-over-time stats. See
[Stats & valuation](stats-and-valuation.md).

## Google Books (optional API key)

[Google Books](https://books.google.com) — free, and used with no key at all
by default. The key is optional in a way the credentials above are not:
nothing stops working without it.

**Adds:** nothing new. It authenticates the Google Books requests Shelf
already makes, which raises the request quota and makes those requests
answer reliably.

**Why you might want one.** Anonymous Google Books requests are rate-limited
per source IP address, and that budget is shared with everyone else calling
from the same address — your ISP's NAT pool, a VPN exit, a cloud host. Shelf
paces its own outbound calls, but it cannot see the neighbours it is sharing
the quota with, so an anonymous request can come back rate-limited on a
perfectly idle Shelf. A key gives you a quota of your own.

You will notice this only where Google Books is actually reached, which is
less often than it sounds. It is the **last** book source tried on an ISBN
scan — behind the Deutsche Nationalbibliothek for 978-3 ISBNs, Open Library,
and Hardcover — so it answers for the books the others missed. It also backs
synopsis lookups and book cover search. If Open Library is answering your
scans, a key will change nothing you can see; if you regularly scan books
that come back thin, or you run bulk operations like the synopsis backfill or
a large Photo Intake, it is worth having.

**Setup:** the key comes from Google Cloud, not from a Google Books account.

1. Open the [Google Cloud console](https://console.cloud.google.com) and
   select a project, or create one — a personal project is fine and the Books
   API has no billing requirement.
2. Enable the **Books API** for that project
   ([direct link](https://console.cloud.google.com/apis/library/books.googleapis.com)).
3. Go to **APIs & Services → Credentials → Create credentials → API key** and
   copy the key it shows you.
4. Optional but recommended: **Edit API key → API restrictions → Restrict
   key → Books API**, so a leaked key can do nothing else.
5. Paste it into the Google Books card under Settings → Integrations and
   press **Test Key** before saving. A key with a stray character or a
   missing API restriction reports *"Google Books rejected the API key"*
   rather than failing quietly later.

Application restrictions (HTTP referrer, IP address) are a poor fit here —
Shelf calls the API from the server, not the browser, so a referrer
restriction will reject every request. Leave the key unrestricted by
application, or restrict by the IP address your server calls out from.

**How the key is handled.** It is stored encrypted, shown write-only (the
field renders blank once saved; leave it blank to keep the stored value), and
sent only in the `X-Goog-Api-Key` request header — never in a request URL,
which keeps it out of URLs that might be logged. Remove it at any time with
**Remove saved key**; Shelf falls straight back to anonymous requests.

## Vision providers (Photo Intake)

Anthropic, any OpenAI-compatible endpoint, or Ollama. See
[Photo Intake](photo-intake.md#setup).

## Notifications (ntfy / webhook)

Not an integration card — lives under Settings → Library → Lending — but the
same idea: an ntfy topic or JSON webhook URL for the overdue-loan digest. See
[Lending](lending.md#reminders).

## Always-on sources (no key)

Open Library, Google Books (anonymous by default), Amazon cover images, UPC Item DB, and
the Deutsche Nationalbibliothek for German ISBNs. Apart from credentials you
explicitly configure, lookups send only the ISBN or UPC — never your account,
collection or personal data. Requests to every provider are paced to its
published rate limit. UPC Item DB's free tier is
the tightest of them at six lookups a minute, so Shelf leaves ten seconds
between consecutive barcode lookups: scanning a stack of discs or games is
deliberately unhurried. One scan on its own never waits, and ISBNs are not
paced this way.

Some of these meter you per day rather than per second — UPC Item DB's free
tier allows 100 lookups a day, and keyless Google Books has a per-day project
quota. Once one is spent it rejects every request until it resets. Shelf does
not wait a daily limit out; it gives up at once, says on the scan card that a
source is rate-limiting you, and names the provider in the log — the card
names no provider, because a book lookup consults up to four and any subset
can be starved at once. See
[Troubleshooting](../troubleshooting.md#a-scan-comes-back-empty-and-the-log-says-a-provider-asked-for-a-long-wait).

## Supplying keys by environment instead

Every key except the vision providers can come from an environment variable
(`HARDCOVER_TOKEN`, `GOOGLE_BOOKS_API_KEY`, `ABS_URL`/`ABS_TOKEN`, `ISBNDB_API_KEY`, `TMDB_API_KEY`,
`IGDB_CLIENT_ID`/`IGDB_CLIENT_SECRET`), which overrides whatever is stored. The
secret field stays blank in Settings — Shelf never echoes a secret back — but
**Test key** still works against it, so you can confirm the key without pasting
a second copy in. See [Configuration](../configuration.md#credential-overrides).
