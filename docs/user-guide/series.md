# Series

The **Series** page groups your library by series name, sorted by size, with
each volume's position and a cover strip. It is the fastest way to answer
"which Discworld am I missing?"

Series can be turned off in Settings → **Features**. While it is off, an item
page still shows its series name and position as plain text, but the name is
no longer a link and the "you own N of …" read-out goes. Home's **Series**
shortcut goes too.

## What it shows

- **Series name**, count, and the covers you own in position order.
- **Gaps** — positions with no item are flagged when the numbering has holes
  (you have #1, #2 and #4).
- **Complete** — mark a series finished so it stops nagging about gaps; the
  badge shows on the card.
- **Synopsis** — write one inline, or **Fetch from Hardcover**.
- **Unassigned** — a block at the bottom for items with no series at all
  (with the true count; these are not a series, just the remainder).

## With Hardcover connected

Hardcover knows how many books a series has. **Check completeness** compares your
shelf against Hardcover's list: volumes you don't own appear with covers and
an **Add to wishlist** button — one click per missing volume, or all of
them. This is the feature that turns a wishlist into a completion list.
When [buy links](wishlist-and-store-mode.md#buy-links) are set up, each
missing volume also carries Bookshop.org and Amazon search links that use
your own affiliate tags.

## Watching for new volumes

With Hardcover connected and **Release radar** on (Settings → Features), Shelf
can tell you when a new book in a series is announced.

- **Watch for new volumes** — a button on each series card (editors). It reads
  **Watching** once the series is watched; press it again to stop. Viewers see
  a **Watching** badge instead.
- **Watch all complete series** — a button in the Series page header that
  watches every series whose last **Check completeness** found nothing
  missing. A series that has never been checked is not included, so run the
  check first. Nothing is watched automatically.
- **Not found on Hardcover** — shown under a watched series' name when
  Hardcover has no series by that name. Rename the series to Hardcover's
  spelling to fix it.
- **Upcoming releases** — a link in the header to a page listing every
  announced volume in the series you watch, by date, with undated ones last
  (*announced — date TBA*). Editors can **+ Wishlist** a volume, **Dismiss**
  it, or **Stop watching** its series. A volume you own or have on the
  wishlist is not listed.
- **The daily digest** — once a day Shelf asks Hardcover about your watched
  series and sends one notification listing newly announced volumes and any
  coming out within the week. It uses the notification URL on Settings →
  Library → Lending; with no URL set, the page still fills in. The number of
  series checked per day is set in Settings → Integrations → Hardcover →
  **Release radar**; with more watches than that, they are checked in turn
  over several days.

Announcements come from Hardcover. If none is found for a series, that does
not mean nothing is coming — Hardcover may not list the next volume yet.

## Fixing series data

Metadata sources disagree about names ("The Expanse" vs "Expanse") and
leave you with two cards for one series. On the card:

- **Rename** — renaming onto an existing name **merges** the two.
- **Disband** — clears the series from every item in it (items stay).
- Per-item position and name are edited on the item page or in bulk from
  Browse (**Set series**).

## Tips

- Series are free text — there is no master list, so spelling is the key.
  The rename autocomplete helps you land on the existing name.
- Comics and manga work fine; numbering is the volume.
- A series with one item is still a series — useful as a placeholder once
  you've added the first volume and wishlisted the rest.
