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

Google Books remains available anonymously. An optional API key can be saved
in Settings to authenticate ISBN, synopsis, and cover searches. Shelf sends it
only in the `X-Goog-Api-Key` request header; it is stored encrypted and never
placed in request URLs.

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
