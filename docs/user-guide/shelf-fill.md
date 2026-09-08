# Shelf Fill

Shelf Fill is a rapid filing workflow for physical media. Open **Shelf Fill**, choose the precise room/bookcase/shelf once, then scan items while you place them there.

## The workflow

Pick the shelf, then scan. Each scan takes **the next position on that shelf** — the first item is 1, the next 2, and so on, in the order you scan them. The position belongs to the shelf, so when you finish one and pick another, the new shelf starts again at 1:

```
Study / Bookcase 1 / Shelf 1     Study / Bookcase 2 / Shelf 2
  #1  Dune                        #1  The Hobbit
  #2  Dune Messiah                #2  The Silmarillion
  #3  Children of Dune
```

The card for each scanned item shows its position, and the panel under the location picker says how many items are already on the shelf and which position the next scan will take. Both update as you go.

Nothing is locked in: any shelf can be reordered later on its **Arrange** page, by dragging or by sorting on title, creator, series, release or issue. There is a link to it beside the shelf summary.

## What each scan does

- Existing catalogue items are moved immediately without repeating metadata lookups.
- A copy-specific barcode moves that exact copy; it does not silently move the primary copy. This is how two copies of the same book end up on different shelves.
- A wishlisted item becomes owned when it is physically shelved.
- Unknown item barcodes reuse Shelf's normal Add scanner and metadata providers, then land on the shelf.
- Hierarchical locations are stored directly on the first-class physical-copy record; the catalogue item's location remains the compatibility projection for its primary copy.
- The selected location, media type and game platform stay on the device between scans.

## Shelf Fill or the Scan tab?

Both can put an item in a location, and the difference is what you are doing:

| | **Scan → Move** | **Shelf Fill** |
|---|---|---|
| Best for | relocating a few items | working along a shelf, filling it |
| Target location | chosen per scan | chosen once, stays selected |
| Position on the shelf | none assigned | next position, in scan order |
| A barcode you have never catalogued | reports it is not in your library | adds it, then shelves it |
| A specific copy's own barcode | moves the item's primary copy | moves that exact copy |

If you are standing at a bookcase putting books away, Shelf Fill is the one. If you are picking a single book off a table and want it filed, either works.

## Items that arrive without a position

An item put on a shelf before ordering existed, or moved there from the Scan tab, has no position. Those sort after the ordered ones on the Arrange page, and the shelf summary counts them separately so a partly-ordered shelf is not described as fully ordered. Give them positions by dragging them on Arrange, or by using one of its automatic sorts.
