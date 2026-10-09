# Scanning

The **Scan** tab is where items enter Shelf and where most day-to-day actions
happen. One barcode field, one mode selector, and a strip of recent scans —
plus title search and **Add by hand** for the things a barcode cannot reach.

## Input methods

**Phone or tablet camera.** Tap the camera button. Shelf picks the decoder
for the device — ZXing on iOS Safari, html5-qrcode everywhere else — and
reads EAN-13, EAN-8, UPC-A and UPC-E. Requires HTTPS (the default, or a TLS reverse proxy in front) and a
one-time camera permission. Hold steady about 10–15 cm away; the viewfinder
beeps and fills the field on a read.

**USB or Bluetooth barcode scanner.** Any scanner that types the barcode and
sends Enter (the default for nearly all of them) works: click into the
barcode field once and scan away. No camera involved, no configuration.
Let each result appear before scanning the next item: a scan made while the
previous one is still being looked up is ignored.

**Keyboard.** Type an ISBN-10, ISBN-13 or UPC and press Enter.

## Scan modes

The mode is sticky — set it once and scan a pile. With Lending turned off in
Settings → **Features**, Scan offers no **Lend** or **Return** mode, and a
device last left in one of them opens in **Add**.

| Mode | What happens on each scan |
|---|---|
| **Add** | Look up metadata, download the cover, add the item as owned. Scanning a barcode you already own shows the existing item instead of duplicating it — whatever the media-type dropdown says. Scanning something on your wishlist marks it owned and takes it off the wishlist (*Now owned*). Scanning something you deleted brings it back from Trash as it was (*Restored from Trash*) rather than adding a second record. A dropdown pick the barcode contradicts is corrected rather than obeyed (see [Media types](#media-types)) |
| **Wishlist** | Same lookup, but the item is added to your wishlist, not as owned. A barcode already in your library is left as it is |
| **Lend** | Pick a borrower first; each scan checks that item out to them. Optional due date |
| **Return** | Each scan checks the item back in, whoever had it |
| **Move** | Pick a location first; each scan relocates the item there. To work along a shelf putting things away in order, use [Shelf Fill](shelf-fill.md) instead — it keeps the shelf selected and numbers each scan's position |
| **Inventory** | Pick a location; scan everything physically present; then **Check for missing** lists items Shelf thinks are there but you didn't scan. Counts *copies*, not records — see [Auditing a shelf with more than one copy](#auditing-a-shelf-with-more-than-one-copy) |
| **Lookup** | Read-only: tells you whether the item is in your library (and where, and whether it's lent out). Changes nothing |
| **Quick Rate** | Marks the item finished with today's date — Watched for a disc, Played for a game, Read otherwise |

The Scan tab is for editors and admins; viewers don't see it.

### Scanning an item that is in Trash

**Lend, Return, Move, Inventory, Lookup and Quick Rate never act on an item in
[Trash](items.md#trash).** The card says *In Trash since* the date it was
deleted, with an amber **in Trash** badge, and nothing else happens: no loan,
no move, no rating, no inventory mark. A **Restore** button on the card brings
the item back, and the card turns into *Restored from Trash*; scan it again to
lend, move or rate it. Both scans appear in Recent scans.

### Auditing a shelf with more than one copy

A book can exist as two physical objects — you bought a second, or you merged
two duplicate records that were filed in different rooms. Shelf tracks those
separately, and both Inventory and the audit count them:

- **The audit expects an item on a shelf if any of its copies is there**, not
  only the one Shelf considers primary. A copy in the Loft is expected in the
  Loft even though the record also has a copy in the Office.
- **Two copies of one book on the same shelf are one line with a count**
  ("(2 copies)"). A barcode cannot tell two identical books apart, so neither
  can the audit — scanning either one marks the book accounted for.
- **Scanning a multi-copy book at a shelf where none of its copies live
  reports rather than moves.** You get "Copies at Office and Loft; none here."
  and nothing is changed. The barcode says *which book*, never *which copy*, so
  guessing would silently move a copy out of the room it is actually in.

A book with only one copy still relocates on a scan, as it always has — there
is only one object the scan can mean. **Lookup** mode names every room a copy
is in, for the same reason.

## Default tags

To file a whole pile under one heading — a stack of cookbooks, a box of
signed copies — type it once in **Default tags** before you start. The field
sits below the barcode field in **Add** and **Wishlist** modes, and every item
those modes file from then on gets the tag.

- **Several tags:** separate them with `;` — `Cookbook; Signed`.
- **New names are fine.** A tag that does not exist yet is created, as a
  global tag, on the first successful add. There is no trip to Settings.
- **It is sticky per device**, like the mode and location: it is still there
  after a reload, until you clear it. A phone and a laptop keep their own.
  Renaming or deleting a tag in [Settings](settings.md#tags) does not change
  what a saved field holds — the next add creates the old name again, so
  retype the field after a rename.
- **Suggestions follow the media type.** The field suggests your own tags
  that suit the selected type, plus a few starter ideas for it (Cookbook,
  Signed, First edition… for books). A type's starters stop showing once you
  have a tag of your own scoped to that type. With **Auto**, everything is
  suggested.
- **Only an item filed now is tagged:** a new item, a wishlist add, or an
  item a scan brings back from Trash. A duplicate is never tagged — Shelf
  would be editing an item you did not just file. Nor is a wishlist item a
  scan marks as owned.
- It carries through everywhere an Add can finish: the [legacy-barcode
  follow-up](#legacy-price-point-book-barcodes), [title search](#title-search-no-barcode),
  and the [Add by hand](#add-by-hand) form — including the one a failed scan
  offers.

Tags are applied on the item page or its edit page, or in bulk from
[Browse](browse-and-search.md#bulk-editing), and an admin renames, scopes and
deletes them in [Settings](settings.md#tags).

## Default reading status

Started a stack, or cataloguing a shelf you have already read? Pick it once in
**Default status**, just below Default tags, and every item **Add** or
**Wishlist** files from then on takes that status — no second edit per item.

- **Choices:** None, or the three statuses. **The words follow the media
  type**, as on the item page: Want to Read / Reading / Read for books, Want
  to Watch / Watching / Watched for discs, Want to Play / Playing / Played
  for games. Under **Auto** it shows neutral words until the scan says what
  the item is.
- **Disabled where it would do nothing:** in every mode other than Add and
  Wishlist, and for a type with no reading status (music, magazines…). Under
  Auto a scan that turns out to be one of those files with no status.
- **It is sticky per device**, like the mode, location and default tags.
- **Only an item filed now takes it:** a new item, a wishlist add, or an
  item a scan brings back from Trash *that has no status of its own*. A
  restored item that already had one keeps it. A duplicate never changes,
  nor does a wishlist item a scan marks as owned.
- **No dates are recorded.** Setting Read here does not stamp a finish date
  or add a reading-log entry; set dates on the item page if you want them.
- It carries through the same places default tags do: the [legacy-barcode
  follow-up](#legacy-price-point-book-barcodes), [title search](#title-search-no-barcode),
  and the [Add by hand](#add-by-hand) form.

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

An item added from the results takes the [default tags](#default-tags) and
the [default reading status](#default-reading-status) set on the card.

## Add by hand

Nothing needs a barcode. **Add by hand** is a panel on the Scan page, below
title search, and a title is the only field it requires. Everything else is
optional and editable afterwards from the item page.

Open it with the **Add by hand** button, or go straight to `/scan?add=manual`.
It is available in **Add** and **Wishlist** modes; arriving by the link from
anywhere else switches you to Add mode so the panel is there when you land.

- **Media type** is yours to choose — every type Shelf supports, including
  the music formats and magazines. There is no "Auto" here: that means
  "detect it from the barcode", and there is no barcode to detect from.
- **ISBN or barcode** is optional. Type one and Shelf files it correctly on
  its own — an ISBN goes in the ISBN column, a UPC in the UPC column.
- **Wishlist mode** works here too: switch the Scan page to Wishlist and what
  you add by hand goes on the wishlist rather than the shelf.
- The **creator field** renames itself to match the type — Author(s) for
  books, Developer for games, Director for discs, Artist for music.
- The **platform** picker appears only for video games.
- The card's [default tags](#default-tags) and [default reading
  status](#default-reading-status) apply here too.

Five ways in, all landing on the same panel:

1. The **Add by hand** button on the Scan page itself.
2. **Add by hand** in Home's quick actions.
3. The button on an empty **title search** — it carries what you typed
   through as the title. A provider *failure* deliberately does not offer it:
   a rejected key is not a missing book, and hand-typing what a working key
   would have fetched is the wrong repair.
4. The button on the card you get when the scan box cannot read what you
   typed — which is what happens when you type a **title** into it.
5. **Add another like this** on any item's page, which prefills the author,
   publisher, year, type, platform, series and location from that item.

A scan that finds a valid barcode no provider knows still opens the same
form in place on its result card, as it always has.

What you type is checked before it's stored: an ISBN whose check digit
doesn't add up, a location that no longer exists, or a game platform that
isn't in your list each answer with an error card and nothing is added. An
ISBN-10 is fine — Shelf stores it alongside the ISBN-13 it implies.

A typed or scanned ISBN in **Add** mode is check-digit validated *before*
any lookup, so a mistyped digit costs nothing and says "Invalid ISBN"
straight away. The lookup modes (lend, return, move, inventory, lookup,
quick-rate) are not that strict on purpose: an old record whose stored ISBN
isn't valid is still found when you scan it.

From an existing item's page, **Add another like this** opens the Add by
hand panel with that item's author, publisher, year, media type, platform,
series and location already filled — handy for a second edition or the next
book in a series. It creates a **separate record**; it does not register
another physical copy of the same item.

The Scan tab is not the only place a camera scan happens. An item's **edit**
form has a **Scan ISBN** button in its Identifiers section, for fixing one
wrong ISBN without starting a scan session — see
[Items](items.md#editing). It fills the field and leaves the saving to you.

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

Shelf needs both parts of one of these barcodes. Scanning it **with** the
five-digit supplement follows the lookup path above. Scanning the bare UPC-A
**without** the supplement stops the scan instead of filing anything: the card
tells you it is an older book barcode and asks for the five digits printed
beside it. You **type** those digits — you do not have to rescan, which is what
makes this work on a scanner that drops the supplement in the first place. An
entry that is not exactly five digits is refused on the spot.

Your answer is remembered under the **full** 17-digit code, so scanning the bare
12 digits again asks for the digits again. That is deliberate: the bare code
alone does not say which book it is — on the order of 100,000 titles share it —
so remembering an answer under it would put that answer on the next Scholastic
book you scanned.

All of this works the same way in [Shelf Fill](shelf-fill.md): the card appears
without leaving the page, and the book you resolve takes its position on the
shelf you are filling. The one difference is the escape hatch. On this page the
card offers **Add it by hand**; Shelf Fill has no by-hand panel, so there it
offers what its other cards offer — scan the printed ISBN instead.

A barcode whose publisher prefix is **not** confirmed still falls through to the
ordinary UPC path, unchanged. If a scan will not resolve, scanning the printed
ISBN on the copyright page always works.

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

Books further divide into book, audiobook, eBook, comic / graphic
novel and Manga — the barcode cannot tell those apart, so they stay yours to
pick. Change the type on the item page or in bulk from Browse.

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
