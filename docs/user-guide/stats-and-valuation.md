# Stats & valuation

## Stats

The **Stats** tab renders server-side SVG charts (no JavaScript, print-
friendly):

- **Books read per year** — finished items by year, from reading-status dates
- **Collection growth** — items added over time
- **Top authors**
- **Collection value over time** — the total from valuation runs, once you've done one
- **By media type** and **by location** breakdowns
- **Recently added** (last 30 days)

Reading dates come from the item's reading status; imports from Goodreads /
StoryGraph / Hardcover bring their dates with them, so the history chart
fills in retroactively.

## Valuation (ISBNdb)

Settings → Integrations → **Collection Valuation** takes an
[ISBNdb](https://isbndb.com) API key (paid; the basic tier suffices). Then:

- **Valuate all** walks every item with an ISBN and records the list price.
  It respects ISBNdb's pacing, so a large library takes a few minutes; a
  live progress stream shows where it is.
- Per item, the page gets a **Valuate** button and shows the price and when
  it was fetched.
- **Manual value** on any item overrides the estimate — for signed copies,
  items without ISBNs, games and discs.

What you get is **list price**, not used-market price. It is the right number
for insurance replacement cost and the wrong number for "what could I sell
this for".

## The insurance report

**Stats → Valuation report** (or `/valuation/report`) is a print-ready page
grouping every item by location with per-location subtotals and a grand
total — what exists, where it is, what it would cost to replace. Items
without a value are listed too (a documentation report needs them),
flagged **Missing Value**. Print to PDF from the browser and file it with your
policy.

## Display currency

Settings → Library → **Collection** → Currency. Twenty currencies; changes
every value surface (item page, Browse, Stats, report). This is formatting
only — Shelf never converts. ISBNdb returns USD list prices; if you set EUR,
the same digits show with a € sign. Use **manual value** for items you've
priced in your own currency.
