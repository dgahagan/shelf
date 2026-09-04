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

An empty result box tells you *why* it is empty, in the same words the scan
card uses: a rejected key, a provider that is rate-limiting us, or a provider
Shelf could not reach at all. "No books found for …" now means only what it
says — the provider answered and genuinely had nothing.

## Manual add

**Add manually** opens a blank item form for anything lookup can't find: a
self-published book, a burned CD, a box set. Fill what you know; you can
attach a cover by upload or cover search afterwards from the item page.

What you type is checked before it's stored: an ISBN whose check digit
doesn't add up, a location that no longer exists, or a game platform that
isn't in your list each answer with an error card and nothing is added. An
ISBN-10 is fine — Shelf stores it alongside the ISBN-13 it implies.

A typed or scanned ISBN in **Add** mode is check-digit validated *before*
any lookup, so a mistyped digit costs nothing and says "Invalid ISBN"
straight away. The lookup modes (lend, return, move, inventory, lookup,
quick-rate) are not that strict on purpose: an old record whose stored ISBN
isn't valid is still found when you scan it.

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
overrode you. The reverse mostly holds too: a non-ISBN barcode is almost
certainly not a book, so a disc scanned under "Book" is not filed as one. The
one documented exception is the legacy price-point barcode below.

### Legacy price-point book barcodes

Before Bookland EAN, some books — Scholastic paperbacks in particular — carried
an ordinary UPC-A plus a five-digit supplement. The UPC identifies a publisher
and a price band shared by many titles; only the supplement says *which* book.
Treating that UPC as a normal product barcode would file the wrong book.

When Shelf recognizes one of these, it works out the small set of ISBNs the
supplement could mean and looks each one up:

- **One match** — the book is filed, exactly as an ISBN scan would.
- **Two matches** — Shelf stops and shows both so you can pick the book in your
  hand. It will not guess. Your choice is remembered, so the next copy of that
  barcode files itself.
- **A provider was unreachable** — Shelf says it could not verify the scan
  rather than reporting "not found", because an unanswered lookup is not the
  same as a book that does not exist.

Only barcodes whose publisher prefix has been confirmed are treated this way;
anything else falls through to the ordinary UPC path. If a scan will not
resolve, scanning the printed ISBN on the copyright page always works.

For a UPC there is no certain prefix, so Shelf reads the product record it
already fetched — the platform, format, medium or audio wording in the retail
title (`Nintendo Switch`, `PC CD`, `[DVD]`, `4K UHD`, `CD-ROM`, `Audio CD`)
first, then the product category, which may name a game (`Video Game Software`) or a
music CD (`Music CDs`) and nothing else. A category is never enough on its own
to call something a disc, and a category naming a *console* is never enough to
call something a game — that is the shelf the product sits on, not what the
product is.

The four title arms are checked in order, and the order matters. A game whose
own subtitle carries a format word (`Alice Madness Returns (PC DVD)`) is a
game, not a disc. A disc bundle whose own title carries `CD` or `CD-ROM`
(`Purple Rain [DVD/CD Combo]`, `Terminator 2 [DVD] (includes bonus CD-ROM)`)
is a disc, not an album and not a game. Each arm runs after the one that
could be wrong about it.

**Auto** is the default for a new install: it means "read the barcode and
decide", and it is the one to leave it on. If you have used Shelf before,
your saved choice is left alone — nothing is silently reinterpreted — and the
barcode rule above corrects a stale one anyway.

When nothing in the barcode or the product record disagrees with you, your
choice stands. That still matters for CDs: a music CD is detected on Auto when
the retail title carries an audio tag (`… - CD`, `Audio CD`) or the category
names music CDs, but when the record names **neither**, the dropdown is what
says it — and the choice stands.

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
search comes back with a *different* film rather than nothing.

For one class of scan it does not search at all. When the retail title names
console hardware — a console, controller or headset **together with** a
platform name *or* a known peripheral brand (Logitech, SteelSeries, Corsair,
Sony, …), as in `PlayStation 5 Console`, `Nintendo Switch Pro Controller` or
`Logitech G Pro X Gaming Headset` — Shelf files the item under that title and
asks no provider anything. The shortened title would be `PlayStation`, and a film database
answers that with a confident match for an unrelated film, so the choice is
between a thin record and a wrong one. Shelf declines the search rather than
the honesty. Both halves have to match: `Console Wars` and `Air Traffic
Controller` are films, and they still get the full ladder.

A format or disc word on the same listing does not change the answer.
`PlayStation 5 Wireless Headset DVD`, `… CD` and `… CD-ROM` are all still the
headset — filed under its own title, with nobody asked. Retail listings
carry that wording for reasons of their own, and it says nothing about what the
object is, so the hardware reading comes **before** the format, medium and
audio wording is read rather than after it.

The recognition needs both halves: a hardware word, and either a platform
name or a brand from a short list of peripheral makers. A brand on its own is
not enough — `Astro Boy` and `Turtle Beach` are films — and neither is a
hardware word on its own. The brand half is there because a peripheral usually
names its maker rather than its platform, and a long brand name on its own can
be a search that matches someone else's film, so the brand is read instead.

The remaining gap is a hardware listing whose brand is **not** on that list. It
is filed as a disc under its own title, with the notice that nothing in the
barcode said what it was, and it is still searched — no worse than before.
Correct the type on the item page if that is wrong.

When no provider matches, the item is still added under its own title — use
**Retry cover** or **Find cover** on the item page, or edit the title and type
directly in the item editor, to fill it in.

**And the card says why it was thin**, because the six reasons need six
different responses:

- **no lookup was attempted** — nothing to fix. The title named console
  hardware, so Shelf declined to guess rather than filing someone else's film.
  Correct the type or the title on the item page if it read the title wrong.
- **no key configured** — add one in Settings → Integrations.
- **the key was rejected** — fix it. The provider answered, and said no.
- **a provider is rate-limiting us** — wait and re-scan. This may not be a
  genuine miss, so it is worth trying again before adding anything by hand.
- **Shelf has no metadata source for this format yet** — nothing to fix. CDs
  are the case today: there is no music provider wired up, so a scanned CD is
  filed under its barcode title. That now happens on **Auto** — the CD is
  detected rather than needing the dropdown — and it is still filed silently,
  with no film database asked. This one is decided by the *format*; the
  hardware case above is decided by the *title*.
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
