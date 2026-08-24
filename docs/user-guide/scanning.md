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
| **Add** | Look up metadata, download the cover, add the item as owned. Scanning an ISBN you already own shows the existing item instead of duplicating it |
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

Shelf tells books, DVDs / Blu-rays and video games apart by barcode: ISBNs
(978/979) are books; other UPCs are looked up via UPC Item DB and TMDb / IGDB.
Books further divide into book, kids book, audiobook, eBook, comic / graphic
novel — change the type on the item page or in bulk from Browse. CDs are
manual-add.

A UPC scan brings back a synopsis, a year and cover art when TMDb (discs) or
IGDB (games) is configured. Barcode databases store retail shelf titles rather
than film or game titles — `Goodfellas [DVD]  Feature Thriller Drama …` — so
Shelf strips format tags, platform suffixes and edition wording, and if that
still finds nothing it retries with progressively shorter versions of the
title. It stops short of searching a single short word, because a one-word
search comes back with a *different* film rather than nothing. When no provider
matches, the item is still added under its own title — use **Retry ISBN** or
**Search by Title** on the item page to fill it in.

## Tips

- A barcode that looks up wrong? Open the item, hit **Edit**, fix it, and use
  **Find cover** to pick a better image.
- Scanning in the store with no signal? Use [Store Mode](wishlist-and-store-mode.md).
