# Stats & valuation

## Stats

The **Stats** page (under **Explore** in the nav) renders server-side SVG charts (no JavaScript, print-
friendly), and its **Wrap-up** link makes a shareable image of a month or a
year (see [Wrap-ups](#wrap-ups)). The page shows:

- **Finished in <year>** — this year's finished items, split into read ·
  watched · played; a part with nothing in it is left out
- **Books read per year** — finished books by year, from status dates. Only
  books, audiobooks, ebooks, comics and manga count here; a finished film,
  game, album or magazine does not
- **Collection growth** — items added over time
- **Top authors**
- **Collection value over time** — the total from valuation runs, once you've done one
- **By media type** and **by location** breakdowns
- **Recently added** (last 30 days)

Reading dates come from the item's status; imports from Goodreads /
StoryGraph / Hardcover bring their dates with them, so the history chart
fills in retroactively.

### Wrap-ups

A wrap-up is a shareable image of one month or one year of your library.
Open it from the **Wrap-up** link at the top of the Stats page. It starts on
the most recent month in which anything was finished or added; the **Period**
menu picks any other month, or a whole year.

The image shows:

- the period, and how many items were finished in it, split into read ·
  watched · played
- how many items were added to the library
- the top author of the period, when one author appears more than once
- a grid of covers — the items finished in the period, or, when nothing was
  finished, the items added. The grid holds at most 24 covers, newest first;
  the numbers above it always count the whole period. An item without a cover
  gets a tile with its title. When most covers are square (audiobooks,
  music), the tiles are square too.

**Download image** saves it as a PNG. Where your device can share files (most
phones), a **Share** button sends it straight to another app.

In January, Stats also links to last year's wrap-up — in January 2026, **2025 in
Books**.

The image is drawn in your browser from the page. Shelf does not store it and
does not publish it anywhere — you decide where it goes. A wrap-up covers the
whole library, not one user. It is part of Stats: when Stats is turned off in
Settings → **Features**, wrap-ups are off too.

## Valuation (ISBNdb)

Valuation can be turned off in Settings → **Features**. While it is off,
estimated values leave the item page, the Stats page loses its **Est. Value**
tile and value chart, and Settings loses the valuate button and the report
link. A value you typed yourself (a manual value) still shows.

Settings → Integrations → **Collection Valuation** takes an
[ISBNdb](https://isbndb.com) API key (paid; the basic tier suffices). The
same key powers [price alerts](wishlist-and-store-mode.md#price-alerts) for
the wishlist. Then:

- **Valuate all** walks every owned item with an ISBN and records the list price.
  It respects ISBNdb's pacing, so a large library takes a few minutes; a
  live progress stream shows where it is.
- Per item, the page gets a **Valuate** button and shows the price and when
  it was fetched.
- **Manual value** on any item overrides the estimate — for signed copies,
  items without ISBNs, games and discs.

The valuation counts **what you own**: the sweep only prices owned items,
and the collection total, the Stats page's **Est. Value** and the insurance
report leave out wishlist items and items you track without owning. If your
wishlist items had values, totals and reports taken after upgrading are
lower than earlier ones by that amount; the value-over-time chart keeps the
earlier points as they were recorded. **Valuate** on a single item still
works whether you own it or not.

What you get is **list price**, not used-market price. It is the right number
for insurance replacement cost and the wrong number for "what could I sell
this for".

## The insurance report

**Stats → Valuation report** (or `/valuation/report`) is a print-ready page
grouping every owned item by location with per-location subtotals and a grand
total — what exists, where it is, what it would cost to replace. Items
without a value are listed too (a documentation report needs them),
flagged **Missing Value**. The **Print** button on the page opens the browser's
print dialog; save it as a PDF and file it with your policy.

## Display currency

Settings → Library → **Collection** → Currency. Twenty currencies; changes
every value surface (item page, Browse, Stats, report). This is formatting
only — Shelf never converts. ISBNdb returns USD list prices; if you set EUR,
the same digits show with a € sign. Use **manual value** for items you've
priced in your own currency.
