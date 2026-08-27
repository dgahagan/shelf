# Scanning

The **Scan** tab is where items enter Shelf and where most day-to-day actions
happen. One barcode field, one mode selector, and a strip of recent scans.

## Input methods

**Phone or tablet camera.** Tap the camera button. Shelf picks the decoder
for the device — ZXing on iOS Safari, html5-qrcode everywhere else — and
reads EAN-13, EAN-8, UPC-A and UPC-E. Requires HTTPS (you have it) and a
one-time camera permission. Hold steady about 10–15 cm away; the viewfinder
beeps and fills the field on a read.

**USB or Bluetooth barcode scanner.** Any scanner that types the barcode and
sends Enter (the default for nearly all of them) works: click into the
barcode field once and scan away. No camera involved, no configuration.

**Keyboard.** Type an ISBN-10, ISBN-13 or UPC and press Enter.

## Scan modes

The mode is sticky — set it once and scan a pile.

| Mode | What happens on each scan |
|---|---|
| **Add** | Look up metadata, download the cover, add the item as owned. Scanning a barcode you already own shows the existing item instead of duplicating it — whatever the media-type dropdown says. A dropdown pick the barcode contradicts is corrected rather than obeyed (see [Media types](#media-types)) |
| **Wishlist** | Same lookup, but the item is added as *not owned* — your wish list |
| **Lend** | Pick a borrower first; each scan checks that item out to them. Optional due date |
| **Return** | Each scan checks the item back in, whoever had it |
| **Move** | Pick a location first; each scan relocates the item there |
| **Inventory** | Pick a location; scan everything physically present; then **Check for missing** lists items Shelf thinks are there but you didn't scan |
| **Lookup** | Read-only: tells you whether the item is in your library (and where, and whether it's lent out). Changes nothing |
| **Quick Rate** | Marks the item as read / finished with today's date |

The Scan tab is for editors and admins; viewers don't see it.

## Title search (no barcode)

Below the barcode field, **Title search** covers the things barcodes miss —
pre-ISBN books, retro game cartridges, discs with a scuffed UPC:

- **Books** — Open Library search; pick an edition from the results and add
  it directly. The preferred language set in Settings → Library → Collection
  ranks matching editions first.
- **Movies** — TMDb title search (needs a TMDb key).
- **Video games** — IGDB title search (needs IGDB credentials); filter by
  platform for "Super Mario Bros." ambiguity.

## Manual add

**Add manually** opens a blank item form for anything lookup can't find: a
self-published book, a burned CD, a box set. Fill what you know; you can
attach a cover by upload or cover search afterwards from the item page.

From an existing item's page, **Add a copy** pre-fills a new form from it —
handy for a second edition or a duplicate copy you want as its own record.

## What happens after a scan

Each scan lands in **Recent scans** with its cover, title and what was done
("Added", "Lent to Sam", "Moved to Office"). Click through to the item page
to fix anything. Cover art that wasn't immediately available is fetched in
the background and appears on its own; a **Retry cover** button on the item
page re-runs the chain on demand.

Lookups are paced per provider to stay inside each one's published rate
limit and retried on transient failures, so a 200-book scanning session
doesn't get you throttled.

## Media types

**The barcode decides when it can; the dropdown is a hint, not an order.**

A 978/979 prefix is an ISBN, and that is certain — so a book scanned while the
dropdown still says "DVD" is filed as a book anyway, and the card tells you it
overrode you. The reverse holds too: a non-ISBN barcode is certainly not a
book, so a disc scanned under "Book" is not filed as one.

For a UPC there is no certain prefix, so Shelf reads the product record it
already fetched — the platform or format wording in the retail title
(`Nintendo Switch`, `[DVD]`, `4K UHD`) first, then the product category, and
only ever for games. A category is never enough on its own to call something a
disc, and a category naming a *console* is never enough to call something a
game — that is the shelf the product sits on, not what the product is.

**Auto** is the default for a new install: it means "read the barcode and
decide", and it is the one to leave it on. If you have used Shelf before,
your saved choice is left alone — nothing is silently reinterpreted — and the
barcode rule above corrects a stale one anyway.

When nothing in the barcode or the product record disagrees with you, your
choice stands. That matters for CDs in particular: Shelf has no CD detection,
so the dropdown is the only thing that can say "this is a CD".

Books further divide into book, kids book, audiobook, eBook, comic / graphic
novel — the barcode cannot tell those apart, so they stay yours to pick.
Change the type on the item page or in bulk from Browse.

Whatever it decides, the card says so: *"Title names the Nintendo Switch
platform — filed as Video Game."* or *"ISBN barcodes are books — overriding
the 'DVD / Blu-ray' hint to Book."* If it could not tell, it says that too
rather than claiming a detection it did not make.

A UPC scan brings back a synopsis, a year and cover art when TMDb (discs) or
IGDB (games) is configured. Barcode databases store retail shelf titles rather
than film or game titles — `Goodfellas [DVD]  Feature Thriller Drama …` — so
Shelf strips format tags, platform suffixes and edition wording, and if that
still finds nothing it retries with progressively shorter versions of the
title. It stops short of searching a single short word, because a one-word
search comes back with a *different* film rather than nothing. When no provider
matches, the item is still added under its own title — use **Retry cover** or
**Find cover** on the item page to fill it in.

**And the card says why it was thin**, because the five reasons need five
different responses:

- **no key configured** — add one in Settings → Integrations.
- **the key was rejected** — fix it. The provider answered, and said no.
- **a provider is rate-limiting us** — wait and re-scan. This may not be a
  genuine miss, so it is worth trying again before adding anything by hand.
- **Shelf has no metadata source for this format yet** — nothing to fix. CDs
  are the case today: there is no music provider wired up, so a scanned CD is
  filed under its barcode title and no lookup is attempted at all.
- **the provider had no match** — nothing to fix either. It was asked, and it
  genuinely does not have this edition.

The card never names *which* provider is rate-limiting, because a book lookup
consults up to four and any subset of them can be starved at once. Naming one
would be a guess. See
[Troubleshooting](../troubleshooting.md#a-scan-added-only-a-title).

A **Not found** card can carry the rate-limit line too. That one matters: it
means the barcode may well be catalogued, and typing the book in by hand is
probably wasted effort. Try again later first.

## Tips

- A barcode that looks up wrong? Open the item, hit **Edit**, fix it, and use
  **Find cover** to pick a better image.
- Scanning in the store with no signal? Use [Store Mode](wishlist-and-store-mode.md).
