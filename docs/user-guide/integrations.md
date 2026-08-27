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

A scan that comes back thin now tells you **on the card** whether the cause
was a missing credential, a rejected one, or a provider with no match — see
[Troubleshooting](../troubleshooting.md#a-scan-added-only-a-title). (On IGDB
a rejected credential is indistinguishable from a genuine miss, so that one
reads as "no match"; **Test key** is how to tell.)

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

## Vision providers (Photo Intake)

Anthropic, any OpenAI-compatible endpoint, or Ollama. See
[Photo Intake](photo-intake.md#setup).

## Notifications (ntfy / webhook)

Not an integration card — lives under Settings → Library → Lending — but the
same idea: an ntfy topic or JSON webhook URL for the overdue-loan digest. See
[Lending](lending.md#reminders).

## Always-on sources (no key)

Open Library, Google Books (keyless), Amazon cover images, UPC Item DB, and
the Deutsche Nationalbibliothek for German ISBNs. Lookups send only the ISBN
or UPC — never your account, collection or personal data. Requests to every
provider are paced to its published rate limit.

## Supplying keys by environment instead

Every key except the vision providers can come from an environment variable
(`HARDCOVER_TOKEN`, `ABS_URL`/`ABS_TOKEN`, `ISBNDB_API_KEY`, `TMDB_API_KEY`,
`IGDB_CLIENT_ID`/`IGDB_CLIENT_SECRET`), which overrides whatever is stored. The
secret field stays blank in Settings — Shelf never echoes a secret back — but
**Test key** still works against it, so you can confirm the key without pasting
a second copy in. See [Configuration](../configuration.md#credential-overrides).
